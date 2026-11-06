"""Step 6: site review, submission and the Flow 2 handoff (kit TC-DOC-M1 to M4)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from adjud.documents import DocumentProcessor, documents_of, upload
from adjud.handoff import pending_handoffs, read_manifest, write_handoff
from adjud.intake import IntakePipeline
from adjud.integrity import verify_all
from adjud.review import (
    ReviewRefused,
    change_redactions,
    delete_document,
    submission_status,
    submit,
)
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
CONFIG = ROOT / "data" / "config"
DOCS = KIT / "documents" / "DEMO01-101-0001_AE-001"
SITE = "site101.coord"


def pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        return doc.tobytes()


@pytest.fixture
def ctx(tmp_path, clock):
    data = tmp_path / "data"
    pipe = IntakePipeline(data, CONFIG, KIT / "edc_mock", clock=clock)
    (data / "flow1-testkit").symlink_to(KIT)
    pipe.import_file(KIT / "inbox_scenarios" / "01_initial.csv")
    proc = DocumentProcessor(pipe.store, CONFIG)
    cid = pipe.case_for("DEMO01|DEMO01-101-0001|AE-001")
    return pipe.store, proc, cid


def processed(store, cid, name):
    return documents_of(store, cid).processed[name]


def test_m2_m3_residual_blocks_submission_until_the_site_adds_the_term(ctx):
    store, proc, cid = ctx
    upload(store, cid, [("note.pdf", pdf("Sample 2929647 was sent to the lab."))], SITE)
    proc.process_case(cid)
    # No label, no pattern, no NER hit: only the seeded-identity check catches it.
    assert processed(store, cid, "note.pdf")["status"] == "blocked"
    status = submission_status(store, cid)
    assert not status.ready and "residual" in status.reasons[0]
    with pytest.raises(ReviewRefused):
        submit(store, cid, SITE, "demo", CONFIG)

    change_redactions(store, cid, SITE, add_terms=["2929647"])
    assert store.state(cid).state == State.DEIDENTIFYING
    proc.process_case(cid)
    assert processed(store, cid, "note.pdf")["status"] == "verified"
    out = store.get_file(cid, processed(store, cid, "note.pdf")["output"]["path"])
    with pymupdf.open(stream=out, filetype="pdf") as doc:
        assert "2929647" not in doc[0].get_text()
        assert "sent to the lab" in doc[0].get_text()
    events = (store.case_dir(cid) / "events.jsonl").read_text()
    assert "2929647" not in events  # the term itself never enters the log


def test_m1_unticked_finding_is_unredacted_with_its_reason_logged(ctx):
    store, proc, cid = ctx
    upload(store, cid, [("ecg_report.pdf", (DOCS / "ecg_report.pdf").read_bytes())], SITE)
    proc.process_case(cid)
    report = store.get_json(cid, processed(store, cid, "ecg_report.pdf")["report"]["path"])
    staff = next(f for f in report["findings"] if f["category"] == "STAFF_NAME")
    with pytest.raises(ReviewRefused, match="reason"):
        change_redactions(store, cid, SITE, keep=[{"file": "ecg_report.pdf", **staff}])
    change_redactions(
        store,
        cid,
        SITE,
        reason="Reporting physician may stay visible",
        keep=[{"file": "ecg_report.pdf", **staff}],
    )
    proc.process_case(cid)
    done = processed(store, cid, "ecg_report.pdf")
    assert done["status"] == "verified"
    with pymupdf.open(stream=store.get_file(cid, done["output"]["path"]), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "Amelia Hart" in text and "Philip Roberts" not in text
    change = next(e for e in store.events(cid) if e["type"] == "redactions.changed")
    assert change["reason"] == "Reporting physician may stay visible"
    assert "Amelia" not in json.dumps(change)  # only the id and masked text are logged


def test_m4_submission_signature_and_handoff_manifest(ctx):
    store, proc, cid = ctx
    upload(
        store,
        cid,
        [(n, (DOCS / n).read_bytes()) for n in ("discharge_summary.pdf", "troponin_results.xlsx")],
        SITE,
    )
    proc.process_case(cid)
    assert submission_status(store, cid).ready
    with pytest.raises(SignatureRefused):
        submit(store, cid, SITE, "wrong", CONFIG)
    with pytest.raises(SignatureRefused):
        submit(store, cid, "ss.anna", "demo", CONFIG)  # only a site user submits
    signed = submit(store, cid, SITE, "demo", CONFIG)
    assert store.state(cid).state == State.SUBMITTED
    assert signed["signature"]["meaning"].startswith("Submitted")
    assert pending_handoffs(store) == [cid]

    manifest = write_handoff(store, cid, CONFIG, proc.outbox)
    assert pending_handoffs(store) == []
    assert read_manifest(store, cid) == manifest
    assert {d["source_name"] for d in manifest["documents"]} == {
        "discharge_summary.pdf",
        "troponin_results.xlsx",
    }
    from adjud.events import sha256_bytes

    for d in manifest["documents"]:
        assert d["path"].startswith("documents/redacted/")
        assert sha256_bytes(store.get_file(cid, d["path"])) == d["sha256"]
    assert "original" not in json.dumps(manifest)
    assert manifest["submission"]["signer"] == "Site 101 Coordinator"
    assert all(r.ok for r in verify_all(store.paths))
    templates = [json.loads(p.read_text())["template"] for p in store.paths.outbox.glob("*.json")]
    assert {"submission_received", "documents_received"} <= set(templates)


def test_rejected_file_must_be_deleted_before_submission(ctx):
    store, proc, cid = ctx
    upload(
        store,
        cid,
        [
            ("ecg_report.pdf", (DOCS / "ecg_report.pdf").read_bytes()),
            ("notes.txt", b"plain text notes"),
        ],
        SITE,
    )
    proc.process_case(cid)
    status = submission_status(store, cid)
    assert not status.ready and any("rejected" in r for r in status.reasons)
    delete_document(store, cid, "notes.txt", SITE, reason="wrong file")
    assert submission_status(store, cid).ready
