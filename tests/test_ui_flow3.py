"""Flows 3 and 4 in the UI (plan P-41 to P-53): the adjudicator's blind form, the
committee page, the Coordinator's assignments, blinding on the case record and in the
Assistant. AI is off; nothing here calls an API."""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from adjud.documents import upload
from adjud.flow3 import current_round
from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.workflow import State
from tests.test_flow3 import SITE, answers, pdf, through_qc
from tests.test_ui import ROOT, html_text, run
from tools.reset_demo import DEMO_AES, reset

USERS = {u["id"]: u for u in yaml.safe_load((ROOT / "data/config/users.yaml").read_text())["users"]}


@pytest.fixture
def reviewing(tmp_path, monkeypatch):
    """DEMO01-0001 with its two reviews assigned."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    monkeypatch.setenv("ADJUD_DATA_DIR", str(data))
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    runner = Runner(paths)
    runner.run_once()
    store = runner.pipeline.store
    cid = "DEMO01-0001"
    upload(store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                        ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE)  # fmt: skip
    through_qc(runner, store, cid)
    assert store.state(cid).state == State.ADJ_REVIEW
    return runner, store, cid


def as_user(user_id: str, page: str, cid: str | None = None):
    u = USERS[user_id]
    state = {"role": u["role"], f"user_{u['role']}": u}
    if cid:
        state["case_id"] = cid
    return run(page, **state)


def tables(at) -> str:
    return "\n".join(d.value.to_string() for d in at.dataframe)


def text(at) -> str:
    return "\n".join([m.value for m in at.markdown] + [html_text(at)])


def test_an_adjudicator_signs_a_blind_review_on_the_form(reviewing):
    runner, store, cid = reviewing
    rnd = current_round(store, cid)
    a1, a2 = rnd.holders["1"], rnd.holders["2"]
    other = next(a for a in ("adj.okafor", "adj.lindqvist", "adj.reyes") if a not in (a1, a2))
    at = as_user(other, "views/adjudication.py")  # not assigned: the case is not listed
    assert cid not in next(s for s in at.selectbox if s.label == "Case").options

    runner.flow3.sign_review(cid, a2, "demo", answers(mi_type="type_2"))
    at = as_user(a1, "views/adjudication.py", cid)
    assert "type_2" not in text(at) and "Type 2" not in [r.value for r in at.radio]
    assert "You are Adjudicator 1" in text(at)
    labels = [r.label for r in at.radio]
    assert "MI type (Fourth Universal Definition)" not in labels  # shown once confirmed
    next(r for r in at.radio if r.label.startswith("Is the event")).set_value("yes").run()
    next(r for r in at.radio if r.label.startswith("MI type")).set_value("type_1").run()
    next(r for r in at.radio if r.label.startswith("Related")).set_value("not_related").run()
    at.date_input[0].set_value(date(2026, 3, 14)).run()
    at.text_area[0].input("Troponin rise.").run()
    next(t for t in at.text_input if t.label.startswith("Password")).input("demo")
    next(b for b in at.button if b.label == "Sign my review").click().run()
    assert not at.exception, at.exception
    assert "You signed your review" in text(at) and "Type 2" not in text(at)
    runner.run_case_steps()
    assert store.state(cid).state == State.ADJ3_REVIEW  # type 1 against type 2


def test_the_site_never_sees_who_adjudicates(reviewing):
    runner, store, cid = reviewing
    rnd = current_round(store, cid)
    for slot in ("1", "2"):
        runner.flow3.sign_review(cid, rnd.holders[slot], "demo", answers())
    for _ in range(4):
        runner.run_case_steps()
    assert store.state(cid).state == State.CLOSED
    at = as_user(SITE, "views/results.py", cid)
    shown = text(at) + tables(at)
    assert "not shown to sites" in shown
    for a in ("Okafor", "Lindqvist", "Reyes", "Pharmacovigilance"):
        assert a not in shown
    at = as_user("cec.sofia", "views/results.py", cid)
    shown = text(at) + tables(at)
    assert "Outcome of round 1" in [e.label for e in at.expander]
    assert "Both reviews agree" in shown and "Closed" in html_text(at)


FLOW34_MESSAGES = ("More information was requested for an adjudication",
                   "A case needs the adjudication committee", "Adjudication complete",
                   "Re-adjudication started")  # fmt: skip
FLOW34_PEOPLE = ("Okafor", "Lindqvist", "Reyes", "Sofia Marques", "EDC (mock)")


def test_the_site_record_hides_who_asked_for_more_data(reviewing):
    """ACC-04, P-45: the site sees its own query, but not the adjudicator who asked nor the
    e-mail to the CEC Coordinator, in the audit trail or the notifications."""
    runner, store, cid = reviewing
    runner.flow3.request_more_data(cid, current_round(store, cid).holders["1"], "demo",
                                   "The angiography report.")  # fmt: skip
    runner.run_case_steps()
    assert store.state(cid).state == State.QUERY_OPEN
    shown = tables(as_user(SITE, "views/results.py", cid))
    assert "A query about your documents needs an answer" in shown  # its own query stays
    for word in FLOW34_MESSAGES + FLOW34_PEOPLE:
        assert word not in shown, word


def test_the_site_record_hides_the_outcome_and_re_adjudication_e_mails(reviewing):
    runner, store, cid = reviewing
    rnd = current_round(store, cid)
    for slot in ("1", "2"):
        runner.flow3.sign_review(cid, rnd.holders[slot], "demo", answers())
    for _ in range(4):
        runner.run_case_steps()
    assert store.state(cid).state == State.CLOSED
    runner.flow4.start_readjudication(cid, "cec.sofia", "demo", "New ECG found.")
    runner.run_case_steps()
    shown = tables(as_user(SITE, "views/results.py", cid))
    for word in FLOW34_MESSAGES + FLOW34_PEOPLE:
        assert word not in shown, word


def test_the_committee_records_and_the_chair_signs(reviewing):
    runner, store, cid = reviewing
    f3 = runner.flow3
    rnd = current_round(store, cid)
    f3.sign_review(cid, rnd.holders["1"], "demo", answers(mi_type="type_1"))
    f3.sign_review(cid, rnd.holders["2"], "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    f3.sign_review(cid, current_round(store, cid).holders["3"], "demo", {"mi_type": "type_4"})
    runner.run_case_steps()
    assert store.state(cid).state == State.CEC_MEETING

    at = as_user("cec.sofia", "views/committee.py", cid)
    assert "MI type (Fourth Universal Definition)" in str(at.dataframe[0].value)
    next(r for r in at.radio if r.label.startswith("Decision")).set_value("type_1")
    next(t for t in at.text_area if t.label == "Summary of the discussion").input("Agreed: type 1.")
    next(b for b in at.button if b.label == "Record the meeting").click().run()
    assert not at.exception, at.exception
    assert "Recorded meeting" in [h.value for h in at.header]

    at = as_user("adj.lindqvist", "views/committee.py", cid)  # not the Chair
    assert "signed by its Chair" in text(at)
    at = as_user("adj.okafor", "views/committee.py", cid)
    next(t for t in at.text_input if t.label.startswith("Password")).input("demo")
    next(b for b in at.button if b.label == "Sign the consensus").click().run()
    assert not at.exception, at.exception
    assert store.state(cid).state == State.OUTCOME


def test_the_chair_cannot_sign_a_meeting_record_he_has_not_seen(reviewing):
    """B07: a meeting recorded again while the Chair's page is open is not signed with
    the record he was shown; he is told it changed and sees the new one."""
    runner, store, cid = reviewing
    f3 = runner.flow3
    rnd = current_round(store, cid)
    f3.sign_review(cid, rnd.holders["1"], "demo", answers(mi_type="type_1"))
    f3.sign_review(cid, rnd.holders["2"], "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    f3.sign_review(cid, current_round(store, cid).holders["3"], "demo", {"mi_type": "type_4"})
    runner.run_case_steps()
    adjs = [u for u, v in f3.users().items() if v["role"] == "adjudicator"]
    meeting = {"held_on": "2026-11-18", "attendees": adjs}
    f3.record_meeting(cid, "cec.sofia", **meeting, summary="v1: type 1.",
                      decisions={"mi_type": "type_1"})  # fmt: skip
    at = as_user("adj.okafor", "views/committee.py", cid)
    assert "Type 1" in tables(at)
    next(t for t in at.text_input if t.label.startswith("Password")).input("demo")
    f3.record_meeting(cid, "cec.sofia", **meeting, summary="v2: type 2.",
                      decisions={"mi_type": "type_2"})  # fmt: skip
    next(b for b in at.button if b.label == "Sign the consensus").click().run()
    assert not at.exception, at.exception
    assert not [e for e in store.events(cid) if e["type"] == "consensus.signed"]
    assert any("changed" in e.value for e in at.error)
    assert "Type 2" in tables(at)  # the new record is now on screen
    next(t for t in at.text_input if t.label.startswith("Password")).input("demo")
    next(b for b in at.button if b.label == "Sign the consensus").click().run()
    signed = [e for e in store.events(cid) if e["type"] == "consensus.signed"]
    assert signed[0]["data"]["meeting"]["path"].endswith("meeting_v2.json")


def test_the_coordinator_reassigns_and_the_assistant_lists_tasks(reviewing):
    runner, store, cid = reviewing
    rnd = current_round(store, cid)
    a1 = rnd.holders["1"]
    at = as_user("cec.sofia", "views/assignments.py")
    assert cid in str(at.dataframe[0].value)
    at = as_user("cec.sofia", "views/assignments.py", cid)
    next(t for t in at.text_input if t.label == "Reason (required)").input("On leave")
    next(b for b in at.button if b.label == "Reassign").click().run()
    assert not at.exception, at.exception
    assert current_round(store, cid).holders["1"] != a1

    me = current_round(store, cid).holders["1"]
    at = as_user(me, "views/assistant.py")
    assert f"Adjudicate {cid}" in [b.label for b in at.button]
    assert "independent review is open" in text(at)


def test_more_data_from_the_form_reaches_the_site_and_the_quality_check(reviewing):
    """The adjudicator signs the request on the page; the site sees an anonymous query
    from the adjudication; after the site answers, the Quality Check shows where it came
    from (P-47, ADR 0001, ACC-04)."""
    runner, store, cid = reviewing
    a1 = current_round(store, cid).holders["1"]
    at = as_user(a1, "views/adjudication.py", cid)
    next(t for t in at.text_area if t.label == "What is needed").input("The angiography report.")
    pw = [t for t in at.text_input if t.label.startswith("Password")]
    pw[-1].input("demo")
    next(b for b in at.button if b.label == "Sign and ask the site for more data").click().run()
    assert not at.exception, at.exception
    assert store.state(cid).state == State.MORE_DATA
    runner.run_case_steps()
    assert store.state(cid).state == State.QUERY_OPEN

    at = as_user(SITE, "views/site_documents.py", cid)
    shown = "\n".join(w.value for w in at.warning)
    assert "Query from the adjudication" in shown and "The angiography report." in shown
    assert USERS[a1]["name"] not in shown + text(at)
    at = as_user(SITE, "views/assistant.py")
    assert "more information is needed for the adjudication" in text(at)

    upload(store, cid, [("angio.pdf", pdf("ANGIOGRAPHY REPORT\nOccluded artery."))], SITE)
    runner.run_case_steps()
    from adjud.review import submit

    submit(store, cid, SITE, "demo", store.paths.config)
    runner.run_case_steps()
    runner.run_case_steps()
    assert store.state(cid).state == State.QC_REVIEW
    at = as_user("ss.anna", "views/quality_check.py", cid)
    assert any("the adjudication (a signed request for more data)" in i.value for i in at.info)
