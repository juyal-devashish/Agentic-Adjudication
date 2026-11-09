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
    """The Flows 1+2 demo (README): a site drops one ZIP and, with the recorded agent
    answers, the case reaches adjudication with only the site's people in its audit
    trail; a case whose letter has no title is typed by the agent and passes too."""
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
    for _ in range(4):  # handoff, QC (+ agents), dossier + narrative, Flow 3 handoff
        runner.run_case_steps()
    for cid, site in cases.items():
        assert store.state(cid).state == State.READY_FOR_ADJUDICATION
        people = {e["actor"] for e in store.events(cid)} - {"system:upload-folder"}
        assert {a for a in people if not a.startswith(("system:", "agent:"))} == {site}
