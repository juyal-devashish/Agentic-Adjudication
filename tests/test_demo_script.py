"""Rehearsal of the core demo script (the Flow 1 demo), end to end through
the same functions the UI calls. If this passes, every scenario in the script works on a
freshly reset demo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjud.documents import documents_of, upload
from adjud.integrity import rebuild_all, verify_all
from adjud.paths import DataPaths
from adjud.review import change_redactions, delete_document, submission_status, submit
from adjud.runner import Runner
from adjud.workflow import State
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    data = tmp_path_factory.mktemp("demo") / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths)
    return Runner(paths)


def staged(runner, case_key, *names):
    folder = runner.paths.root / "demo_uploads" / "documents" / case_key
    return [(n, (folder / n).read_bytes()) for n in names]


def drop(runner, name):
    src = runner.paths.root / "demo_uploads" / "inbox_files" / name
    (runner.paths.inbox / name).write_bytes(src.read_bytes())
    runner.import_inbox()
    return json.loads((runner.paths.processed / f"{name}.report.json").read_text())


def test_1_to_3_import_reimport_updates(demo):
    rep = drop(demo, "01_initial.csv")
    assert (rep["records"], rep["new_cases"], rep["not_eligible"]) == (22, 11, 11)
    assert drop(demo, "02_reimport_identical.csv")["new_cases"] == 0
    rep = drop(demo, "03_updates.csv")
    assert (rep["new_cases"], rep["changed"], rep["stale"]) == (2, 2, 1)
    downgraded = demo.pipeline.case_for("DEMO01|DEMO01-101-0005|AE-006")
    st = demo.pipeline.case_state(downgraded)
    assert "no_longer_eligible" in st["flags"] and st["state"] == "AWAITING_DOCUMENTS"


def test_4_manual_entry(demo):
    v = json.loads((KIT / "inbox_scenarios" / "manual_entry_valid.json").read_text())
    r = demo.pipeline.manual_entry(v["record"], v["reason"], v["actor"])
    assert r.new_cases == 1


def test_5_text_pdfs_review_and_submit(demo):
    store = demo.pipeline.store
    cid = demo.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    upload(
        store,
        cid,
        staged(
            demo,
            "DEMO01-101-0001_AE-001",
            "discharge_summary.pdf",
            "ecg_report.pdf",
            "troponin_results.xlsx",
        ),
        "site101.coord",
    )
    demo.run_case_steps()
    assert submission_status(store, cid).ready
    submit(store, cid, "site101.coord", "demo", demo.paths.config)
    demo.run_case_steps()  # handoff
    assert any(e["type"] == "handoff.flow2_ready" for e in store.events(cid))


def test_6_poor_scans_and_image_banner(demo):
    store = demo.pipeline.store
    cid = demo.pipeline.case_for("DEMO01|DEMO01-102-0004|AE-005")
    upload(
        store,
        cid,
        staged(
            demo,
            "DEMO01-102-0004_AE-005",
            "summary_letter_lowquality.jpg",
            "referral_letter_mixed.pdf",
        ),
        "site102.coord",
    )
    demo.run_case_steps()
    processed = documents_of(store, cid).processed
    assert {n: d["status"] for n, d in processed.items()} == {
        "summary_letter_lowquality.jpg": "verified",
        "referral_letter_mixed.pdf": "verified",
    }


def test_7_identifier_learned_in_one_file_is_removed_from_another(demo):
    """The residual-then-add-term flow is covered by tests/test_review.py (TC-DOC-M2/M3).
    Here: the MRN learned from the German letter's banner is also removed from a later,
    unlabelled lab note of the same case."""
    import pymupdf

    store = demo.pipeline.store
    cid = demo.pipeline.case_for("DEMO01|DEMO01-104-0006|AE-007")
    letter = staged(demo, "DEMO01-104-0006_AE-007", "entlassungsbrief.pdf")
    upload(store, cid, letter, "site104.coord")
    demo.run_case_steps()
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Lab sample 8859641 received.")  # bare number
        sample = doc.tobytes()
    upload(store, cid, [("lab_sample.pdf", sample)], "site104.coord")
    demo.run_case_steps()
    done = documents_of(store, cid).processed["lab_sample.pdf"]
    assert done["status"] == "verified" and done["findings"] >= 1


def test_8_untick_with_reason(demo):
    store = demo.pipeline.store
    cid = demo.pipeline.case_for("DEMO01|DEMO01-104-0006|AE-007")
    done = documents_of(store, cid).processed["entlassungsbrief.pdf"]
    report = store.get_json(cid, done["report"]["path"])
    staff = next(f for f in report["findings"] if f["category"] == "STAFF_NAME")
    change_redactions(
        store,
        cid,
        "site104.coord",
        reason="Treating physician may stay",
        keep=[{"file": "entlassungsbrief.pdf", **staff}],
    )
    demo.run_case_steps()
    assert store.state(cid).state == State.AWAITING_SITE_CONFIRMATION


def test_9_rejections(demo):
    store = demo.pipeline.store
    cid = demo.pipeline.case_for("DEMO01|DEMO01-104-0006|AE-007")
    upload(
        store,
        cid,
        staged(
            demo,
            "DEMO01-104-0006_AE-007",
            "befund_verschluesselt.pdf",
            "ekg_beschaedigt.pdf",
            "notizen.txt",
            "roentgen_thorax.pdf",
        ),
        "site104.coord",
    )
    demo.run_case_steps()
    processed = documents_of(store, cid).processed
    assert processed["befund_verschluesselt.pdf"]["reject_code"] == "ENCRYPTED"
    assert processed["ekg_beschaedigt.pdf"]["reject_code"] == "CORRUPT"
    assert processed["notizen.txt"]["reject_code"] == "UNSUPPORTED_TYPE"
    assert processed["roentgen_thorax.pdf"]["status"] == "verified"
    assert not submission_status(store, cid).ready
    for name in ("befund_verschluesselt.pdf", "ekg_beschaedigt.pdf", "notizen.txt"):
        delete_document(store, cid, name, "site104.coord", reason="rejected")
    assert submission_status(store, cid).ready


def test_11_tamper_detected_and_state_rebuilt(demo):
    assert all(r.ok for r in verify_all(demo.paths))
    cid = demo.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    log = demo.pipeline.store.case_dir(cid) / "events.jsonl"
    original = log.read_text()
    lines = original.splitlines()
    rec = json.loads(lines[1])
    rec["actor"] = "someone.else"
    lines[1] = json.dumps(rec)
    log.write_text("\n".join(lines) + "\n")
    bad = [r for r in verify_all(demo.paths) if not r.ok]
    assert len(bad) == 1 and "line 2" in bad[0].problems[0]
    log.write_text(original)
    for case_id in demo.pipeline.store.list_cases():
        (demo.pipeline.store.case_dir(case_id) / "case.json").unlink()
    rebuild_all(demo.paths)
    assert all(r.ok for r in verify_all(demo.paths))
    assert not any(r.changed for r in rebuild_all(demo.paths, write=False))


def test_flows_1_and_2_demo_in_replay_mode(tmp_path, monkeypatch):
    """The Flows 1+2 demo (README): a site drops one ZIP; code and the recorded agent
    answers do the work between the people's steps, and the case reaches adjudication
    with exactly two signatures in its audit trail: the site's submission, and the Safety
    Specialist's one Quality Check signature over the QC pass and the AI-drafted narrative
    (P-37, P-39). A case whose letter has no title is typed by the agent, but the recorded
    live answer is unsure (confidence 0.7), so the Safety Specialist gets no suggestion."""
    import io
    import zipfile

    from adjud.paths import DataPaths
    from adjud.review import submit
    from adjud.runner import Runner
    from adjud.workflow import State
    from tools.reset_demo import reset

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    store = runner.pipeline.store
    runner.take_uploads()  # folders for every case waiting for documents
    cases = {}
    for folder, site in (("DEMO01-101-0001_AE-001", "101"), ("DEMO01-102-0004_AE-005", "102")):
        subject, ae = folder.split("_")
        cid = runner.pipeline.case_for(f"DEMO01|{subject}|{ae}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted((KIT / "documents" / folder).iterdir()):
                zf.writestr(f.name, f.read_bytes())
        (paths.uploads / cid / "documents.zip").write_bytes(buf.getvalue())
        cases[cid] = f"site{site}.coord"
    runner.take_uploads()
    runner.take_uploads()  # unchanged since the last tick: imported
    runner.run_case_steps()  # identifiers removed
    for cid, site in cases.items():
        assert store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
        submit(store, cid, site, "demo", paths.config)  # the one human signature
    for _ in range(2):  # handoff, QC (+ agents)
        runner.run_case_steps()
    (ae001, site), (ae005, _) = cases.items()
    assert store.state(ae001).state == State.QC_REVIEW  # every case waits for a person
    review = store.get_json(ae001, "qc/checks_v1.json")
    assert review["proposal"] == "pass" and review["suggestion"]["status"] == "ok"
    assert review["narrative"]["source"] == "ai"  # drafted before the review (P-39)
    text = store.get_file(ae001, review["narrative"]["draft"]["path"]).decode()
    edited = text + "\nThe patient was discharged home."
    runner.flow2.sign_qc_pass(ae001, "ss.anna", "demo", edited)
    runner.run_case_steps()  # Flow 3 handoff
    assert store.state(ae001).state == State.READY_FOR_ADJUDICATION
    signed = [e["type"] for e in store.events(ae001) if e.get("signature")]
    assert signed == ["submission.signed", "quality_check.signed"]
    passed = next(e for e in store.events(ae001) if e["type"] == "quality_check.signed")
    assert passed["data"]["edited"]  # the edited narrative, not the draft, goes forward
    package = store.get_json(ae001, "handoff/flow3_manifest.json")
    assert package["narrative"]["path"] == passed["data"]["narrative"]["path"]
    people = {e["actor"] for e in store.events(ae001)} - {"system:upload-folder"}
    assert {a for a in people if not a.startswith("system:")} == {site, "ss.anna"}
    assert store.state(ae005).state == State.QC_REVIEW
    assert store.get_json(ae005, "qc/checks_v1.json")["proposal"] == "unclear"


def test_the_short_demo_has_three_cases_and_keeps_its_recorded_ai(tmp_path, monkeypatch):
    """`tools.reset_demo` without --all: three cases and one record that is not eligible,
    and the recorded agent answers still match although the case numbers changed. The
    three demo stories (P-39): AE-001 a suggested pass, AE-005 nothing pre-selected (the
    AI is not sure), AE-006 a suggested query for the missing required documents."""
    from adjud.documents import upload
    from adjud.paths import DataPaths
    from adjud.review import submit
    from adjud.runner import Runner
    from adjud.workflow import State
    from tools.reset_demo import DEMO_AES, reset

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    staged = sorted(p.name for p in (paths.root / "demo_uploads" / "documents").iterdir())
    assert staged == ["DEMO01-101-0001_AE-001", "DEMO01-101-0005_AE-006",
                      "DEMO01-102-0004_AE-005"]  # fmt: skip
    runner = Runner(paths)
    runner.run_once()
    pipe, store = runner.pipeline, runner.pipeline.store
    assert sorted(store.list_cases()) == ["DEMO01-0001", "DEMO01-0002", "DEMO01-0003"]
    assert pipe.case_for("DEMO01|DEMO01-102-0004|AE-005") == "DEMO01-0002"
    for folder in staged:
        subject, ae = folder.split("_")
        cid, site = pipe.case_for(f"DEMO01|{subject}|{ae}"), f"site{subject[7:10]}.coord"
        files = sorted((KIT / "documents" / folder).iterdir())
        upload(store, cid, [(f.name, f.read_bytes()) for f in files], site)
        runner.run_case_steps()
        submit(store, cid, site, "demo", paths.config)
    for _ in range(2):
        runner.run_case_steps()
    clean = store.get_json("DEMO01-0001", "qc/checks_v1.json")
    assert clean["proposal"] == "pass" and clean["suggestion"]["label"] == "AI"  # recorded
    assert clean["narrative"]["source"] == "ai" and clean["narrative"]["simulated"] is False
    unsure = store.get_json("DEMO01-0002", "qc/checks_v1.json")
    assert unsure["proposal"] == "unclear" and unsure["ai"][0]["label"] == "AI"
    more = store.get_json("DEMO01-0003", "qc/checks_v1.json")
    assert more["proposal"] == "query" and more["query_codes"] == ["missing_document"]
    assert more["suggestion"]["would_help"]  # the AI's advice, beside code's required list
    for cid in ("DEMO01-0001", "DEMO01-0002", "DEMO01-0003"):
        assert store.state(cid).state == State.QC_REVIEW  # one stop, a person decides


def test_after_a_query_the_narrative_is_the_ai_draft_from_before_it(tmp_path, monkeypatch):
    """Nirav's run (P-59): the Safety Specialist sends a query without editing the AI
    narrative; the site answers with a new document, for which no AI answer is recorded,
    so round 2 has only the study template. The Quality check page and the Slack notice
    start from round 1's AI narrative, and the narrative box stays whatever the decision."""
    from streamlit.testing.v1 import AppTest

    from adjud.documents import upload
    from adjud.flow2 import qc_drafts
    from adjud.paths import DataPaths
    from adjud.review import submit
    from adjud.runner import Runner
    from adjud.slack_app.core import Workspace
    from adjud.slack_app.dm import NoticeDetail
    from adjud.workflow import State
    from tools.reset_demo import DEMO_AES, reset

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    monkeypatch.setenv("ADJUD_DATA_DIR", str(data))
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    runner = Runner(paths)
    runner.run_once()
    store, cid, site = runner.pipeline.store, "DEMO01-0001", "site101.coord"
    files = sorted((KIT / "documents" / "DEMO01-101-0001_AE-001").iterdir())
    upload(store, cid, [(f.name, f.read_bytes()) for f in files], site)
    runner.run_case_steps()
    submit(store, cid, site, "demo", paths.config)
    for _ in range(2):
        runner.run_case_steps()
    first = store.get_json(cid, "qc/checks_v1.json")["narrative"]
    assert first["source"] == "ai"
    ai_text = store.get_file(cid, first["draft"]["path"]).decode()

    runner.flow2.open_query(cid, ["missing_document"], "Send the discharge summary again.",
                            user_id="ss.anna", password="demo")  # fmt: skip
    again = files[0]
    upload(store, cid, [("discharge_summary-2.pdf", again.read_bytes())], site)
    runner.run_case_steps()
    submit(store, cid, site, "demo", paths.config)
    for _ in range(2):
        runner.run_case_steps()
    assert store.state(cid).state == State.QC_REVIEW
    assert store.get_json(cid, "qc/checks_v2.json")["narrative"]["source"] == "template"
    drafts = qc_drafts(store, cid)
    assert drafts.narrative == ai_text and drafts.narrative_via == "earlier_ai"
    assert drafts.narrative_round == 1 and not drafts.narrative_edited
    note = {"template": "qc_review_needed", "case_id": cid}
    said = NoticeDetail(Workspace(paths))(note) or ""
    assert "from before the query" in said and ai_text.splitlines()[0][:40] in said

    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30)
    at.session_state["role"], at.session_state["case_id"] = "safety_specialist", cid
    at.run()
    at.switch_page("views/quality_check.py")
    at.run()
    assert not at.exception, at.exception

    def box() -> str:
        return next(a for a in at.text_area if a.label == "Narrative").value

    assert box() == ai_text  # shown before any decision
    decision = next(r for r in at.radio if r.label == "Your decision")
    decision.set_value("query").run()
    assert box() == ai_text  # still there with "query" chosen
    next(a for a in at.text_area if a.label == "Narrative").set_value("Kept edit.").run()
    next(r for r in at.radio if r.label == "Your decision").set_value("pass").run()
    assert box() == "Kept edit."  # back on "pass", the edit is there
    assert qc_drafts(store, cid).narrative.strip() == "Kept edit."
