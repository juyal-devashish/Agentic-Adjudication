"""TC-HAR: the scoring harness must catch leaks, never call unproven content 'removed', and
pass a correct redaction. Uses the two reference redactors (null = no redaction, oracle =
redaction from ground truth, text layers only)."""
import json
import shutil

import pytest

import reference_redactors as rr
from conftest import needs_ocr
from deid_check import score
from textmatch import contains


@pytest.fixture(scope="module")
def outputs(tmp_path_factory, kit):
    base = tmp_path_factory.mktemp("redacted")
    rr.run(kit, base / "null", "null")
    rr.run(kit, base / "oracle", "oracle")
    return base


def items(res, **kw):
    return [i for i in res["items"] if all(i[k] == v for k, v in kw.items())]


def test_har01_null_redactor_fails_gate(kit, outputs):
    res = score(kit, outputs / "null", ocr=False)
    g = res["summary"]["gate"]
    assert not g["passed"] and not g["text_pdf_must_redact_all_removed"] and not g["no_identifiers_in_metadata"]
    assert all(i["status"] == "residual" for i in items(res, variant="text_pdf", requirement="must_redact"))


def test_har02_oracle_passes_gate(kit, outputs):
    res = score(kit, outputs / "oracle", ocr=False)
    assert res["summary"]["gate"]["passed"]
    for v in ("text_pdf", "xlsx"):
        assert all(i["status"] == "removed" for i in items(res, variant=v, requirement="must_redact")), v
        assert all(i["status"] == "preserved" for i in items(res, variant=v, requirement="must_keep")), v
    dicom_header = [i for i in items(res, variant="dicom") if i["channel"] == "dicom_header"]
    assert all(i["status"] == ("preserved" if i["requirement"] == "must_keep" else "removed") for i in dicom_header)


def test_har03_without_ocr_image_content_is_never_called_removed(kit, outputs):
    res = score(kit, outputs / "oracle", ocr=False)
    image_variants = {"scan_png_300dpi", "scan_png_150dpi", "scan_jpg_150dpi_lowq", "image_pdf_200dpi",
                      "image_pdf_rotated_90", "wrong_extension_png"}
    img = [i for i in res["items"] if i["variant"] in image_variants or i["channel"] == "pixels"
           or i["locations"] == ["image_header"]]
    assert img and all(i["status"] == "unverified" for i in img)


def test_har04_leaked_output_for_rejected_document_fails_gate(kit, outputs, tmp_path, corpus):
    out = tmp_path / "leak"
    shutil.copytree(outputs / "oracle", out)
    enc = next(d for d in corpus["documents"] if d["variant"] == "encrypted_pdf")
    shutil.copy(kit / enc["file"], out / enc["case_key"])
    res = score(kit, out, ocr=False)
    assert not res["summary"]["gate"]["rejected_documents_have_no_output"]


def test_har05_missing_output_counts_as_failure(kit, outputs, tmp_path, corpus):
    out = tmp_path / "missing"
    shutil.copytree(outputs / "oracle", out)
    d = next(d for d in corpus["documents"] if d["variant"] == "text_pdf")
    (out / d["case_key"] / d["expected_output"]).unlink()
    res = score(kit, out, ocr=False)
    assert not res["summary"]["gate"]["passed"]
    assert {i["status"] for i in items(res, doc_id=d["doc_id"])} == {"missing"}


def test_har06_reference_intake_checks(outputs, corpus):
    log = json.loads((outputs / "oracle" / "processing_log.json").read_text())
    want = {d["doc_id"]: d["expected_handling"] for d in corpus["documents"]}
    for doc_id, handling in want.items():
        if handling == "process":
            assert log[doc_id]["status"] == "processed", doc_id
        else:
            assert log[doc_id] == {"status": "rejected", "reason": handling.split(":")[1]}, doc_id
    png = next(d["doc_id"] for d in corpus["documents"] if d["variant"] == "wrong_extension_png")
    assert log[png]["extension_mismatch"] is True


@needs_ocr
@pytest.mark.ocr
def test_har07_ocr_finds_phi_left_in_scans(kit, outputs, corpus):
    ids = {d["doc_id"] for d in corpus["documents"] if d["variant"] in ("scan_png_300dpi", "image_pdf_rotated_90")}
    res = score(kit, outputs / "null", ocr=True, only=ids)
    for doc_id in ids:
        must = items(res, doc_id=doc_id, requirement="must_redact")
        assert sum(i["status"] == "residual" for i in must) >= 2, doc_id   # incl. the rotated page
        assert not any(i["status"] in ("removed", "unverified") for i in must), doc_id


@needs_ocr
@pytest.mark.ocr
def test_har08_text_only_redaction_misses_image_banner(kit, outputs, corpus):
    d = next(d for d in corpus["documents"] if d["variant"] == "mixed_pdf_image_header")
    res = score(kit, outputs / "oracle", ocr=True, only={d["doc_id"]})
    banner = [i for i in items(res, doc_id=d["doc_id"], requirement="must_redact")]
    assert banner and all(i["status"] == "residual" for i in banner)


def test_har09_matching_rules():
    assert contains("Recorded 14/03/2026 08:52", "14/03/2026")
    assert contains("Tel 01632960123", "01632 960123")
    assert contains("geb. 14.03.1956", "14/03/1956")
    assert contains("SMITH^JOHN", "Smith^John")
    assert not contains("Johnsmith", "John Smith")
    assert not contains("MRN 12345678", "1234567")
