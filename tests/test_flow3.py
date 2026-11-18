"""Flows 3 and 4 (plan P-41 to P-53): people adjudicate, code assigns, compares, derives
the outcome, exports it and closes the case. AI is off; nothing here calls an API."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pymupdf
import pytest

from adjud.documents import upload
from adjud.flow2 import Flow2Refused
from adjud.flow3 import Flow3Refused, assigned_cases, current_round
from adjud.flow4 import Flow4Refused, outcomes
from adjud.integrity import rebuild_all, verify_all
from adjud.review import submit
from adjud.runner import Runner
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ROOT = Path(__file__).resolve().parents[1]
SITE, SS, COORD = "site101.coord", "ss.anna", "cec.sofia"
CHAIR, ADJS = "adj.okafor", ("adj.okafor", "adj.lindqvist", "adj.reyes")


def pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text, fontsize=11)
        return doc.tobytes()


def answers(**kw) -> dict:
    base = {"event_confirmed": "yes", "mi_type": "type_1", "event_date": "2026-03-14",
            "related_to_study_drug": "not_related",
            "rationale": "Troponin rise and ST elevation."}  # fmt: skip
    return {**base, **kw}


def to_qc_review(runner, store, cid) -> str:
    """De-identification, the site's signature and the QC round: return the narrative the
    Safety Specialist would sign."""
    runner.run_case_steps()  # de-identification
    submit(store, cid, SITE, "demo", store.paths.config)
    runner.run_case_steps()  # Flow 2 handoff, QC start
    runner.run_case_steps()  # QC round, narrative draft
    assert store.state(cid).state == State.QC_REVIEW
    latest = max(store.case_dir(cid).glob("qc/checks_v*.json"), key=lambda p: p.stat().st_mtime)
    draft = store.get_file(cid, json.loads(latest.read_text())["narrative"]["draft"]["path"])
    return re.sub(r"\[Add[^\]]*\]", "Chest pain treated with a stent.", draft.decode())


def through_qc(runner, store, cid) -> None:
    """De-identification, the site's signature and the Quality Check, up to READY and the
    first adjudication step (the runner assigns in the same pass as the Flow 3 handoff)."""
    text = to_qc_review(runner, store, cid)
    runner.flow2.sign_qc_pass(cid, SS, "demo", text, "Documents present")
    runner.run_case_steps()  # Flow 3 handoff
    runner.run_case_steps()  # the reviews are assigned


@pytest.fixture
def uploaded(tmp_path, clock):
    from adjud.paths import DataPaths
    from tools.reset_demo import reset

    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths, clock=clock)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    upload(store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                        ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE)  # fmt: skip
    return runner, store, cid


@pytest.fixture
def assigned(uploaded):
    runner, store, cid = uploaded
    through_qc(runner, store, cid)
    assert store.state(cid).state == State.ADJ_REVIEW
    return runner, store, cid


def state(store, cid) -> State:
    return store.state(cid).state


def templates(store) -> list[tuple[str, str]]:
    notes = [json.loads(p.read_text()) for p in store.paths.outbox.glob("*.json")]
    return [(n["template"], n["to"]) for n in notes]


def holders(store, cid) -> tuple[str, str]:
    rnd = current_round(store, cid)
    return rnd.holders["1"], rnd.holders["2"]


def finish(runner, times: int = 3) -> None:
    for _ in range(times):  # outcome, export, acknowledgement: one runner step each
        runner.run_case_steps()


def test_agreement_goes_straight_to_outcome_export_and_close(assigned):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    assert a1 != a2 and {a1, a2} <= set(ADJS)
    assert cid in assigned_cases(store, a1) and cid in assigned_cases(store, a2)
    third = next(a for a in ADJS if a not in (a1, a2))
    assert cid not in assigned_cases(store, third)  # ACC-02: only assigned cases
    assert ("adjudication_assigned", f"user:{a1}") in templates(store)
    note = next(json.loads(p.read_text()) for p in store.paths.outbox.glob("*.json")
                if f"user:{a1}" in p.read_text())  # fmt: skip
    assert note["link"] == f"/adjudicate?role=adjudicator&user={a1}&case={cid}"
    form = store.get_json(cid, current_round(store, cid).form["path"])
    assert form["form_id"] == "MI" and form["version"] == 1  # fixed for the round (ADJ-01)
    assert [v for v, _ in form["fields"][0]["options"]] == ["yes", "no", "insufficient_data"]

    f3.save_draft(cid, a1, {"event_confirmed": "yes"})
    assert f3.draft(cid, a1) == {"event_confirmed": "yes"} and f3.draft(cid, a2) == {}
    with pytest.raises(Flow3Refused, match="MI type"):
        f3.sign_review(cid, a1, "demo", {"event_confirmed": "yes", "related_to_study_drug": "x"})
    with pytest.raises(SignatureRefused):
        f3.sign_review(cid, a1, "wrong", answers())
    with pytest.raises(Flow3Refused, match="no open review"):
        f3.sign_review(cid, third, "demo", answers())
    f3.sign_review(cid, a1, "demo", answers())
    with pytest.raises(Flow3Refused, match="already signed"):
        f3.sign_review(cid, a1, "demo", answers())
    runner.run_case_steps()
    assert state(store, cid) == State.ADJ_REVIEW  # waits for the second review
    f3.sign_review(cid, a2, "demo", answers(event_date="2026-03-15", rationale="Agree."))
    runner.run_case_steps()  # compare: the date is within the 1-day tolerance
    assert state(store, cid) == State.OUTCOME
    finish(runner)
    assert state(store, cid) == State.CLOSED

    [out] = outcomes(store, cid)
    outcome = store.get_json(cid, out["outcome"]["path"])
    assert outcome["derivation"] == "outcome-v1" and not outcome["related"]
    assert outcome["values"]["event_date"]["value"] == "2026-03-14"  # Adjudicator 1's
    src = outcome["values"]["mi_type"]["sources"]
    assert [s["role"] for s in src] == ["Adjudicator 1", "Adjudicator 2"]
    assert all(s["event_seq"] and s["answers"]["sha256"] for s in src)  # OUT-01
    assert outcome["notes"]["rationale"]["Adjudicator 2"] == "Agree."
    exported = store.paths.edc_exports / f"{cid}_round1.csv"
    rows = list(csv.DictReader(exported.open()))
    assert {r["field"] for r in rows} == set(outcome["values"])
    assert all(r["correction"] == "N" and r["decided_by"] == "agreed" for r in rows)
    ack = json.loads((store.paths.edc_mock / "acks" / f"{cid}_round1.json").read_text())
    assert ack["status"] == "accepted"
    assert ("adjudication_complete", "role:cec_coordinator") in templates(store)
    assert not any(t == "outcome_for_pv" for t, _ in templates(store))
    assert all(r.ok for r in verify_all(store.paths))
    assert not any(r.changed for r in rebuild_all(store.paths, write=False))


def test_related_outcome_goes_to_pharmacovigilance(assigned):
    runner, store, cid = assigned
    for a in holders(store, cid):
        runner.flow3.sign_review(cid, a, "demo", answers(related_to_study_drug="related"))
    runner.run_case_steps()
    finish(runner)
    assert outcomes(store, cid)[0]["related"]
    assert ("outcome_for_pv", "mailbox:pharmacovigilance") in templates(store)
    note = next(json.loads(p.read_text()) for p in store.paths.outbox.glob("*outcome_for_pv*"))
    assert note["link"] is None  # a mailbox outside the app: no link, no clinical content


def test_adjudicator_3_resolves_every_disputed_field(assigned):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers(mi_type="type_1"))
    f3.sign_review(cid, a2, "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    assert state(store, cid) == State.ADJ3_REVIEW
    rnd = current_round(store, cid)
    a3 = rnd.holders["3"]
    assert rnd.disputed == ["mi_type"] and a3 not in (a1, a2)
    assert f3.fields_for(cid, "3") == ["mi_type"]  # the disputed field only (ADJ-08)
    assert ("adj3_assigned", f"user:{a3}") in templates(store)
    f3.sign_review(cid, a3, "demo", {"mi_type": "type_2"})
    runner.run_case_steps()
    assert state(store, cid) == State.OUTCOME
    finish(runner)
    outcome = store.get_json(cid, outcomes(store, cid)[0]["outcome"]["path"])
    mi = outcome["values"]["mi_type"]
    assert mi["value"] == "type_2" and mi["how"] == "adjudicator_3"
    assert [s["role"] for s in mi["sources"]] == ["Adjudicator 3", "Adjudicator 2"]


def shown(store, cid: str) -> dict:
    """The meeting record the Chair has on screen: the latest one recorded."""
    return current_round(store, cid).meeting["data"]["meeting"]


def test_unresolved_fields_go_to_the_committee_and_the_chair_signs(assigned):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers(mi_type="type_1", related_to_study_drug="related"))
    f3.sign_review(cid, a2, "demo", answers(mi_type="type_2", related_to_study_drug="not_related"))
    runner.run_case_steps()
    a3 = current_round(store, cid).holders["3"]
    f3.sign_review(cid, a3, "demo",
                   {"mi_type": "type_1", "related_to_study_drug": "unable_to_assess"})  # fmt: skip
    runner.run_case_steps()
    assert state(store, cid) == State.CEC_MEETING
    assert f3.committee_fields(cid) == ["related_to_study_drug"]
    assert ("committee_needed", "role:cec_coordinator") in templates(store)
    assert cid in assigned_cases(store, CHAIR, chair=True)  # the Chair sees committee cases

    with pytest.raises(Flow3Refused, match="no meeting"):
        f3.sign_consensus(cid, CHAIR, "demo", shown=None)
    f3.record_meeting(cid, COORD, held_on="2026-11-18", attendees=[a1, a2], summary="Short.",
                      decisions={"related_to_study_drug": "not_related"})  # fmt: skip
    with pytest.raises(Flow3Refused, match="quorum"):
        f3.sign_consensus(cid, CHAIR, "demo", shown=shown(store, cid))
    f3.record_meeting(cid, COORD, held_on="2026-11-18", attendees=list(ADJS),
                      summary="The committee reviewed the dossier.",
                      decisions={"related_to_study_drug": "not_related"})  # fmt: skip
    assert ("consensus_to_sign", f"user:{CHAIR}") in templates(store)
    seen = shown(store, cid)  # B07: a newer record replaces the one on the Chair's screen
    f3.record_meeting(cid, COORD, held_on="2026-11-18", attendees=list(ADJS),
                      summary="Again.", decisions={"related_to_study_drug": "related"})  # fmt: skip
    with pytest.raises(Flow3Refused, match="changed"):
        f3.sign_consensus(cid, CHAIR, "demo", shown=seen)
    assert not [e for e in store.events(cid) if e["type"] == "consensus.signed"]
    f3.record_meeting(cid, COORD, held_on="2026-11-18", attendees=list(ADJS),
                      summary="The committee reviewed the dossier.",
                      decisions={"related_to_study_drug": "not_related"})  # fmt: skip
    note = next(json.loads(p.read_text()) for p in store.paths.outbox.glob("*consensus_to_sign*"))
    assert note["link"].startswith("/committee?role=adjudicator&user=adj.okafor")
    other = next(a for a in ADJS if a != CHAIR)
    with pytest.raises(SignatureRefused, match="Chair"):
        f3.sign_consensus(cid, other, "demo", shown=shown(store, cid))
    f3.sign_consensus(cid, CHAIR, "demo", shown=shown(store, cid))
    assert state(store, cid) == State.OUTCOME
    finish(runner)
    outcome = store.get_json(cid, outcomes(store, cid)[0]["outcome"]["path"])
    rel = outcome["values"]["related_to_study_drug"]
    assert rel["how"] == "committee" and rel["sources"][0]["role"] == "Committee Chair"
    assert outcome["values"]["mi_type"]["how"] == "adjudicator_3"
    assert state(store, cid) == State.CLOSED


def test_fields_shown_only_by_an_open_decision_stay_open_with_it(assigned):
    """B06: MI type and date are shown only when the event is confirmed. While that
    decision is open, two hidden answers are not an agreement: the committee decides them
    with it, and a confirmed MI never leaves without its type and date."""
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers(event_date="2026-01-08"))
    a2_answers = {"event_confirmed": "insufficient_data",
                  "related_to_study_drug": "not_related", "rationale": "x"}  # fmt: skip
    f3.sign_review(cid, a2, "demo", a2_answers)
    runner.run_case_steps()
    rnd = current_round(store, cid)
    assert rnd.disputed == ["event_confirmed", "mi_type", "event_date"]
    a3 = rnd.holders["3"]
    f3.sign_review(cid, a3, "demo", {"event_confirmed": "no"})
    runner.run_case_steps()
    assert state(store, cid) == State.CEC_MEETING
    assert f3.committee_fields(cid) == ["event_confirmed", "mi_type", "event_date"]
    assert "mi_type" not in current_round(store, cid).agreed
    meeting = {"held_on": "2026-11-18", "attendees": list(ADJS), "summary": "Discussed."}
    with pytest.raises(Flow3Refused, match="MI type"):
        f3.record_meeting(cid, COORD, **meeting, decisions={"event_confirmed": "yes"})
    f3.record_meeting(cid, COORD, **meeting, decisions={"event_confirmed": "yes",
                      "mi_type": "type_2", "event_date": "2026-01-08"})  # fmt: skip
    f3.sign_consensus(cid, CHAIR, "demo", shown=shown(store, cid))
    finish(runner)
    assert state(store, cid) == State.CLOSED
    values = store.get_json(cid, outcomes(store, cid)[0]["outcome"]["path"])["values"]
    assert {f: (values[f]["value"], values[f]["how"]) for f in ("mi_type", "event_date")} == {
        "mi_type": ("type_2", "committee"), "event_date": ("2026-01-08", "committee")}  # fmt: skip


def test_more_data_goes_back_through_flow_1_and_qc_then_to_the_same_reviewers(assigned):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers())
    with pytest.raises(Flow3Refused, match="say what"):
        f3.request_more_data(cid, a2, "demo", " ")
    with pytest.raises(SignatureRefused):  # no query reaches the site unsigned (ADR 0001)
        f3.request_more_data(cid, a2, "wrong", "Please send the angiography report.")
    f3.request_more_data(cid, a2, "demo", "Please send the angiography report.")
    asked = next(e for e in store.events(cid) if e["type"] == "more_data.requested")
    assert asked["signature"]["signer"] == runner.flow3.users()[a2]["name"]
    assert state(store, cid) == State.MORE_DATA
    runner.run_case_steps()
    assert state(store, cid) == State.QUERY_OPEN
    query = next(e for e in reversed(store.events(cid)) if e["type"] == "query.opened")
    assert query["data"]["codes"] == ["more_data"]
    assert query["data"]["text"] == "Please send the angiography report."
    assert query["data"]["request"] == asked["data"]["request"]  # traced to the signature
    assert ("query_opened", "site:101") in templates(store)

    upload(store, cid, [("angio.pdf", pdf("ANGIOGRAPHY REPORT\nOccluded artery."))], SITE)
    through_qc(runner, store, cid)
    assert state(store, cid) == State.ADJ_REVIEW
    assert (store.case_dir(cid) / "handoff" / "flow3_manifest_v2.json").exists()
    rnd = current_round(store, cid)
    assert (rnd.number, rnd.passes, rnd.signed) == (1, 2, {})  # earlier review superseded
    assert holders(store, cid) == (a1, a2)  # the same adjudicators review again (Q-05)
    assert rnd.manifest["path"] == "handoff/flow3_manifest_v2.json"


def test_more_data_from_the_committee_returns_to_the_committee(assigned):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers(mi_type="type_1"))
    f3.sign_review(cid, a2, "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    f3.sign_review(cid, current_round(store, cid).holders["3"], "demo", {"mi_type": "type_4"})
    runner.run_case_steps()
    assert state(store, cid) == State.CEC_MEETING
    with pytest.raises(SignatureRefused):
        f3.request_more_data(cid, a1, "demo", "x")  # only the Coordinator, for the committee
    f3.request_more_data(cid, COORD, "demo", "The committee needs the troponin values.")
    runner.run_case_steps()
    upload(store, cid, [("lab.pdf", pdf("LABORATORY RESULTS\nTroponin T 2410 ng/L."))], SITE)
    through_qc(runner, store, cid)
    assert state(store, cid) == State.CEC_MEETING  # Q-06: back to the committee
    assert f3.committee_fields(cid) == ["mi_type"]
    f3.record_meeting(cid, COORD, held_on="2026-11-19", attendees=list(ADJS),
                      summary="Reviewed with the new results.",
                      decisions={"mi_type": "type_1"})  # fmt: skip
    f3.sign_consensus(cid, CHAIR, "demo", shown=shown(store, cid))
    assert state(store, cid) == State.OUTCOME


def test_reassignment_needs_a_reason_and_hides_the_old_draft(assigned, monkeypatch):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    third = next(a for a in ADJS if a not in (a1, a2))
    f3.save_draft(cid, a1, {"event_confirmed": "no"})
    with pytest.raises(Flow3Refused, match="reason"):
        f3.reassign(cid, COORD, "1", third, " ")
    with pytest.raises(Flow3Refused, match="already holds"):
        f3.reassign(cid, COORD, "1", a2, "Leave")
    with pytest.raises(SignatureRefused):
        f3.reassign(cid, a2, "1", third, "Leave")
    f3.reassign(cid, COORD, "1", third, "On leave")
    assert holders(store, cid) == (third, a2)
    assert f3.slot_of(cid, a1) is None and f3.draft(cid, third) == {}  # ASN-07
    assert ("adjudication_assigned", f"user:{third}") in templates(store)

    users = f3.users()  # ASN-03: an adjudicator affiliated with the case's site never reviews it
    users[a1] = {**users[a1], "affiliated_sites": ["101"]}
    monkeypatch.setattr(type(f3), "users", lambda self: users)
    assert a1 not in f3.pool(cid)


def racing(monkeypatch, other, module=None):
    """Run `other` while the first signature is being checked (the slow bcrypt step), as a
    second tab or the Coordinator would; return what it raised, or None."""
    import adjud.flow3

    module = module or adjud.flow3
    real, outcome = module.sign, {}

    def sign(*args, **kw):
        if not outcome:
            outcome["raised"] = None
            try:
                other()
            except Exception as exc:
                outcome["raised"] = exc
        return real(*args, **kw)

    monkeypatch.setattr(module, "sign", sign)
    return outcome


def count(store, cid, event_type: str) -> int:
    return sum(e["type"] == event_type for e in store.events(cid))


def signed_slots(store, cid) -> list[str]:
    return [e["data"]["slot"] for e in store.events(cid) if e["type"] == "adjudication.signed"]


def test_one_review_takes_one_signature_from_two_tabs(assigned, monkeypatch):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, _ = holders(store, cid)
    outcome = racing(monkeypatch, lambda: f3.sign_review(cid, a1, "demo", answers()))
    with pytest.raises(Flow3Refused, match="already signed"):
        f3.sign_review(cid, a1, "demo", answers())
    assert outcome["raised"] is None and signed_slots(store, cid) == ["1"]


def test_different_answers_from_two_tabs_are_refused_not_crashed(assigned, monkeypatch):
    """B15: the second tab's answers file name is taken; it says the review is signed."""
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, _ = holders(store, cid)
    outcome = racing(monkeypatch, lambda: f3.sign_review(cid, a1, "demo", answers()))
    with pytest.raises(Flow3Refused, match="already signed"):
        f3.sign_review(cid, a1, "demo", answers(rationale="Second tab."))
    assert outcome["raised"] is None and signed_slots(store, cid) == ["1"]


def test_one_quality_check_takes_one_signature_from_two_tabs(uploaded, monkeypatch):
    """B15: two tabs sign the QC pass with different narratives at the same moment."""
    import adjud.flow2

    runner, store, cid = uploaded
    text = to_qc_review(runner, store, cid)
    f2 = runner.flow2
    outcome = racing(monkeypatch, lambda: f2.sign_qc_pass(cid, SS, "demo", text, "Tab 1"),
                     adjud.flow2)  # fmt: skip
    with pytest.raises(Flow2Refused, match="not waiting"):
        f2.sign_qc_pass(cid, SS, "demo", text + "\nEdited in tab 2.", "Tab 2")
    assert outcome["raised"] is None and count(store, cid, "quality_check.signed") == 1
    assert state(store, cid) == State.READY_FOR_ADJUDICATION
    assert all(r.ok for r in verify_all(store.paths))


def test_a_review_reassigned_while_signing_is_not_signed(assigned, monkeypatch):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    third = next(a for a in ADJS if a not in (a1, a2))
    outcome = racing(monkeypatch, lambda: f3.reassign(cid, COORD, "1", third, "On leave"))
    with pytest.raises(Flow3Refused, match="no open review"):
        f3.sign_review(cid, a1, "demo", answers())
    assert outcome["raised"] is None and signed_slots(store, cid) == []
    assert holders(store, cid) == (third, a2)


def test_a_review_signed_while_reassigning_is_not_reassigned(assigned, monkeypatch):
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    third = next(a for a in ADJS if a not in (a1, a2))
    orig = store.append_event

    def append_event(case_id, event, **kw):  # the signature lands after the checks, before
        if event.type == "adjudication.reassigned" and not signed_slots(store, cid):  # append
            f3.sign_review(cid, a1, "demo", answers())
        return orig(case_id, event, **kw)

    monkeypatch.setattr(store, "append_event", append_event)
    with pytest.raises(Flow3Refused, match="not open"):
        f3.reassign(cid, COORD, "1", third, "On leave")
    assert signed_slots(store, cid) == ["1"] and holders(store, cid) == (a1, a2)


def test_a_reassignment_names_the_review_the_coordinator_saw(assigned):
    """B09: a page left open must not move another review than the one chosen."""
    runner, store, cid = assigned
    f3 = runner.flow3
    a1, a2 = holders(store, cid)
    third = next(a for a in ADJS if a not in (a1, a2))
    f3.sign_review(cid, a1, "demo", answers())
    with pytest.raises(Flow3Refused, match="not open"):
        f3.reassign(cid, COORD, "1", third, "On leave", expected_from=a1)
    with pytest.raises(Flow3Refused, match="changed"):  # slot 2 is open, but not a1's
        f3.reassign(cid, COORD, "2", third, "On leave", expected_from=a1)
    assert holders(store, cid) == (a1, a2)
    f3.reassign(cid, COORD, "2", third, "On leave", expected_from=a2)
    assert holders(store, cid) == (a1, third)


def test_one_consensus_and_one_readjudication_from_two_tabs(assigned, monkeypatch):
    """B15: the second tab is refused with a message, not a TransitionRefused."""
    import adjud.flow4

    runner, store, cid = assigned
    f3, f4 = runner.flow3, runner.flow4
    a1, a2 = holders(store, cid)
    f3.sign_review(cid, a1, "demo", answers(mi_type="type_1", related_to_study_drug="related"))
    f3.sign_review(cid, a2, "demo", answers(mi_type="type_2", related_to_study_drug="not_related"))
    runner.run_case_steps()
    a3 = current_round(store, cid).holders["3"]
    f3.sign_review(cid, a3, "demo",
                   {"mi_type": "type_1", "related_to_study_drug": "unable_to_assess"})  # fmt: skip
    runner.run_case_steps()
    f3.record_meeting(cid, COORD, held_on="2026-11-18", attendees=list(ADJS), summary="Done.",
                      decisions={"related_to_study_drug": "not_related"})  # fmt: skip
    seen = shown(store, cid)
    outcome = racing(monkeypatch, lambda: f3.sign_consensus(cid, CHAIR, "demo", shown=seen))
    with pytest.raises(Flow3Refused, match="no longer waiting"):
        f3.sign_consensus(cid, CHAIR, "demo", shown=seen)
    assert outcome["raised"] is None and count(store, cid, "consensus.signed") == 1
    finish(runner)
    assert state(store, cid) == State.CLOSED

    outcome = racing(monkeypatch, lambda: f4.start_readjudication(cid, COORD, "demo", "Tab 1"),
                     adjud.flow4)  # fmt: skip
    with pytest.raises(Flow4Refused, match="closed"):
        f4.start_readjudication(cid, COORD, "demo", "Tab 2")
    assert outcome["raised"] is None and count(store, cid, "readjudication.started") == 1
    assert all(r.ok for r in verify_all(store.paths))


def test_readjudication_opens_a_new_round_and_supersedes_the_old_outcome(assigned):
    runner, store, cid = assigned
    f3, f4 = runner.flow3, runner.flow4
    a1, a2 = holders(store, cid)
    with pytest.raises(Flow4Refused, match="closed"):
        f4.start_readjudication(cid, COORD, "demo", "New information")
    for a in (a1, a2):
        f3.sign_review(cid, a, "demo", answers())
    runner.run_case_steps()
    finish(runner)
    assert state(store, cid) == State.CLOSED
    with pytest.raises(SignatureRefused):
        f4.start_readjudication(cid, SS, "demo", "x")
    with pytest.raises(Flow4Refused, match="reason"):
        f4.start_readjudication(cid, COORD, "demo", "")
    f4.start_readjudication(cid, COORD, "demo", "The sponsor found a later ECG")
    assert state(store, cid) == State.READY_FOR_ADJUDICATION
    runner.run_case_steps()
    rnd = current_round(store, cid)
    assert (rnd.number, rnd.passes, holders(store, cid)) == (2, 1, (a1, a2))  # RDJ-06: same
    for a in (a1, a2):
        f3.sign_review(cid, a, "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    finish(runner)
    first, second = outcomes(store, cid)
    assert first["superseded"] and not second["superseded"]
    assert second["corrects"] == first["outcome"]  # RDJ-03: linked, nothing edited
    rows = list(csv.DictReader((store.paths.edc_exports / f"{cid}_round2.csv").open()))
    assert all(r["correction"] == "Y" for r in rows)  # RDJ-04
    assert (store.paths.edc_exports / f"{cid}_round1.csv").exists()
    assert all(r.ok for r in verify_all(store.paths))
    assert not any(r.changed for r in rebuild_all(store.paths, write=False))


def test_the_demo_answer_sets_take_their_routes(assigned, monkeypatch):
    """tools/make_flow3_fixtures.py (P-52): `disputed` goes through Adjudicator 3 to the
    committee, signed as the assigned demo adjudicators; reset stages the files."""
    from tools import make_flow3_fixtures as fx

    runner, store, cid = assigned
    monkeypatch.setenv("ADJUD_DATA_DIR", str(store.paths.root))
    assert (store.paths.root / "demo_uploads" / "flow3" / "disputed.yaml").exists()
    assert len(fx.apply(cid, "disputed")) == 2
    runner.run_case_steps()
    assert state(store, cid) == State.ADJ3_REVIEW
    assert current_round(store, cid).disputed == ["mi_type", "related_to_study_drug"]
    assert len(fx.apply(cid, "disputed")) == 1  # Adjudicator 3
    runner.run_case_steps()
    assert state(store, cid) == State.CEC_MEETING
    assert runner.flow3.committee_fields(cid) == ["related_to_study_drug"]
    assert fx.apply(cid, "disputed") == []  # nothing open: the committee is the presenter's
