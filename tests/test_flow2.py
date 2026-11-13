"""Flow 2 workflow with AI off (plan step 13): every judgement goes to a person, and a
query sends the case back through Flow 1 before QC runs again."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pytest

from adjud.documents import upload
from adjud.flow2 import Flow2, Flow2Refused, query_of
from adjud.handoff import read_manifest
from adjud.integrity import rebuild_all, verify_all
from adjud.review import submit
from adjud.runner import Runner
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
SITE, SS = "site101.coord", "ss.anna"


def pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text, fontsize=11)
        return doc.tobytes()


@pytest.fixture
def submitted(tmp_path, clock):
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
    upload(
        store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain since the morning."))], SITE
    )
    runner.run_case_steps()  # de-identification
    submit(store, cid, SITE, "demo", paths.config)
    return runner, store, cid


def state(store, cid) -> State:
    return store.state(cid).state


def templates(store) -> list[str]:
    return [json.loads(p.read_text())["template"] for p in store.paths.outbox.glob("*.json")]


def test_flow2_end_to_end_with_ai_off(submitted):
    runner, store, cid = submitted
    flow2 = runner.flow2
    runner.run_case_steps()  # handoff, then QC starts from the manifest
    assert state(store, cid) == State.QC_CHECKS
    started = next(e for e in store.events(cid) if e["type"] == "qc.started")
    assert started["data"]["manifest"]["path"] == "handoff/flow2_manifest.json"
    runner.run_case_steps()  # QC round 1: a cardiac event needs an ECG report
    assert state(store, cid) == State.QUERY_OPEN
    query = query_of(store, cid)
    assert query["codes"] == ["missing_document"] and "ECG report" in query["text"]
    opened = next(e for e in store.events(cid) if e["type"] == "query.opened")
    assert opened["actor"] == "system:qc"  # no person was needed to send it
    assert "query_opened" in templates(store)

    # The site answers through Flow 1 again; a new manifest version is handed off.
    upload(store, cid, [("ecg.pdf", pdf("ECG REPORT. Sinus rhythm."))], SITE)
    assert state(store, cid) == State.DEIDENTIFYING
    runner.run_case_steps()
    submit(store, cid, SITE, "demo", store.paths.config)
    runner.run_case_steps()  # handoff v2 + QC start
    assert read_manifest(store, cid)["documents"][1]["source_name"] == "summary.pdf"
    assert (store.case_dir(cid) / "handoff" / "flow2_manifest.json").exists()
    assert (store.case_dir(cid) / "handoff" / "flow2_manifest_v2.json").exists()
    runner.run_case_steps()  # round 2: documents complete, but no subject ID or onset date
    assert state(store, cid) == State.QC_REVIEW
    assert "qc_review_needed" in templates(store)

    with pytest.raises(SignatureRefused):
        flow2.open_query(cid, ["other"], "x", user_id=SS, password="wrong")
    with pytest.raises(Flow2Refused, match="reason"):
        flow2.open_query(cid, [], "x", user_id=SS, password="demo")
    with pytest.raises(SignatureRefused, match="Safety Specialist"):
        flow2.sign_qc_pass(cid, SITE, "demo")
    flow2.sign_qc_pass(cid, SS, "demo", "Both documents present")
    assert state(store, cid) == State.NARRATIVE
    runner.run_case_steps()  # template draft
    assert state(store, cid) == State.NARRATIVE_REVIEW
    draft = store.get_file(cid, "narrative/draft_v1.md").decode()
    assert "Myocardial infarction with onset on 2026-03-14" in draft
    assert "ecg.pdf" in draft and "summary.pdf" in draft

    with pytest.raises(Flow2Refused, match="instruction"):  # the template note is still there
        flow2.sign_narrative(cid, SS, "demo", draft.replace("[Add", "Chest pain. [Add"))
    assert state(store, cid) == State.NARRATIVE_REVIEW
    completed = re.sub(r"\[Add[^\]]*\]", "Chest pain treated with a stent.", draft)
    flow2.sign_narrative(cid, SS, "demo", completed)
    assert state(store, cid) == State.READY_FOR_ADJUDICATION
    signed = next(e for e in store.events(cid) if e["type"] == "narrative.signed")
    assert signed["data"]["edited"] and signed["signature"]["signer"] == "Anna Safety"
    with pytest.raises(Flow2Refused):  # frozen once signed
        flow2.sign_narrative(cid, SS, "demo", "other text")

    assert all(r.ok for r in verify_all(store.paths))
    assert not any(r.changed for r in rebuild_all(store.paths, write=False))


def test_automatic_query_only_during_the_checks(submitted):
    runner, store, cid = submitted
    runner.run_case_steps()
    assert state(store, cid) == State.QC_CHECKS
    with pytest.raises(Flow2Refused):  # no results yet in this round
        runner.flow2.open_query(cid, ["missing_document"], "x")
    runner.run_case_steps()
    with pytest.raises(Flow2Refused, match="only possible during the checks"):
        runner.flow2.open_query(cid, ["missing_document"], "x")


def test_documents_cannot_change_after_signing_until_a_query(submitted):
    from adjud.workflow import TransitionRefused

    runner, store, cid = submitted
    runner.run_case_steps()
    with pytest.raises(TransitionRefused):
        upload(store, cid, [("late.pdf", pdf("Late letter."))], SITE)


def test_flow2_template_has_every_placeholder(submitted):
    runner, store, cid = submitted
    runner.run_case_steps()
    text = Flow2(store, store.paths.config).template_draft(cid)
    assert "{" not in text and "Subject DEMO01-101-0001 at site 101" in text
