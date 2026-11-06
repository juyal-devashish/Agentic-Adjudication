"""Step 4 unit tests: matching parity with the kit's scorer, upload checks, detectors."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pymupdf
import pytest
from openpyxl import Workbook, load_workbook

from adjud.deid.detect import KnownValues, detect, learn, merge_spans
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy
from adjud.deid.sniff import check_upload
from adjud.deid.textmatch import contains
from adjud.deid.xlsx import REDACTED, learn_xlsx, redact_xlsx

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
POLICY = Policy.for_study(ROOT / "data" / "config", "DEMO01")


def kit_textmatch():
    spec = importlib.util.spec_from_file_location("kit_textmatch", KIT / "tools" / "textmatch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def redacted(text: str, known: KnownValues | None = None) -> str:
    known = known or KnownValues()
    unit = TextUnit.from_string(text)
    learn(unit, POLICY, known)
    for s, e in sorted(merge_spans(detect(unit, POLICY, known)), reverse=True):
        text = text[:s] + "█" * (e - s) + text[e:]
    return text


# --- matching ------------------------------------------------------------------------------


def test_matcher_agrees_with_the_kit_scorer_on_every_ground_truth_value():
    kit = kit_textmatch()
    index = json.loads((KIT / "ground_truth" / "corpus_index.json").read_text())
    checked = 0
    for d in index["documents"]:
        if d["variant"] != "text_pdf":
            continue
        with pymupdf.open(KIT / d["file"]) as doc:
            texts = [p.get_text() for p in doc] + [str(v) for v in doc.metadata.values() if v]
        items = json.loads((KIT / d["ground_truth"]).read_text())["items"]
        for text in texts:
            for it in items:
                assert contains(text, it["value"]) == kit.contains(text, it["value"]), it["value"]
                checked += 1
    assert checked > 100


@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("DOB 24.07.1955.", "24/07/1955", True),
        ("call 01632960001", "01632 960001", True),
        ("Mr ROBERTS", "Roberts", True),
        ("Robertson", "Roberts", False),
        ("14/03/2026 08:52", "14/03/2026", True),
    ],
)
def test_matching_rules(text, value, expected):
    assert contains(text, value) is expected


# --- upload checks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("befund_verschluesselt.pdf", "ENCRYPTED"),
        ("ekg_beschaedigt.pdf", "CORRUPT"),
        ("notizen.txt", "UNSUPPORTED_TYPE"),
    ],
)
def test_bad_files_are_rejected(name, code):
    content = (KIT / "documents" / "DEMO01-104-0006_AE-007" / name).read_bytes()
    assert check_upload(name, content).rejected == code


def test_png_named_pdf_is_an_image_with_a_warning():
    content = (KIT / "documents" / "DEMO01-104-0006_AE-007" / "roentgen_thorax.pdf").read_bytes()
    check = check_upload("roentgen_thorax.pdf", content)
    assert check.kind == "image" and check.rejected is None
    assert any(w.startswith("EXTENSION_MISMATCH") for w in check.warnings)


def test_oversized_file_is_rejected():
    assert check_upload("a.pdf", b"%PDF" + b"0" * 2_000_000, max_file_mb=1).rejected == "TOO_LARGE"


# --- detectors -------------------------------------------------------------------------------


def test_banner_label_values_and_their_propagation():
    text = "Patient:\tROBERTS, Philip\tDOB:\t24/07/1955\nMr Philip Roberts was seen. P. Roberts"
    out = redacted(text)
    for value in ("ROBERTS", "Philip", "24/07/1955", "P. Roberts"):
        assert value not in out, out
    assert "Mr" in out and "was seen" in out


def test_german_labels():
    out = redacted(
        "Herr Carlo Stiffel, geb. 18.10.1959, wurde am 08.01.2026 aufgenommen. "
        "Pat.-Nr. 8859641 Versichertennr.: J769144633"
    )
    assert "18.10.1959" not in out and "8859641" not in out and "J769144633" not in out
    assert "08.01.2026" in out  # dob_only: clinical date kept


def test_clinical_dates_drug_and_eponyms_are_kept():
    text = (
        "Admitted on 14/03/2026 with chest pain; Zelvoran or placebo. "
        "No Wolff-Parkinson-White syndrome. Holter monitoring. TIMI flow 0."
    )
    assert redacted(text) == text


def test_surname_matches_only_as_a_name_not_as_a_word():
    known = KnownValues()
    known.add("PATIENT_NAME", "WARD, Graeme")
    out = redacted("Graeme Ward was moved to the cardiac ward by G. Ward's wife.", known)
    assert "Ward" not in out.replace("ward", "") and "cardiac ward" in out


def test_allow_listed_trial_id_is_never_redacted():
    assert "DEMO01-101-0001" in redacted("Patient ID:\tDEMO01-101-0001")


# --- spreadsheets ----------------------------------------------------------------------------


def test_lab_reference_column_is_not_treated_as_an_identifier():
    wb = Workbook()
    ws = wb.active
    ws.append(["Patient Name", "Test", "Value", "Reference"])
    ws.append(["Philip Roberts", "TROPONIN_T", 1240, "< 14"])
    buf = io.BytesIO()
    wb.save(buf)
    known = KnownValues()
    learn_xlsx(buf.getvalue(), POLICY, known)
    out = load_workbook(io.BytesIO(redact_xlsx(buf.getvalue(), POLICY, known).output)).active
    assert [c.value for c in out[2]] == [REDACTED, "TROPONIN_T", 1240, "< 14"]


@pytest.mark.parametrize(
    "text",
    [
        "Wexmoor Road, Halbridge HB4 7QT · Department of Cardiology\nnext line",
        "Wexmoor Road, Halbridge HB4 7QT - Cardiac Catheter Laboratory",  # OCR read "·" as "-"
        "Wexmoor Road, Halbridge HB4 7QT\tDepartment of Cardiology",
    ],
)
def test_letterhead_address_segment_stops_at_separators_and_line_ends(text):
    from adjud.deid.detect import detect_address_segments

    found = [f.text for f in detect_address_segments(TextUnit.from_string(text), POLICY)]
    assert found == ["Wexmoor Road, Halbridge HB4 7QT"]


def test_name_after_a_label_stops_at_the_next_label():
    out = redacted("Name: DOB: 12/05/1957")
    assert "DOB" in out and "12/05/1957" not in out


def test_recent_date_after_a_birth_label_is_not_a_date_of_birth():
    assert "08.01.2026" in redacted("geb. 08.01.2026 aufgenommen")


def test_a_word_followed_by_a_colon_is_a_label_not_a_name():
    assert redacted("Patient:\tDOS:\nMRN:") == "Patient:\tDOS:\nMRN:"
    assert "ROBERTS" not in redacted("Patient:\tROBERTS, Philip\tTel: 01632 960001")
