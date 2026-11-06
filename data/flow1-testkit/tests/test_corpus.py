"""TC-COR: the synthetic corpus itself is correct (positive controls), so a pipeline that
'passes' cannot do so because the PHI was never there."""
import csv
import json
import re

import pymupdf
import pydicom
import pytest
from openpyxl import load_workbook

from deid_check import sniff
from textmatch import contains


def gt(kit, d):
    return json.loads((kit / d["ground_truth"]).read_text(encoding="utf-8"))


def test_cor01_index_complete(kit, corpus):
    assert len(corpus["documents"]) == 17
    for d in corpus["documents"]:
        assert (kit / d["file"]).exists(), d["file"]
        g = gt(kit, d)
        assert sum(d["counts"].values()) == len(g["items"])
        if d["expected_handling"] == "process":
            assert g["items"] and d["expected_output"]
        for it in g["items"]:
            assert it["requirement"] in ("must_redact", "should_redact", "must_keep") and it["value"]


def test_cor02_text_pdf_items_really_in_text_layer(kit, corpus):
    for d in [d for d in corpus["documents"] if d["variant"] in ("text_pdf", "mixed_pdf_image_header")]:
        with pymupdf.open(kit / d["file"]) as doc:
            pages = [p.get_text() for p in doc]
            meta = " ".join(str(v) for v in doc.metadata.values() if v)
        for it in gt(kit, d)["items"]:
            ch = it["channel"]
            text = meta if ch == "metadata" else pages[int(ch.split(":")[1]) - 1]
            in_image = set(it["locations"]) <= {"image_header"}
            assert contains(text, it["value"]) != in_image, (d["doc_id"], it)


def test_cor03_mixed_pdf_identifiers_only_in_image(kit, corpus):
    d = next(d for d in corpus["documents"] if d["variant"] == "mixed_pdf_image_header")
    with pymupdf.open(kit / d["file"]) as doc:
        assert doc[0].get_images(full=True)
    must = [i for i in gt(kit, d)["items"] if i["requirement"] == "must_redact"]
    assert must and all(i["locations"] == ["image_header"] for i in must)


def test_cor04_pdf_metadata_seeded(kit, corpus, identities):
    d = next(d for d in corpus["documents"] if d["file"].endswith("death_summary.pdf"))
    p = identities[d["subject_id"]]
    with pymupdf.open(kit / d["file"]) as doc:
        md = doc.metadata
    assert p["mrn"] == md["subject"] and p["last"] in md["title"] and p["staff"] == md["author"]


def test_cor05_xlsx_hiding_places(kit, corpus, identities):
    d = next(d for d in corpus["documents"] if d["variant"] == "xlsx")
    p = identities[d["subject_id"]]
    wb = load_workbook(kit / d["file"])
    ws = wb["Results"]
    assert ws.column_dimensions["B"].hidden and ws["B2"].value == f"{p['first']} {p['last']}"
    assert wb["PatientInfo"].sheet_state == "hidden"
    assert p["mobile"] in ws["C2"].comment.text
    assert p["last"] in wb.properties.title and wb.properties.creator == p["staff"]
    assert "PatientDOB" in wb.defined_names
    assert str(ws["I2"].value).startswith("=")


def test_cor06_dicom_phi_locations(kit, corpus, identities):
    d = next(d for d in corpus["documents"] if d["variant"] == "dicom")
    p = identities[d["subject_id"]]
    ds = pydicom.dcmread(kit / d["file"])
    assert str(ds.PatientName) == f"{p['last']}^{p['first']}" and ds.PatientID == p["mrn"]
    assert ds.BurnedInAnnotation == "YES" and ds.ClinicalTrialSubjectID == p["subject_id"]
    assert any(e.tag.is_private and e.value == p["phone"] for e in ds.iterall())


def test_cor07_invalid_and_tricky_files(kit, corpus):
    by_variant = {d["variant"]: kit / d["file"] for d in corpus["documents"]}
    with pymupdf.open(by_variant["encrypted_pdf"]) as doc:
        assert doc.needs_pass
    with pytest.raises(Exception):
        pymupdf.open(by_variant["corrupt_pdf"])
    assert sniff(by_variant["wrong_extension_png"]) == "image" and by_variant["wrong_extension_png"].suffix == ".pdf"
    assert sniff(by_variant["unsupported_txt"]) == "unknown"
    with pymupdf.open(by_variant["image_pdf_rotated_90"]) as doc:
        assert doc[0].get_text().strip() == "" and doc[0].rect.width > doc[0].rect.height


def test_cor08_edc_and_import_files_hold_no_identities(kit, identities):
    files = list((kit / "edc_mock").glob("*.csv")) + list((kit / "inbox_scenarios").glob("*.csv")) \
        + list((kit / "inbox_scenarios").glob("*.json"))
    blob = "\n".join(f.read_text(encoding="utf-8-sig") for f in files)
    for s in identities.values():
        for f in ("first", "last", "mrn", "phone", "email", "address", "insurance"):
            assert not contains(blob, s[f]), (s["subject_id"], f)
        assert not contains(blob, s["dob"])


def test_cor09_identifiers_use_reserved_ranges(identities):
    for s in identities.values():
        assert s["email"].endswith("@example.org")
        if s["locale"] == "gb":
            assert re.fullmatch(r"01632 960\d{3}", s["phone"]) and re.fullmatch(r"07700 900\d{3}", s["mobile"])
            assert s["insurance"].startswith("999 ")


def test_cor10_keep_and_redact_values_do_not_collide(kit, corpus):
    for d in corpus["documents"]:
        items = gt(kit, d)["items"]
        keep = [i["value"] for i in items if i["requirement"] == "must_keep"]
        red = [i["value"] for i in items if i["requirement"] != "must_keep"]
        for k in keep:
            for r in red:
                assert not contains(r, k) and not contains(k, r), (d["doc_id"], k, r)


def test_cor11_edc_subjects_consistent(kit):
    with open(kit / "edc_mock/demographics.csv") as f:
        subjects = {r["subject_id"] for r in csv.DictReader(f)}
    for name in ("ae.csv", "conmeds.csv", "labs.csv", "medical_history.csv"):
        with open(kit / "edc_mock" / name) as f:
            assert {r["subject_id"] for r in csv.DictReader(f)} <= subjects, name


@pytest.mark.slow
def test_cor12_generator_is_deterministic(kit, tmp_path):
    import subprocess, sys
    subprocess.run([sys.executable, str(kit / "tools/make_synthetic.py"), "--out", str(tmp_path), "--no-ocr"],
                   check=True, capture_output=True)
    for rel in ["edc_mock/ae.csv", "edc_mock/labs.csv", "inbox_scenarios/01_initial.csv",
                "inbox_scenarios/03_updates.csv", "expected/intake_expected.json", "identities/identities_SYNTHETIC.json"]:
        assert (tmp_path / rel).read_bytes() == (kit / rel).read_bytes(), rel
