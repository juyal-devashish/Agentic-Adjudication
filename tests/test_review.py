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
    assert not status.ready and "still contains" in status.reasons[0]
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


# --- boxes drawn by the site (plan step 11, P-30) -------------------------------------------


def _fractions(page: pymupdf.Page, rect: pymupdf.Rect) -> list[float]:
    w, h = page.rect.width, page.rect.height
    return [rect.x0 / w, rect.y0 / h, rect.x1 / w, rect.y1 / h]


def test_box_removes_text_no_detector_finds_and_survives_later_runs(ctx):
    store, proc, cid = ctx
    upload(store, cid, [("note.pdf", pdf("Seen today. Tray zebra quokka is full, ward 7."))], SITE)
    proc.process_case(cid)
    out = store.get_file(cid, processed(store, cid, "note.pdf")["output"]["path"])
    with pymupdf.open(stream=out, filetype="pdf") as doc:
        assert "zebra quokka" in doc[0].get_text()  # nothing detects these made-up words
        box = _fractions(doc[0], doc[0].search_for("zebra quokka")[0])
    change_redactions(store, cid, SITE, boxes=[{"file": "note.pdf", "page": 1, "rect": box}])
    proc.process_case(cid)
    change_redactions(store, cid, SITE, add_terms=["ward 7"])  # a later, unrelated change
    proc.process_case(cid)
    done = processed(store, cid, "note.pdf")
    assert done["status"] == "verified"
    with pymupdf.open(stream=store.get_file(cid, done["output"]["path"]), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "zebra" not in text and "quokka" not in text and "Seen today" in text
    assert all(r.ok for r in verify_all(store.paths))


def test_box_on_a_scan_blacks_out_the_pixels(ctx):
    from io import BytesIO

    from PIL import Image, ImageDraw

    store, proc, cid = ctx
    img = Image.new("L", (1240, 1754), 255)  # A4 at 150 dpi
    draw = ImageDraw.Draw(img)
    draw.rectangle((300, 400, 700, 460), fill=90)  # a grey "handwritten" scribble
    buf = BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    upload(store, cid, [("scan.png", buf.getvalue())], SITE)
    proc.process_case(cid)
    box = [280 / 1240, 390 / 1754, 720 / 1240, 470 / 1754]
    change_redactions(store, cid, SITE, boxes=[{"file": "scan.png", "page": 1, "rect": box}])
    proc.process_case(cid)
    done = processed(store, cid, "scan.png")
    with pymupdf.open(stream=store.get_file(cid, done["output"]["path"]), filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    out = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    sx, sy = out.width / 1240, out.height / 1754
    area = out.crop((int(310 * sx), int(410 * sy), int(690 * sx), int(450 * sy)))
    assert area.getextrema()[1] < 10  # the grey scribble is now black, not grey


@pytest.mark.parametrize(
    ("box", "match"),
    [
        ({"file": "note.pdf", "page": 1, "rect": [0.1, 0.1, 0.1001, 0.2]}, "too small"),
        ({"file": "other.pdf", "page": 1, "rect": [0.1, 0.1, 0.3, 0.2]}, "no document"),
        ({"file": "note.pdf", "page": 0, "rect": [0.1, 0.1, 0.3, 0.2]}, "too small"),
        ({"file": "note.pdf", "rect": [0.1, 0.1, 0.3, 0.2]}, "not valid"),
    ],
)
def test_invalid_boxes_are_refused(ctx, box, match):
    store, proc, cid = ctx
    upload(store, cid, [("note.pdf", pdf("Seen today."))], SITE)
    proc.process_case(cid)
    with pytest.raises(ReviewRefused, match=match):
        change_redactions(store, cid, SITE, boxes=[box])
