"""Step 4: de-identification inside the case workflow, using the kit's documents."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from adjud.documents import DocumentProcessor, documents_of, upload
from adjud.intake import IntakePipeline
from adjud.integrity import verify_all
from adjud.workflow import State

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
DOCS = KIT / "documents"


@pytest.fixture
def setup(tmp_path, clock):
    data = tmp_path / "data"
    pipe = IntakePipeline(data, ROOT / "data" / "config", KIT / "edc_mock", clock=clock)
    (data / "flow1-testkit").symlink_to(KIT)  # seeded identities for verification
    pipe.import_file(KIT / "inbox_scenarios" / "01_initial.csv")
    proc = DocumentProcessor(pipe.store, ROOT / "data" / "config")
    return pipe, proc


def case(pipe, subject, ae):
    cid = pipe.case_for(f"DEMO01|{subject}|{ae}")
    assert cid
    return cid


def files(folder, *names):
    return [(n, (DOCS / folder / n).read_bytes()) for n in names]


def test_text_pdfs_and_spreadsheet_are_verified(setup):
    pipe, proc = setup
    cid = case(pipe, "DEMO01-101-0001", "AE-001")
    upload(
        pipe.store,
        cid,
        files(
            "DEMO01-101-0001_AE-001",
            "discharge_summary.pdf",
            "ecg_report.pdf",
            "troponin_results.xlsx",
        ),
        "site101.coord",
    )
    assert pipe.store.state(cid).state == State.DEIDENTIFYING
    summary = proc.process_case(cid)
    assert summary["all_verified"], summary
    assert pipe.store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
    docs = documents_of(pipe.store, cid)
    assert {d["output"]["path"] for d in docs.processed.values()} == {
        "documents/redacted/r1/discharge_summary.pdf",
        "documents/redacted/r1/ecg_report.pdf",
        "documents/redacted/r1/troponin_results.xlsx",
    }
    assert all(r.ok for r in verify_all(pipe.paths))
    notes = [json.loads(p.read_text()) for p in pipe.paths.outbox.glob("*.json")]
    assert any(n["case_id"] == cid and n["template"] == "ready_for_review" for n in notes)


def test_rejected_files_have_no_output_and_the_site_is_told(setup):
    pipe, proc = setup
    cid = case(pipe, "DEMO01-104-0006", "AE-007")
    folder = "DEMO01-104-0006_AE-007"
    upload(
        pipe.store,
        cid,
        files(
            folder,
            "entlassungsbrief.pdf",
            "befund_verschluesselt.pdf",
            "ekg_beschaedigt.pdf",
            "notizen.txt",
        ),
        "site104.coord",
    )
    summary = proc.process_case(cid)
    assert summary["files"] == {
        "entlassungsbrief.pdf": "verified",
        "befund_verschluesselt.pdf": "rejected",
        "ekg_beschaedigt.pdf": "rejected",
        "notizen.txt": "rejected",
    }
    docs = documents_of(pipe.store, cid)
    assert docs.processed["befund_verschluesselt.pdf"]["reject_code"] == "ENCRYPTED"
    assert docs.processed["ekg_beschaedigt.pdf"]["reject_code"] == "CORRUPT"
    assert docs.processed["notizen.txt"]["reject_code"] == "UNSUPPORTED_TYPE"
    assert docs.processed["notizen.txt"]["output"] is None
    templates = [json.loads(p.read_text())["template"] for p in pipe.paths.outbox.glob("*.json")]
    assert "processing_failed" in templates


def test_only_rejected_files_send_the_case_back_to_the_site(setup):
    pipe, proc = setup
    cid = case(pipe, "DEMO01-104-0006", "AE-007")
    upload(pipe.store, cid, files("DEMO01-104-0006_AE-007", "notizen.txt"), "site104.coord")
    proc.process_case(cid)
    assert pipe.store.state(cid).state == State.AWAITING_DOCUMENTS


def test_more_uploads_start_a_new_run_and_never_overwrite(setup):
    pipe, proc = setup
    cid = case(pipe, "DEMO01-101-0001", "AE-001")
    upload(pipe.store, cid, files("DEMO01-101-0001_AE-001", "ecg_report.pdf"), "site101.coord")
    proc.process_case(cid)
    names = upload(
        pipe.store, cid, files("DEMO01-101-0001_AE-001", "ecg_report.pdf"), "site101.coord"
    )
    assert names == ["ecg_report-2.pdf"]
    assert pipe.store.state(cid).state == State.DEIDENTIFYING
    assert proc.process_case(cid)["run"] == 2
    out = pipe.store.case_dir(cid) / "documents" / "redacted"
    assert sorted(p.name for p in out.iterdir()) == ["r1", "r2"]


def test_no_seeded_identity_in_stored_reports_or_logs(setup):
    pipe, proc = setup
    cid = case(pipe, "DEMO01-101-0001", "AE-001")
    upload(
        pipe.store,
        cid,
        files("DEMO01-101-0001_AE-001", "discharge_summary.pdf", "troponin_results.xlsx"),
        "site101.coord",
    )
    proc.process_case(cid)
    ident = json.loads((KIT / "identities" / "identities_SYNTHETIC.json").read_text())
    s = ident["subjects"]["DEMO01-101-0001"]
    stored = [
        p
        for p in pipe.paths.root.rglob("*")
        if p.is_file() and p.suffix in (".json", ".jsonl") and "flow1-testkit" not in str(p)
    ]
    blob = "\n".join(p.read_text() for p in stored)
    blob = re.sub(r"sha256:[0-9a-f]{64}", "", blob)
    for field in ("last", "mrn", "phone", "email", "address", "kin"):
        assert s[field] not in blob, field


def test_mislabelled_image_is_ocred_and_rebuilt_as_an_image_only_page(setup):
    import pymupdf

    pipe, proc = setup
    cid = case(pipe, "DEMO01-104-0006", "AE-007")
    upload(
        pipe.store,
        cid,
        files("DEMO01-104-0006_AE-007", "entlassungsbrief.pdf", "roentgen_thorax.pdf"),
        "site104.coord",
    )
    summary = proc.process_case(cid)
    assert summary["files"]["roentgen_thorax.pdf"] == "verified", summary
    done = documents_of(pipe.store, cid).processed["roentgen_thorax.pdf"]
    assert any(w.startswith("EXTENSION_MISMATCH") for w in done["warnings"])
    out = pipe.store.get_file(cid, done["output"]["path"])
    with pymupdf.open(stream=out, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert doc[0].get_text().strip() == ""  # image-only: no hidden text layer
        assert doc[0].get_images()


def test_removing_a_file_while_identifiers_are_removed_is_refused_plainly(setup):
    """A second tab's "Remove" while the case is DEIDENTIFYING used to crash the page
    with TransitionRefused; the site is now told to wait for the run."""
    from adjud.review import ReviewRefused, delete_document

    pipe, proc = setup
    cid = case(pipe, "DEMO01-104-0006", "AE-007")
    folder = "DEMO01-104-0006_AE-007"
    upload(pipe.store, cid, files(folder, "entlassungsbrief.pdf", "notizen.txt"), "site104.coord")
    proc.process_case(cid)
    upload(pipe.store, cid, files(folder, "roentgen_thorax.pdf"), "site104.coord")
    assert pipe.store.state(cid).state == State.DEIDENTIFYING
    with pytest.raises(ReviewRefused, match="identifiers"):
        delete_document(pipe.store, cid, "notizen.txt", "site104.coord")


def test_a_file_uploaded_during_a_run_is_processed_before_the_site_review(setup, monkeypatch):
    """An upload from another tab while a run is going used to be left out: the case went
    to site review with the late file "not processed yet" and no run to process it."""
    import adjud.documents as D

    pipe, proc = setup
    cid = case(pipe, "DEMO01-101-0001", "AE-001")
    folder = "DEMO01-101-0001_AE-001"
    upload(pipe.store, cid, files(folder, "discharge_summary.pdf"), "site101.coord")
    real, late = D.deidentify_case, files(folder, "ecg_report.pdf")

    def upload_during_run(*args, **kwargs):
        if late:
            upload(pipe.store, cid, [late.pop()], "site101.coord")
        return real(*args, **kwargs)

    monkeypatch.setattr(D, "deidentify_case", upload_during_run)
    summary = proc.process_case(cid)
    assert pipe.store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
    assert set(summary["files"]) == {"discharge_summary.pdf", "ecg_report.pdf"}
    assert set(documents_of(pipe.store, cid).processed) == {"discharge_summary.pdf",
                                                            "ecg_report.pdf"}  # fmt: skip
    assert all(r.ok for r in verify_all(pipe.paths))


def test_two_runs_of_one_case_at_once_do_not_both_run(setup, monkeypatch):
    """ "Remove identifiers now" in two tabs (or a tab and the runner) used to start two
    runs: the second crashed with TransitionRefused and left unreferenced files."""
    import adjud.documents as D

    pipe, proc = setup
    cid = case(pipe, "DEMO01-101-0001", "AE-001")
    upload(pipe.store, cid, files("DEMO01-101-0001_AE-001", "ecg_report.pdf"), "site101.coord")
    real, inner = D.deidentify_case, []

    def second_click_during_run(*args, **kwargs):
        if not inner:
            with pytest.raises(D.DeidBusy):
                inner.append(proc.process_case(cid))
            inner.append("refused")
        return real(*args, **kwargs)

    monkeypatch.setattr(D, "deidentify_case", second_click_during_run)
    assert proc.process_case(cid)["run"] == 1
    assert inner == ["refused"]
    started = [e for e in pipe.store.events(cid) if e["type"] == "deid.started"]
    assert len(started) == 1
    assert pipe.store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
    assert not (pipe.store.case_dir(cid) / "documents" / "redacted" / "r2").exists()
