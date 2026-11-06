#!/usr/bin/env python3
"""Generate the synthetic Flow 1 test kit: study configuration, mock EDC, AE import scenarios,
expected intake results, source documents with seeded fake PHI, and ground truth.

Everything is SYNTHETIC. Names, addresses and numbers are invented (Faker) or taken from
reserved test ranges: UK drama phone numbers (01632 960xxx, 07700 900xxx), NHS test numbers
(999 xxx xxxx) and example.org e-mail domains. MedDRA-style codes are synthetic (9xxxxxxx),
not real MedDRA.

Usage:  python tools/make_synthetic.py --out .            (regenerate in place)
        python tools/make_synthetic.py --out . --no-ocr   (skip OCR visibility baseline)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import random
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import numpy as np
import yaml
from faker import Faker
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textmatch import contains  # noqa: E402

SEED = 20261105
STUDY = "DEMO01"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

# --------------------------------------------------------------------------------------
# Synthetic dictionary (NOT MedDRA)
# --------------------------------------------------------------------------------------
SOC = {"CARD": ("90010001", "Cardiac disorders (synthetic)"),
       "NERV": ("90010002", "Nervous system disorders (synthetic)"),
       "GAST": ("90010003", "Gastrointestinal disorders (synthetic)"),
       "INFE": ("90010004", "Infections (synthetic)")}
PT = {  # key: (pt_code, term, soc_key, adjudicable)
    "MI":   ("90000101", "Myocardial infarction", "CARD", True),
    "UA":   ("90000102", "Unstable angina", "CARD", True),
    "STRK": ("90000201", "Ischaemic stroke", "NERV", True),
    "TIA":  ("90000202", "Transient ischaemic attack", "NERV", True),
    "SCD":  ("90000301", "Sudden cardiac death", "CARD", True),
    "HEAD": ("90000401", "Headache", "NERV", False),
    "NAUS": ("90000402", "Nausea", "GAST", False),
    "PNEU": ("90000403", "Pneumonia", "INFE", False),
    "AF":   ("90000404", "Atrial fibrillation", "CARD", False),
}

# --------------------------------------------------------------------------------------
# Subjects (pseudonymised; the EDC never holds names)
# --------------------------------------------------------------------------------------
SUBJECT_PLAN = [  # idx, site, sex, age, first_dose
    (1, "101", "M", 70, "2025-03-10"), (2, "101", "M", 64, "2025-02-17"),
    (3, "102", "M", 77, "2025-04-01"), (4, "102", "F", 68, "2025-06-02"),
    (5, "101", "F", 59, "2025-05-12"), (6, "104", "M", 66, "2025-03-24"),
    (7, "101", "M", 55, "2025-02-03"), (8, "102", "F", 48, "2025-04-14"),
    (9, "104", "F", 72, "2025-05-05"), (10, "101", "M", 61, "2025-07-07"),
    (11, "102", "M", 69, "2025-03-03"), (12, "104", "F", 74, "2025-06-16"),
    (13, "101", "M", 63, "2025-01-20"), (14, "102", "F", 79, "2025-02-24"),
    (15, "104", "M", 58, "2025-04-28"), (16, "101", "F", 51, "2025-05-19"),
    (17, "102", "M", 67, "2025-03-31"), (18, "104", "M", 73, "2025-06-09"),
    (19, "101", "F", 62, "2025-01-27"), (20, "102", "F", 56, "2025-07-21"),
]


def sid(idx: int) -> str:
    site = next(s for i, s, *_ in SUBJECT_PLAN if i == idx)
    return f"{STUDY}-{site}-{idx:04d}"


HOSPITALS = {
    "101": ("St Aldric General Hospital", "Wexmoor Road, Halbridge HB4 7QT"),
    "102": ("Northvale Heart Centre", "12 Fenwick Lane, Corrington CR9 2LP"),
    "104": ("Klinikum Rheinbrück", "Uferstraße 40, 50999 Rheinbrück"),
}

TEMPLATE_COLUMNS = ["study_id", "site_id", "subject_id", "ae_id", "ae_term", "llt_code", "pt_code",
                    "soc_code", "meddra_version", "onset_date", "end_date", "serious",
                    "seriousness_criteria", "outcome", "severity", "relatedness_investigator",
                    "last_modified"]


def ae(idx, ae_id, key, onset, end, serious, crit, outcome, lm, *, term=None, coded=True,
       severity="SEVERE", rel="NOT_RELATED", study=STUDY):
    pt_code, pt_term, soc_key, _ = PT[key]
    return {"study_id": study, "site_id": sid(idx).split("-")[1], "subject_id": sid(idx),
            "ae_id": ae_id, "ae_term": term or pt_term,
            "llt_code": pt_code if coded else "", "pt_code": pt_code if coded else "",
            "soc_code": SOC[soc_key][0] if coded else "",
            "meddra_version": "SYN-1.0" if coded else "",
            "onset_date": onset, "end_date": end, "serious": serious,
            "seriousness_criteria": crit, "outcome": outcome, "severity": severity,
            "relatedness_investigator": rel, "last_modified": lm}


# Initial import: (row, expected_eligible, expected_failed_rules, note)
INITIAL = [
    (ae(1, "AE-001", "MI", "2026-03-14", "2026-03-21", "Y", "HOSPITALISATION", "RECOVERED", "2026-03-22T09:15:00Z"), True, [], "Case A, documents provided"),
    (ae(2, "AE-002", "STRK", "2026-04-02", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-04-05T11:00:00Z", rel="UNLIKELY"), True, [], "Case B, documents provided"),
    (ae(3, "AE-003", "SCD", "2026-05-19", "2026-05-19", "Y", "DEATH", "FATAL", "2026-05-21T08:30:00Z"), True, [], "Case C, documents provided"),
    (ae(3, "AE-004", "MI", "2026-05-17", "2026-05-19", "Y", "HOSPITALISATION", "FATAL", "2026-05-21T08:31:00Z"), True, [], "Same subject, prior event for AE-003"),
    (ae(4, "AE-005", "MI", "2025-05-20", "2025-05-20", "Y", "DEATH", "FATAL", "2025-05-23T10:00:00Z"), True, [], "Onset before first dose but fatal: eligible via R3b. Case D"),
    (ae(5, "AE-006", "UA", "2025-05-12", "2025-05-14", "Y", "HOSPITALISATION", "RECOVERED", "2025-05-15T12:00:00Z"), True, [], "Onset equals first dose date (boundary). Case E"),
    (ae(6, "AE-007", "TIA", "2026-01-08", "2026-01-09", "Y", "HOSPITALISATION", "RECOVERED", "2026-01-12T07:45:00Z"), True, [], "German site. Case F"),
    (ae(7, "AE-008", "MI", "2026-02-02", "2026-02-06", "N", "", "RECOVERED", "2026-02-07T09:00:00Z"), False, ["R1"], "Not serious"),
    (ae(8, "AE-009", "HEAD", "2026-01-15", "2026-01-16", "Y", "HOSPITALISATION", "RECOVERED", "2026-01-17T09:00:00Z", severity="MODERATE"), False, ["R2"], "Not an adjudicable term"),
    (ae(9, "AE-010", "PNEU", "2025-11-20", "2025-12-02", "Y", "HOSPITALISATION", "RECOVERED", "2025-12-03T09:00:00Z"), False, ["R2"], "Not an adjudicable term"),
    (ae(10, "AE-011", "MI", "2025-06-30", "2025-07-03", "Y", "HOSPITALISATION", "RECOVERED", "2025-07-04T09:00:00Z"), False, ["R3"], "Before first dose and not fatal"),
    (ae(11, "AE-012", "STRK", "2026-03-01", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-03-04T09:00:00Z", coded=False), False, ["R2"], "Uncoded: pt_code missing"),
    (ae(12, "AE-013", "NAUS", "2025-09-09", "2025-09-10", "N", "", "RECOVERED", "2025-09-11T09:00:00Z", severity="MILD"), False, ["R1", "R2"], "Not serious, not adjudicable"),
    (ae(13, "AE-014", "MI", "2025-11-03", "2025-11-08", " y ", "HOSPITALISATION", "RECOVERED", "2025-11-09T09:00:00Z"), True, [], "Serious flag ' y ' must normalise to Y"),
    (ae(14, "AE-015", "STRK", "2026-06-11", "2026-06-13", "Y", "DEATH", "fatal", "2026-06-14T09:00:00Z"), True, [], "Outcome 'fatal' must normalise to FATAL"),
    (ae(15, "AE-016", "MI", "2026-02-22", "2026-02-27", "Y", "HOSPITALISATION", "RECOVERED", "2026-02-28T09:00:00Z", term="Infarctus du myocarde aigu \u2013 STEMI"), True, [], "Non-ASCII verbatim term"),
    (ae(16, "AE-017", "HEAD", "2025-08-01", "2025-08-01", "N", "", "RECOVERED", "2025-08-02T09:00:00Z", severity="MILD"), False, ["R1", "R2"], ""),
    (ae(18, "AE-018", "AF", "2026-04-18", "2026-04-20", "Y", "HOSPITALISATION", "RECOVERED", "2026-04-21T09:00:00Z"), False, ["R2"], "Cardiac SOC but not adjudicable"),
    (ae(19, "AE-019", "TIA", "2026-02-10", "2026-02-11", "Y", "HOSPITALISATION", "RECOVERED", "2026-02-12T09:00:00Z"), True, [], ""),
    (ae(20, "AE-020", "PNEU", "2025-10-10", "2025-10-20", "N", "", "RECOVERED", "2025-10-21T09:00:00Z"), False, ["R1", "R2"], ""),
    (ae(2, "AE-021", "HEAD", "2026-04-20", "2026-04-21", "Y", "HOSPITALISATION", "RECOVERED", "2026-04-22T09:00:00Z"), False, ["R2"], "Same subject as case B, after onset"),
    (ae(17, "AE-022", "NAUS", "2025-06-01", "2025-06-02", "N", "", "RECOVERED", "2025-06-03T09:00:00Z", severity="MILD"), False, ["R1", "R2"], ""),
]

# Historical AEs that exist only in the mock EDC (for prior-event lookup)
HISTORY = [
    ae(1, "H-001", "MI", "2025-09-01", "2025-09-05", "Y", "HOSPITALISATION", "RECOVERED", "2025-09-06T09:00:00Z"),   # 194 d before AE-001, same PT
    ae(1, "H-002", "AF", "2025-12-01", "2025-12-02", "N", "", "RECOVERED", "2025-12-03T09:00:00Z"),                  # same SOC
    ae(1, "H-003", "HEAD", "2026-01-10", "2026-01-10", "N", "", "RECOVERED", "2026-01-11T09:00:00Z"),                # other SOC
    ae(1, "H-004", "MI", "2025-03-13", "2025-03-14", "Y", "HOSPITALISATION", "RECOVERED", "2025-03-15T09:00:00Z"),   # 366 d before: outside
    ae(2, "H-005", "STRK", "2025-04-02", "2025-04-04", "Y", "HOSPITALISATION", "RECOVERED", "2025-04-05T09:00:00Z"), # exactly 365 d before: inside
    ae(2, "H-006", "TIA", "2025-04-01", "2025-04-01", "Y", "HOSPITALISATION", "RECOVERED", "2025-04-02T09:00:00Z"),  # 366 d before: outside
]

UPDATES = [  # (row, expected_outcome, expected_eligible_now, note)
    ({**INITIAL[0][0], "end_date": "2026-03-23", "last_modified": "2026-04-02T10:00:00Z"}, "changed", True, "End date corrected; still eligible"),
    ({**INITIAL[5][0], "serious": "N", "seriousness_criteria": "", "last_modified": "2026-04-10T08:00:00Z"}, "changed", False, "Downgraded to non-serious: case flagged, NOT withdrawn"),
    (dict(INITIAL[13][0]), "unchanged", True, "Identical row"),
    ({**INITIAL[7][0], "serious": "Y", "seriousness_criteria": "HOSPITALISATION", "last_modified": "2026-02-20T09:00:00Z"}, "created", True, "Was not eligible, now serious: new case"),
    ({**INITIAL[4][0], "ae_term": "Acute myocardial infarction", "last_modified": "2025-05-01T09:00:00Z"}, "stale", True, "Older last_modified than stored: ignored"),
    (ae(16, "AE-023", "MI", "2026-07-01", "2026-07-04", "Y", "HOSPITALISATION", "RECOVERED", "2026-07-05T09:00:00Z"), "created", True, "New AE"),
]

XLSX_ROWS = [  # rows for the XLSX-format import; dates written as real Excel dates where noted
    (ae(17, "AE-024", "MI", "2026-08-04", "2026-08-09", "Y", "HOSPITALISATION", "RECOVERED", "2026-08-10T09:00:00Z"), True, [], "Dates stored as Excel date cells"),
    (ae(18, "AE-025", "STRK", "2026-05-04", "", "N", "", "RECOVERING", "2026-05-06T09:00:00Z"), False, ["R1"], "Onset stored as text"),
    (ae(20, "AE-026", "TIA", "2026-09-01", "2026-09-01", "Y", "HOSPITALISATION", "RECOVERED", "2026-09-02T09:00:00Z"), True, [], ""),
]

DUPLICATE_ROW = ae(19, "AE-027", "TIA", "2026-02-11", "2026-02-12", "Y", "HOSPITALISATION", "RECOVERED", "2026-02-13T09:00:00Z")
BOM_ROW = ae(12, "AE-028", "MI", "2026-03-30", "2026-04-02", "Y", "HOSPITALISATION", "RECOVERED", "2026-04-03T09:00:00Z")


def invalid_rows():
    good = ae(10, "AE-030", "MI", "2026-01-05", "2026-01-08", "Y", "HOSPITALISATION", "RECOVERED", "2026-01-09T09:00:00Z")
    rows = [
        (good, None),
        ({**ae(11, "AE-031", "MI", "2026-02-01", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-02-03T09:00:00Z"), "onset_date": "31/02/2026"}, ("onset_date", "INVALID_DATE")),
        ({**ae(12, "AE-032", "MI", "2026-02-01", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-02-03T09:00:00Z"), "serious": "maybe"}, ("serious", "INVALID_VALUE")),
        ({**ae(13, "AE-033", "MI", "2026-02-01", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-02-03T09:00:00Z"), "subject_id": "DEMO01-101-0099"}, ("subject_id", "UNKNOWN_SUBJECT")),
        (dict(good), ("ae_id", "DUPLICATE_KEY")),
        ({**ae(14, "AE-034", "MI", "2026-02-01", "", "Y", "", "RECOVERING", "2026-02-03T09:00:00Z")}, ("seriousness_criteria", "MISSING_VALUE")),
        ({**ae(15, "AE-035", "MI", "2026-02-01", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-02-03T09:00:00Z"), "study_id": "DEMO02"}, ("study_id", "WRONG_STUDY")),
    ]
    return rows


def key(r):
    return f"{r['study_id']}|{r['subject_id']}|{r['ae_id']}"


# --------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------
def write_csv(path: Path, rows, columns=TEMPLATE_COLUMNS, *, bom=False, delimiter=","):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter=delimiter, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_yaml(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_config(out: Path):
    c = out / "config" / STUDY
    write_yaml(c / "study.yaml", {
        "study": STUDY, "sites": sorted(HOSPITALS),
        "inbox_poll_minutes": 5,
        "upload_limits": {"max_file_mb": 50, "max_dicom_study_mb": 500, "max_pdf_pages": 200},
        "ai": {"phi_detection": False, "model": "<pinned model id>"},
    })
    write_yaml(c / "eligibility_rules.yaml", {
        "rule_set": "DEMO01-ELIG", "version": 1,
        "context": "AE record merged with the subject's demographics (first_dose_date)",
        "all": [
            {"id": "R1", "description": "Serious adverse event", "field": "serious", "equals": "Y"},
            {"id": "R2", "description": "Preferred term is in the adjudicable list", "field": "pt_code", "in_list": "adjudicable_pts"},
            {"id": "R3", "description": "Treatment-emergent, or fatal", "any": [
                {"id": "R3a", "description": "Onset on or after first dose", "field": "onset_date", "on_or_after_field": "first_dose_date"},
                {"id": "R3b", "description": "Fatal outcome (always adjudicated)", "field": "outcome", "equals": "FATAL"},
            ]},
        ],
        "lists": {"adjudicable_pts": [v[0] for v in PT.values() if v[3]]},
    })
    write_yaml(c / "related_data.yaml", {
        "version": 1,
        "demographics": {"fields": ["sex", "age", "first_dose_date"]},
        "medical_history": {"all": True},
        "conmeds": {"ongoing_at_onset": True},
        "labs": {"tests": ["TROPONIN_T", "CK_MB", "LDL", "HBA1C"], "window_days": 30},
        "prior_events": {"match_on": ["pt_code", "soc_code"], "window_days": 365},
    })
    write_yaml(c / "deid_policy.yaml", {
        "version": 1,
        "date_policy": "dob_only",
        "must_redact": ["PATIENT_NAME", "DOB", "MRN", "INSURANCE_NO", "ADDRESS", "PHONE", "EMAIL",
                        "NEXT_OF_KIN", "CASE_REFERENCE"],
        "should_redact": ["STAFF_NAME", "STAFF_CONTACT", "FACILITY"],
        "allow_list": ["DEMO01-*", "Zelvoran", "Wolff-Parkinson-White", "Holter", "Parkinson",
                       "Hodgkin", "Addison", "NIHSS", "TIMI"],
        "context_labels": ["Name", "Patient", "DOB", "Date of birth", "born", "MRN", "Hospital no",
                           "NHS No", "Address", "Tel", "geb.", "Pat.-Nr.", "Versichertennr."],
    })
    write_yaml(out / "config" / "users.yaml", {
        "note": "Demo users only. Password for every user is 'demo' (bcrypt hash in a real MVP).",
        "users": [
            {"id": "site101.coord", "name": "Site 101 Coordinator", "role": "site", "site": "101"},
            {"id": "site102.coord", "name": "Site 102 Coordinator", "role": "site", "site": "102"},
            {"id": "site104.coord", "name": "Site 104 Coordinator", "role": "site", "site": "104"},
            {"id": "ss.anna", "name": "Anna Safety", "role": "safety_specialist"},
            {"id": "admin.sam", "name": "Sam Admin", "role": "study_admin"},
        ],
    })
    with open(out / "config" / "synthetic_dictionary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pt_code", "pt_term", "soc_code", "soc_term", "adjudicable_in_demo"])
        for pt_code, term, soc_key, adj in PT.values():
            w.writerow([pt_code, term, SOC[soc_key][0], SOC[soc_key][1], "Y" if adj else "N"])


def write_edc(out: Path, rng: random.Random):
    e = out / "edc_mock"
    demo = [{"subject_id": sid(i), "site_id": s, "sex": sex, "age": age, "first_dose_date": fd}
            for i, s, sex, age, fd in SUBJECT_PLAN]
    write_csv(e / "demographics.csv", demo, ["subject_id", "site_id", "sex", "age", "first_dose_date"])

    mh_cols = ["subject_id", "condition", "start_year", "ongoing"]
    mh = [{"subject_id": sid(1), "condition": "Hypertension", "start_year": 2012, "ongoing": "Y"},
          {"subject_id": sid(1), "condition": "Type 2 diabetes mellitus", "start_year": 2018, "ongoing": "Y"}]
    for i, *_ in SUBJECT_PLAN[1:]:
        mh.append({"subject_id": sid(i), "condition": rng.choice(["Hypertension", "Hyperlipidaemia", "Asthma", "Osteoarthritis"]),
                   "start_year": rng.randint(2005, 2022), "ongoing": "Y"})
    write_csv(e / "medical_history.csv", mh, mh_cols)

    cm_cols = ["subject_id", "drug", "dose", "start_date", "end_date"]
    cm = [  # S01 deliberately constructed: expected ongoing at onset 2026-03-14 = Aspirin, Clopidogrel
        {"subject_id": sid(1), "drug": "Aspirin", "dose": "75 mg OD", "start_date": "2025-03-10", "end_date": ""},
        {"subject_id": sid(1), "drug": "Atorvastatin", "dose": "40 mg ON", "start_date": "2025-03-10", "end_date": "2026-01-31"},
        {"subject_id": sid(1), "drug": "Metoprolol", "dose": "25 mg BD", "start_date": "2026-03-16", "end_date": ""},
        {"subject_id": sid(1), "drug": "Clopidogrel", "dose": "75 mg OD", "start_date": "2025-06-01", "end_date": "2026-03-14"},
    ]
    for i, *_ in SUBJECT_PLAN[1:]:
        cm.append({"subject_id": sid(i), "drug": rng.choice(["Ramipril", "Amlodipine", "Metformin", "Simvastatin"]),
                   "dose": "standard", "start_date": "2024-11-01", "end_date": ""})
    write_csv(e / "conmeds.csv", cm, cm_cols)

    lb_cols = ["subject_id", "test", "value", "unit", "collected_date"]
    lb = [  # S01: expected within ±30 days of 2026-03-14 and in test list = 2x TROPONIN_T, HBA1C
        {"subject_id": sid(1), "test": "TROPONIN_T", "value": 1240, "unit": "ng/L", "collected_date": "2026-03-14"},
        {"subject_id": sid(1), "test": "TROPONIN_T", "value": 3860, "unit": "ng/L", "collected_date": "2026-03-16"},
        {"subject_id": sid(1), "test": "LDL", "value": 3.1, "unit": "mmol/L", "collected_date": "2026-02-02"},
        {"subject_id": sid(1), "test": "HBA1C", "value": 58, "unit": "mmol/mol", "collected_date": "2026-03-04"},
        {"subject_id": sid(1), "test": "CK_MB", "value": 41, "unit": "U/L", "collected_date": "2026-04-14"},
        {"subject_id": sid(1), "test": "POTASSIUM", "value": 4.4, "unit": "mmol/L", "collected_date": "2026-03-13"},
    ]
    for i, *_ in SUBJECT_PLAN[1:]:
        lb.append({"subject_id": sid(i), "test": "LDL", "value": round(rng.uniform(1.8, 4.5), 1), "unit": "mmol/L", "collected_date": "2025-09-15"})
    write_csv(e / "labs.csv", lb, lb_cols)

    ae_rows = [r for r, *_ in INITIAL] + HISTORY + [r for r, *_ in XLSX_ROWS] + [DUPLICATE_ROW, BOM_ROW]
    ae_rows = [{**r, "serious": r["serious"].strip().upper(), "outcome": r["outcome"].strip().upper()} for r in ae_rows]
    write_csv(e / "ae.csv", ae_rows)


def write_inbox_scenarios(out: Path):
    d = out / "inbox_scenarios"
    write_csv(d / "01_initial.csv", [r for r, *_ in INITIAL])
    write_csv(d / "02_reimport_identical.csv", [r for r, *_ in INITIAL])
    write_csv(d / "03_updates.csv", [r for r, *_ in UPDATES])
    write_csv(d / "04_invalid_missing_column.csv", [r for r, *_ in INITIAL[:3]],
              [c for c in TEMPLATE_COLUMNS if c != "last_modified"])
    write_csv(d / "05_invalid_rows.csv", [r for r, _ in invalid_rows()])

    wb = Workbook()
    ws = wb.active
    ws.title = "AE_IMPORT"
    ws.append(TEMPLATE_COLUMNS)
    for i, (r, *_) in enumerate(XLSX_ROWS):
        vals = []
        for c in TEMPLATE_COLUMNS:
            v = r[c]
            if c in ("onset_date", "end_date") and v and i != 1:
                v = dt.date.fromisoformat(v)
            if c == "last_modified":
                v = dt.datetime.fromisoformat(v.replace("Z", ""))
            vals.append(v)
        ws.append(vals)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, dt.datetime):
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
            elif isinstance(cell.value, dt.date):
                cell.number_format = "yyyy-mm-dd"
    wb.save(d / "06_xlsx_format.xlsx")

    write_csv(d / "07_duplicate_candidate.csv", [DUPLICATE_ROW])
    write_csv(d / "08_header_only.csv", [])
    write_csv(d / "09_utf8_bom.csv", [BOM_ROW], bom=True)
    write_csv(d / "10_semicolon_delimited.csv", [BOM_ROW], delimiter=";")

    write_json(d / "manual_entry_valid.json", {
        "record": ae(12, "AE-M01", "STRK", "2026-08-20", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-08-21T09:00:00Z"),
        "reason": "Reported by the site by phone; EDC entry pending", "actor": "ss.anna"})
    write_json(d / "manual_entry_missing_reason.json", {
        "record": ae(12, "AE-M02", "STRK", "2026-08-22", "", "Y", "HOSPITALISATION", "RECOVERING", "2026-08-23T09:00:00Z"),
        "reason": "", "actor": "ss.anna"})


def write_expected(out: Path):
    elig = lambda rows: [{"key": key(r), "eligible": e, "failed_rules": f, "note": n} for r, e, f, n in rows]
    expected = {
        "_readme": "Expected intake results. Keys are study|subject|ae_id. Case IDs are assigned by the "
                   "implementation, so cases are identified by key. Row numbers count the header as row 1.",
        "sequence": ["01_initial.csv", "02_reimport_identical.csv", "03_updates.csv",
                     "06_xlsx_format.xlsx", "07_duplicate_candidate.csv", "09_utf8_bom.csv"],
        "scenarios": {
            "01_initial.csv": {"status": "ACCEPTED", "records": len(INITIAL),
                               "new_cases": sum(e for _, e, *_ in INITIAL),
                               "not_eligible": sum(not e for _, e, *_ in INITIAL),
                               "evaluations": elig(INITIAL)},
            "02_reimport_identical.csv": {"status": "ACCEPTED", "records": len(INITIAL), "new_cases": 0,
                                          "changed": 0, "unchanged": len(INITIAL)},
            "03_updates.csv": {"status": "ACCEPTED", "records": len(UPDATES),
                               "outcomes": [{"key": key(r), "outcome": o, "eligible_now": e, "note": n} for r, o, e, n in UPDATES],
                               "new_cases": 2, "changed": 2, "unchanged": 1, "stale": 1,
                               "flags": {key(UPDATES[1][0]): ["source_changed", "no_longer_eligible"],
                                         key(UPDATES[0][0]): ["source_changed"]}},
            "04_invalid_missing_column.csv": {"status": "REJECTED", "records_imported": 0,
                                              "errors": [{"row": 1, "column": "last_modified", "code": "MISSING_COLUMN"}]},
            "05_invalid_rows.csv": {"status": "REJECTED", "records_imported": 0,
                                    "errors": [{"row": i + 2, "column": c, "code": code}
                                               for i, (_, err) in enumerate(invalid_rows()) if err for c, code in [err]]},
            "06_xlsx_format.xlsx": {"status": "ACCEPTED", "records": 3, "new_cases": 2, "evaluations": elig(XLSX_ROWS)},
            "07_duplicate_candidate.csv": {"status": "ACCEPTED", "records": 1, "new_cases": 1,
                                           "flags": {key(DUPLICATE_ROW): ["possible_duplicate"]},
                                           "possible_duplicate_of": {key(DUPLICATE_ROW): key(INITIAL[18][0])}},
            "08_header_only.csv": {"status": "ACCEPTED", "records": 0, "new_cases": 0},
            "09_utf8_bom.csv": {"status": "ACCEPTED", "records": 1, "new_cases": 1},
            "10_semicolon_delimited.csv": {"status": "REJECTED", "records_imported": 0,
                                           "errors_include_code": "MISSING_COLUMN"},
            "manual_entry_valid.json": {"status": "ACCEPTED", "new_cases": 1, "source": "MANUAL"},
            "manual_entry_missing_reason.json": {"status": "REJECTED", "errors_include_code": "MISSING_REASON"},
        },
        "prior_events": {  # after 01_initial (and 07 for AE-027); match type PT or SOC
            key(INITIAL[0][0]): [{"ae_id": "H-001", "match": "PT"}, {"ae_id": "H-002", "match": "SOC"}],
            key(INITIAL[1][0]): [{"ae_id": "H-005", "match": "PT"}],
            key(INITIAL[2][0]): [{"ae_id": "AE-004", "match": "SOC"}],
            key(INITIAL[3][0]): [],
            key(DUPLICATE_ROW): [{"ae_id": "AE-019", "match": "PT"}],
        },
        "related_data": {
            key(INITIAL[0][0]): {"medical_history": 2, "conmeds": ["Aspirin", "Clopidogrel"],
                                 "labs": [["TROPONIN_T", "2026-03-14"], ["TROPONIN_T", "2026-03-16"], ["HBA1C", "2026-03-04"]],
                                 "demographics": {"sex": "M", "age": "70", "first_dose_date": "2025-03-10"}},
        },
    }
    write_json(out / "expected" / "intake_expected.json", expected)


# ======================================================================================
# Identities (exist ONLY in source documents, never in the EDC)
# ======================================================================================
def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def make_identities(rng: random.Random):
    fk = {"gb": Faker("en_GB"), "de": Faker("de_DE")}
    for f in fk.values():
        f.seed_instance(SEED)
    staff = {"101": "Dr Amelia Hart", "102": "Dr Rupert Okafor", "104": "Dr. med. Katrin Albers"}
    ids = {}
    for i, site, sex, age, fd in SUBJECT_PLAN:
        loc = "de" if site == "104" else "gb"
        f = fk[loc]
        first = f.first_name_male() if sex == "M" else f.first_name_female()
        last = f.last_name()
        dob = dt.date(2025 - age, rng.randint(1, 12), rng.randint(1, 28))
        if loc == "gb":
            addr = f"{f.building_number()} {f.street_name()}, {f.city()} {f.postcode()}"
            phone, mobile = f"01632 960{i:03d}", f"07700 900{100 + i:03d}"
            insurance = f"999 {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
        else:
            addr = f"{f.street_address()}, {f.postcode()} {f.city()}"
            phone, mobile = f"+49 221 5550{i:03d}", f"+49 170 5550{100 + i:03d}"
            insurance = f"{chr(65 + rng.randint(0, 25))}{rng.randint(100000000, 999999999)}"
        mrn = {"101": f"{rng.randint(1000000, 9999999)}",
               "102": f"HN-{chr(65 + rng.randint(0, 25))}{chr(65 + rng.randint(0, 25))}{rng.randint(10000, 99999)}",
               "104": f"{rng.randint(8800000, 8899999)}"}[site]
        kin_first = f.first_name_female() if sex == "M" else f.first_name_male()
        ids[i] = {"subject_id": sid(i), "site": site, "locale": loc, "sex": sex,
                  "first": first, "last": last, "dob": dob, "address": addr, "phone": phone,
                  "mobile": mobile, "email": f"{ascii_fold(first)}.{ascii_fold(last)}{i}@example.org".lower(),
                  "mrn": mrn, "insurance": insurance, "kin": f"{kin_first} {last}",
                  "hospital": HOSPITALS[site][0], "hospital_addr": HOSPITALS[site][1],
                  "staff": staff[site]}
    return ids


def dmy(d: dt.date, sep="/"):
    return d.strftime(f"%d{sep}%m{sep}%Y")


# ======================================================================================
# PDF rendering (reportlab) and rasterising (PyMuPDF + Pillow)
# ======================================================================================
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader, simpleSplit  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402
import pymupdf  # noqa: E402

try:
    pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    FONT, FONT_B = "Body", "Bold"
except Exception:  # fall back to built-in fonts (Latin-1 only)
    FONT, FONT_B = "Helvetica", "Helvetica-Bold"

W, H = A4
LM, RM = 20 * mm, W - 20 * mm


def _font(size, bold=False):
    path = FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def draw_blocks(c, blocks, y):
    for b in blocks:
        kind = b[0]
        if kind == "title":
            c.setFont(FONT_B, 14); c.drawString(LM, y, b[1]); y -= 9 * mm
        elif kind == "h":
            c.setFont(FONT_B, 11); c.drawString(LM, y, b[1]); y -= 6 * mm
        elif kind == "p":
            c.setFont(FONT, 10)
            for line in simpleSplit(b[1], FONT, 10, RM - LM):
                c.drawString(LM, y, line); y -= 4.6 * mm
            y -= 2.5 * mm
        elif kind == "kv":
            for k, v in b[1]:
                c.setFont(FONT_B, 10); c.drawString(LM, y, f"{k}:")
                c.setFont(FONT, 10); c.drawString(LM + 42 * mm, y, v); y -= 5 * mm
            y -= 2 * mm
        elif kind == "table":
            rows, widths = b[1], b[2]
            for ri, row in enumerate(rows):
                x = LM
                c.setFont(FONT_B if ri == 0 else FONT, 9.5)
                for cell, w_ in zip(row, widths):
                    c.drawString(x + 1.5 * mm, y, str(cell)); x += w_ * mm
                c.setLineWidth(0.3); c.line(LM, y - 1.8 * mm, LM + sum(widths) * mm, y - 1.8 * mm)
                y -= 6 * mm
            y -= 3 * mm
        elif kind == "img":
            img, width_mm = b[1], b[2]
            h_mm = width_mm * img.height / img.width
            c.drawImage(ImageReader(img), LM, y - h_mm * mm + 4 * mm, width_mm * mm, h_mm * mm)
            y -= h_mm * mm + 4 * mm
        elif kind == "sp":
            y -= b[1] * mm
    return y


def render_pdf(path: Path, pages, *, letterhead=None, footer=None, metadata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(path), pagesize=A4)
    md = metadata or {}
    c.setAuthor(md.get("author", "")); c.setTitle(md.get("title", ""))
    c.setSubject(md.get("subject", "")); c.setKeywords(md.get("keywords", ""))
    c.setCreator("synthetic-generator")
    for pno, blocks in enumerate(pages, start=1):
        y = H - 18 * mm
        if letterhead:
            name, addr, dept = letterhead
            c.setFont(FONT_B, 12); c.drawString(LM, y, name)
            c.setFont(FONT, 8.5); c.drawString(LM, y - 4.5 * mm, f"{addr}  ·  {dept}")
            c.setLineWidth(0.8); c.line(LM, y - 7 * mm, RM, y - 7 * mm)
            y -= 15 * mm
        draw_blocks(c, blocks, y)
        if footer:
            c.setFont(FONT, 8); c.drawString(LM, 12 * mm, footer(pno, len(pages)))
        c.showPage()
    c.save()


def page_image(pdf: Path, page_no=0, dpi=300) -> Image.Image:
    with pymupdf.open(pdf) as doc:
        pix = doc[page_no].get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")


def scanify(img: Image.Image, rng: np.random.Generator, angle=0.0, noise=6.0, blur=0.0, rotate90=False):
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    if rotate90:
        img = img.rotate(90, expand=True)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(img, dtype=np.float32)
    arr += rng.normal(0, noise, arr.shape)
    speckle = rng.random(arr.shape) < 0.0008
    arr[speckle] = 40
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def image_only_pdf(img: Image.Image, path: Path, dpi: int):
    buf = io.BytesIO(); img.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=img.width * 72 / dpi, height=img.height * 72 / dpi)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def text_banner(lines, width=1600, size=30):
    font, bold = _font(size), _font(size + 4, True)
    h = 26 + (size + 16) * len(lines)
    img = Image.new("L", (width, h), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width - 1, h - 1], outline=0, width=3)
    y = 14
    for i, ln in enumerate(lines):
        d.text((24, y), ln, fill=0, font=bold if i == 0 else font)
        y += size + 16
    return img


# ======================================================================================
# Ground truth helpers
# ======================================================================================
MUST = {"PATIENT_NAME", "DOB", "MRN", "INSURANCE_NO", "ADDRESS", "PHONE", "EMAIL", "NEXT_OF_KIN", "CASE_REFERENCE"}
SHOULD = {"STAFF_NAME", "STAFF_CONTACT", "FACILITY"}
KEEP = {"SUBJECT_ID", "CLINICAL_DATE", "DRUG", "EPONYM", "LAB_VALUE", "CLINICAL_TERM"}
IMAGE_LOCATIONS = {"image_header", "burned_in", "scan"}


def requirement(cat):
    return "must_redact" if cat in MUST else "should_redact" if cat in SHOULD else "must_keep"


class Truth:
    """Collects ground-truth items for one document, merged by (value, channel)."""

    def __init__(self):
        self.items: dict[tuple, dict] = {}

    def add(self, value, category, location, channel="page:1"):
        assert category in MUST | SHOULD | KEEP, category
        k = (str(value), channel)
        if k in self.items:
            if location not in self.items[k]["locations"]:
                self.items[k]["locations"].append(location)
            return
        self.items[k] = {"value": str(value), "category": category, "requirement": requirement(category),
                         "channel": channel, "locations": [location]}

    def page(self, n, pairs, location):
        for value, cat in pairs:
            self.add(value, cat, location, f"page:{n}")

    def as_list(self):
        out = []
        for i, it in enumerate(self.items.values(), start=1):
            out.append({"id": f"I{i:03d}", **it})
        return out


def ocr_text(img: Image.Image) -> str | None:
    try:
        import pytesseract
        return pytesseract.image_to_string(img, lang="eng")
    except Exception:
        return None


def check_text_pdf(path: Path, items):
    """Generator self-check: every text-layer item must really be on its page (or in metadata);
    every image-only item must NOT be in the text layer."""
    with pymupdf.open(path) as doc:
        pages = [p.get_text() for p in doc]
        meta = " ".join(str(v) for v in (doc.metadata or {}).values() if v)
    for it in items:
        ch = it["channel"]
        if ch == "metadata":
            assert contains(meta, it["value"]), (path.name, it)
        elif ch.startswith("page:"):
            text = pages[int(ch.split(":")[1]) - 1]
            if set(it["locations"]) <= IMAGE_LOCATIONS:
                assert not contains(text, it["value"]), ("image item leaked into text layer", path.name, it)
            else:
                assert contains(text, it["value"]), ("missing from text layer", path.name, it)


def mark_ocr(items, page_imgs: dict[int, Image.Image], use_ocr: bool):
    """Record whether OCR of the ORIGINAL can see each image-borne item (baseline only)."""
    cache = {}
    for it in items:
        ch = it["channel"]
        n = int(ch.split(":")[1]) if ch.startswith("page:") else (1 if ch == "pixels" else None)
        if n is None or n not in page_imgs:
            continue
        if not use_ocr:
            it["ocr_visible_in_original"] = None
            continue
        if n not in cache:
            cache[n] = ocr_text(page_imgs[n])
        it["ocr_visible_in_original"] = None if cache[n] is None else contains(cache[n], it["value"])


# ======================================================================================
# Source documents
# ======================================================================================
def name_forms(p):
    title = "Mr" if p["sex"] == "M" else "Mrs"
    return (f"{p['last'].upper()}, {p['first']}", f"{p['first']} {p['last']}",
            f"{p['first'][0]}. {p['last']}", title)


def letterhead(p, dept):
    return (p["hospital"], p["hospital_addr"], dept)


def add_letterhead_truth(t: Truth, p, pages):
    for n in range(1, pages + 1):
        t.page(n, [(p["hospital"], "FACILITY"), (p["hospital_addr"], "FACILITY")], "letterhead")


def doc_entry(case_key, p, rel, variant, items, *, language="en", handling="process", expected_output=None):
    return {"case_key": case_key, "subject_id": p["subject_id"], "file": rel, "variant": variant,
            "language": language, "expected_handling": handling,
            "expected_output": expected_output if handling == "process" else None, "items": items}


def case_a(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-001"
    d = out / "documents" / ck
    hdr, body, foot, mr = name_forms(p)
    dob = dmy(p["dob"])
    docs = []

    # A1 text PDF, 2 pages, identifiers in banner, body, table, footer
    pages = [[
        ("kv", [("Patient", hdr), ("Date of birth", dob), ("MRN", p["mrn"]), ("NHS No", p["insurance"]),
                ("Address", p["address"]), ("Tel", p["phone"]), ("Trial ID", p["subject_id"])]),
        ("title", "DISCHARGE SUMMARY"),
        ("p", f"{mr} {body} was admitted on 14/03/2026 with central chest pain of two hours' duration. The ECG on "
              "arrival showed ST elevation in leads II, III and aVF. He takes part in the DEMO01 study and receives "
              "blinded study medication (Zelvoran or placebo)."),
        ("p", "Past medical history: hypertension and type 2 diabetes mellitus. There is no history of "
              "Wolff-Parkinson-White syndrome."),
        ("h", "Laboratory results"),
        ("table", [["Test", "Date", "Result", "Reference"], ["Troponin T", "14/03/2026", "1240 ng/L", "< 14"],
                   ["Troponin T", "16/03/2026", "3860 ng/L", "< 14"], ["HbA1c", "04/03/2026", "58 mmol/mol", "20-42"]],
         [45, 35, 40, 40]),
    ], [
        ("p", "Coronary angiography on 15/03/2026 showed an occluded right coronary artery, treated with a "
              "drug-eluting stent. 24-hour Holter monitoring showed no significant arrhythmia."),
        ("p", f"He was discharged home on 21/03/2026 on aspirin 75 mg, clopidogrel 75 mg, atorvastatin 80 mg and "
              f"bisoprolol 2.5 mg. Next of kin: {p['kin']}, mobile {p['mobile']}. Patient e-mail for follow-up "
              f"letters: {p['email']}."),
        ("p", f"Signed: {p['staff']}, Consultant Cardiologist"),
    ]]
    f = d / "discharge_summary.pdf"
    render_pdf(f, pages, letterhead=letterhead(p, "Department of Cardiology"),
               footer=lambda n, N: f"{foot}  |  MRN {p['mrn']}  |  Page {n} of {N}",
               metadata={"title": "Discharge summary"})
    t = Truth()
    add_letterhead_truth(t, p, 2)
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["insurance"], "INSURANCE_NO"),
               (p["address"], "ADDRESS"), (p["phone"], "PHONE"), (p["subject_id"], "SUBJECT_ID")], "banner")
    t.page(1, [(body, "PATIENT_NAME"), ("14/03/2026", "CLINICAL_DATE"), ("Zelvoran", "DRUG"),
               ("Wolff-Parkinson-White", "EPONYM")], "body")
    t.page(1, [("1240 ng/L", "LAB_VALUE"), ("16/03/2026", "CLINICAL_DATE")], "table")
    for n in (1, 2):
        t.page(n, [(foot, "PATIENT_NAME"), (p["mrn"], "MRN")], "footer")
    t.page(2, [("15/03/2026", "CLINICAL_DATE"), ("Holter", "EPONYM"), ("21/03/2026", "CLINICAL_DATE"),
               (p["kin"], "NEXT_OF_KIN"), (p["mobile"], "PHONE"), (p["email"], "EMAIL")], "body")
    t.page(2, [(p["staff"], "STAFF_NAME")], "signature")
    items = t.as_list(); check_text_pdf(f, items)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "text_pdf", items, expected_output=f.name))

    # A2 text PDF, identifiers inside a table
    pages = [[
        ("title", "12-LEAD ECG REPORT"),
        ("table", [["Field", "Value"], ["Name", body], ["Date of birth", dob], ["Hospital no", p["mrn"]],
                   ["Recorded", "14/03/2026 08:52"], ["Trial ID", p["subject_id"]]], [45, 100]),
        ("p", "Sinus rhythm, 92 bpm. ST elevation of 3 mm in II, III and aVF with reciprocal depression in I and "
              "aVL. No delta wave. Interpretation: acute inferior ST-elevation myocardial infarction."),
        ("p", f"Reported by {p['staff']}"),
    ]]
    f = d / "ecg_report.pdf"
    render_pdf(f, pages, letterhead=letterhead(p, "Cardiac Physiology"))
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(body, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), ("14/03/2026", "CLINICAL_DATE"),
               (p["subject_id"], "SUBJECT_ID")], "table")
    t.page(1, [(p["staff"], "STAFF_NAME")], "signature")
    items = t.as_list(); check_text_pdf(f, items)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "text_pdf", items, expected_output=f.name))

    # A3 XLSX with hidden column, hidden sheet, comment, properties, formula, defined name
    f = d / "troponin_results.xlsx"
    wb = Workbook()
    ws = wb.active; ws.title = "Results"
    ws.append(["Sample ID", "Patient Name", "Hospital no", "Collected", "Test", "Value", "Unit", "Reference", "Label", "Trial ID"])
    samples = [("2026-03-14 07:55", "TROPONIN_T", 1240, "ng/L", "< 14"), ("2026-03-14 13:10", "TROPONIN_T", 2410, "ng/L", "< 14"),
               ("2026-03-16 08:05", "TROPONIN_T", 3860, "ng/L", "< 14"), ("2026-03-04 09:30", "HBA1C", 58, "mmol/mol", "20-42")]
    for i, (ts, test, val, unit, ref) in enumerate(samples, start=2):
        ws.append([f"S26-{4410 + i}", body, p["mrn"], dt.datetime.fromisoformat(ts), test, val, unit, ref,
                   f'=B{i}&" / "&C{i}', p["subject_id"]])
        ws.cell(row=i, column=4).number_format = "yyyy-mm-dd hh:mm"
    ws.column_dimensions["B"].hidden = True
    ws["C2"].comment = Comment(f"Result phoned to patient on {p['mobile']}", "Lab")
    info = wb.create_sheet("PatientInfo")
    for row in [["Name", body], ["Date of birth", dob], ["Address", p["address"]], ["Telephone", p["phone"]]]:
        info.append(row)
    info.sheet_state = "hidden"
    wb.defined_names["PatientDOB"] = DefinedName("PatientDOB", attr_text="PatientInfo!$B$2")
    wb.properties.creator = p["staff"]
    wb.properties.title = f"Troponin results - {body}"
    f.parent.mkdir(parents=True, exist_ok=True)
    wb.save(f)
    recalculated = recalc_xlsx(f)
    t = Truth()
    t.add(body, "PATIENT_NAME", "hidden_column", "hidden")
    t.add(dob, "DOB", "hidden_sheet", "hidden")
    t.add(p["address"], "ADDRESS", "hidden_sheet", "hidden")
    t.add(p["phone"], "PHONE", "hidden_sheet", "hidden")
    t.add(p["mrn"], "MRN", "visible_cell", "cells")
    if recalculated:
        t.add(body, "PATIENT_NAME", "formula_cached_value", "cells")
    t.add(p["mobile"], "PHONE", "cell_comment", "comments")
    t.add(body, "PATIENT_NAME", "document_properties", "properties")
    t.add(p["staff"], "STAFF_NAME", "document_properties", "properties")
    t.add(p["subject_id"], "SUBJECT_ID", "visible_cell", "cells")
    t.add("1240", "LAB_VALUE", "visible_cell", "cells")
    t.add("TROPONIN_T", "CLINICAL_TERM", "visible_cell", "cells")
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "xlsx", t.as_list(), expected_output=f.name))
    return docs


def recalc_xlsx(path: Path) -> bool:
    script = Path("/mnt/skills/public/xlsx/scripts/recalc.py")
    if not script.exists() or not shutil.which("soffice"):
        print(f"  note: {path.name} not recalculated (no LibreOffice); formula cached values absent")
        return False
    r = subprocess.run([sys.executable, str(script), str(path), "60"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("status") == "success"
    except Exception:
        return False


def case_b(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-002"
    d = out / "documents" / ck
    hdr, body, foot, mr = name_forms(p)
    dob = dmy(p["dob"])
    docs = []
    src = out / ".build" / "b_source.pdf"
    render_pdf(src, [[
        ("kv", [("Name", hdr), ("DOB", dob), ("MRN", p["mrn"]), ("Address", p["address"]), ("Tel", p["phone"]),
                ("Trial ID", p["subject_id"])]),
        ("title", "DISCHARGE SUMMARY - STROKE UNIT"),
        ("p", f"{mr} {body} was admitted on 02/04/2026 with sudden left-sided weakness and slurred speech "
              "(NIHSS 8). CT of the head showed no haemorrhage and he received intravenous thrombolysis. MRI on "
              "03/04/2026 confirmed an acute right middle cerebral artery infarct."),
        ("p", "Study medication (Zelvoran or placebo) was paused and restarted on 07/04/2026. NIHSS at discharge was 2."),
        ("p", f"Discharged on 09/04/2026 to the stroke clinic. {p['staff']}, Stroke Physician"),
    ]], letterhead=letterhead(p, "Stroke Unit"))
    img = scanify(page_image(src, dpi=300), rng_np, angle=0.8, noise=6)
    f = d / "discharge_summary_scan.png"; f.parent.mkdir(parents=True, exist_ok=True)
    img.save(f, dpi=(300, 300))
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["address"], "ADDRESS"),
               (p["phone"], "PHONE"), (p["subject_id"], "SUBJECT_ID")], "scan")
    t.page(1, [(body, "PATIENT_NAME"), ("02/04/2026", "CLINICAL_DATE"), ("NIHSS", "CLINICAL_TERM"),
               ("Zelvoran", "DRUG"), ("09/04/2026", "CLINICAL_DATE"), (p["staff"], "STAFF_NAME")], "scan")
    items = t.as_list(); mark_ocr(items, {1: img}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "scan_png_300dpi", items, expected_output="discharge_summary_scan.pdf"))

    # B2 DICOM with PHI in header, a private tag and burned-in pixels
    f = d / "ct_head.dcm"
    burned = [f"{p['last'].upper()}^{p['first'].upper()}", f"MRN {p['mrn']}", f"DOB {p['dob'].isoformat()}"]
    pix_img = make_dicom(f, p, burned)
    t = Truth()
    t.add(f"{p['last']}^{p['first']}", "PATIENT_NAME", "PatientName (0010,0010)", "dicom_header")
    t.add(p["mrn"], "MRN", "PatientID (0010,0020)", "dicom_header")
    t.add(p["dob"].strftime("%Y%m%d"), "DOB", "PatientBirthDate (0010,0030)", "dicom_header")
    t.add(p["address"], "ADDRESS", "PatientAddress (0010,1040)", "dicom_header")
    t.add(p["phone"], "PHONE", "private tag (0011,1001)", "dicom_header")
    t.add("ACC26-04417", "CASE_REFERENCE", "AccessionNumber (0008,0050)", "dicom_header")
    t.add(p["hospital"], "FACILITY", "InstitutionName (0008,0080)", "dicom_header")
    t.add("Hart^Amelia", "STAFF_NAME", "ReferringPhysicianName (0008,0090)", "dicom_header")
    t.add("20260402", "CLINICAL_DATE", "StudyDate (0008,0020)", "dicom_header")
    t.add("CT HEAD WITHOUT CONTRAST", "CLINICAL_TERM", "StudyDescription (0008,1030)", "dicom_header")
    t.add(burned[0], "PATIENT_NAME", "burned_in", "pixels")
    t.add(p["mrn"], "MRN", "burned_in", "pixels")
    t.add(p["dob"].isoformat(), "DOB", "burned_in", "pixels")
    items = t.as_list(); mark_ocr(items, {1: pix_img}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "dicom", items, expected_output=f.name))
    return docs


def make_dicom(path: Path, p, burned_lines):
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    size = 512
    yy, xx = np.mgrid[:size, :size]
    r = np.hypot(yy - size / 2, xx - size / 2)
    arr = np.where(r < 200, 1100, 0) + np.where(r < 185, -300, 0) + np.where(np.hypot(yy - 230, xx - 300) < 25, 120, 0)
    canvas_img = Image.fromarray(np.clip(arr / 1100 * 180, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(canvas_img)
    font = _font(15)
    for i, ln in enumerate(burned_lines):
        draw.text((8, 8 + i * 20), ln, fill=255, font=font)
    draw.text((8, size - 26), p["hospital"], fill=255, font=font)
    pixels = (np.asarray(canvas_img, dtype=np.uint16) * 16)

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset(); ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID; ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid(); ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "CT"; ds.StudyDate = "20260402"; ds.StudyTime = "093000"
    ds.StudyDescription = "CT HEAD WITHOUT CONTRAST"; ds.AccessionNumber = "ACC26-04417"
    ds.InstitutionName = p["hospital"]; ds.ReferringPhysicianName = "Hart^Amelia"
    ds.PatientName = f"{p['last']}^{p['first']}"; ds.PatientID = p["mrn"]
    ds.PatientBirthDate = p["dob"].strftime("%Y%m%d"); ds.PatientSex = p["sex"]
    ds.PatientAddress = p["address"]; ds.BurnedInAnnotation = "YES"
    ds.ClinicalTrialSubjectID = p["subject_id"]
    blk = ds.private_block(0x0011, "SYNTHVENDOR", create=True)
    blk.add_new(0x01, "LO", p["phone"])
    ds.Rows = ds.Columns = size; ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16; ds.BitsStored = 12; ds.HighBit = 11; ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)
    return canvas_img


def case_c(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-003"
    d = out / "documents" / ck
    hdr, body, foot, mr = name_forms(p)
    dob = dmy(p["dob"])
    docs = []
    pages = [[
        ("kv", [("Name", hdr), ("DOB", dob), ("Hospital no", p["mrn"]), ("Address", p["address"]),
                ("Trial ID", p["subject_id"])]),
        ("title", "DEATH SUMMARY"),
        ("p", f"{body}, a 77-year-old man, was admitted on 17/05/2026 with an acute anterior myocardial infarction "
              "(troponin T 2950 ng/L). He was enrolled in the DEMO01 study (Zelvoran or placebo)."),
    ], [
        ("p", "On 18/05/2026 he developed cardiogenic shock requiring inotropic support. TIMI flow grade 0 was seen "
              "in the left anterior descending artery."),
        ("p", f"On 19/05/2026 at 03:40 he had a ventricular fibrillation arrest. Resuscitation was unsuccessful and "
              f"death was confirmed at 04:15. His wife, {p['kin']}, was present."),
        ("p", "General practitioner: Dr Helen Marsh, Corrington Medical Practice."),
    ], [
        ("h", "Conclusion"),
        ("p", "Cause of death: 1a cardiogenic shock, 1b acute anterior myocardial infarction. The death was "
              "reported to the coroner (reference 2026/00417)."),
        ("p", f"{p['staff']}, Consultant Cardiologist"),
    ]]
    f = d / "death_summary.pdf"
    render_pdf(f, pages, letterhead=letterhead(p, "Coronary Care Unit"),
               footer=lambda n, N: f"Patient: {foot} - MRN {p['mrn']}" if n == N else "",
               metadata={"author": p["staff"], "title": f"Death summary - {body}", "subject": p["mrn"],
                         "keywords": f"DOB {dob}"})
    t = Truth(); add_letterhead_truth(t, p, 3)
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["address"], "ADDRESS"),
               (p["subject_id"], "SUBJECT_ID")], "banner")
    t.page(1, [(body, "PATIENT_NAME"), ("17/05/2026", "CLINICAL_DATE"), ("2950 ng/L", "LAB_VALUE"), ("Zelvoran", "DRUG")], "body")
    t.page(2, [("18/05/2026", "CLINICAL_DATE"), ("TIMI", "CLINICAL_TERM"), ("19/05/2026", "CLINICAL_DATE"),
               (p["kin"], "NEXT_OF_KIN"), ("Dr Helen Marsh", "STAFF_NAME"), ("Corrington Medical Practice", "FACILITY")], "body")
    t.page(3, [("2026/00417", "CASE_REFERENCE")], "body")
    t.page(3, [(p["staff"], "STAFF_NAME")], "signature")
    t.page(3, [(foot, "PATIENT_NAME"), (p["mrn"], "MRN")], "footer_last_page_only")
    for v, c in [(body, "PATIENT_NAME"), (p["mrn"], "MRN"), (dob, "DOB"), (p["staff"], "STAFF_NAME")]:
        t.add(v, c, "pdf_metadata", "metadata")
    items = t.as_list(); check_text_pdf(f, items)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "text_pdf", items, expected_output=f.name))

    src = out / ".build" / "c_source.pdf"
    render_pdf(src, [[
        ("title", "REQUEST FOR POST-MORTEM EXAMINATION"),
        ("kv", [("Deceased", body), ("Date of birth", dob), ("Address", p["address"]), ("Hospital no", p["mrn"]),
                ("Coroner reference", "2026/00417"), ("Date of death", "19/05/2026"), ("Trial ID", p["subject_id"])]),
        ("p", "Clinical summary: acute anterior myocardial infarction complicated by cardiogenic shock."),
        ("p", f"Requested by {p['staff']}."),
    ]], letterhead=letterhead(p, "Coronary Care Unit"))
    img = scanify(page_image(src, dpi=200), rng_np, angle=-1.2, noise=7)
    f = d / "autopsy_request_scan.pdf"
    image_only_pdf(img, f, 200)
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(body, "PATIENT_NAME"), (dob, "DOB"), (p["address"], "ADDRESS"), (p["mrn"], "MRN"),
               ("2026/00417", "CASE_REFERENCE"), ("19/05/2026", "CLINICAL_DATE"), (p["subject_id"], "SUBJECT_ID"),
               (p["staff"], "STAFF_NAME")], "scan")
    items = t.as_list(); mark_ocr(items, {1: img}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "image_pdf_200dpi", items, expected_output=f.name))
    return docs


def case_d(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-005"
    d = out / "documents" / ck
    hdr, body, foot, mr = name_forms(p)
    dob = dmy(p["dob"])
    docs = []
    src = out / ".build" / "d_source.pdf"
    render_pdf(src, [[
        ("kv", [("Name", hdr), ("DOB", dob), ("Hospital no", p["mrn"]), ("Tel", p["phone"])]),
        ("title", "SUMMARY LETTER TO GENERAL PRACTITIONER"),
        ("p", f"{mr} {body} was admitted on 20/05/2025 with chest pain and collapsed on the ward the same evening. "
              "Despite resuscitation she died on 20/05/2025. Post-mortem findings are awaited."),
        ("p", "She had been screened for the DEMO01 study but had not yet received study medication."),
        ("p", f"{p['staff']}, Consultant Cardiologist"),
    ]], letterhead=letterhead(p, "Acute Medicine"))
    img = scanify(page_image(src, dpi=150), rng_np, angle=2.5, noise=11, blur=0.6)
    f = d / "summary_letter_lowquality.jpg"; f.parent.mkdir(parents=True, exist_ok=True)
    img.save(f, quality=45, dpi=(150, 150))
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["phone"], "PHONE"), (body, "PATIENT_NAME"),
               ("20/05/2025", "CLINICAL_DATE"), (p["staff"], "STAFF_NAME")], "scan")
    items = t.as_list(); mark_ocr(items, {1: Image.open(f).convert("L")}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "scan_jpg_150dpi_lowq", items, expected_output="summary_letter_lowquality.pdf"))

    # D2 mixed PDF: identifiers ONLY inside an image banner; body text is clean
    banner = text_banner([p["hospital"], f"Patient: {hdr}    DOB: {dob}", f"MRN: {p['mrn']}    Tel: {p['phone']}"])
    f = d / "referral_letter_mixed.pdf"
    render_pdf(f, [[
        ("img", banner, 170),
        ("title", "CARDIOLOGY REFERRAL"),
        ("p", "The patient was seen in the emergency department on 19/05/2025 with exertional chest pain and "
              "T-wave inversion in V1-V4. Please review urgently."),
        ("p", f"Trial ID: {p['subject_id']}. Not yet dosed."),
        ("p", f"Referring clinician: {p['staff']}"),
    ]])
    t = Truth()
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["phone"], "PHONE"),
               (p["hospital"], "FACILITY")], "image_header")
    t.page(1, [("19/05/2025", "CLINICAL_DATE"), (p["subject_id"], "SUBJECT_ID")], "body")
    t.page(1, [(p["staff"], "STAFF_NAME")], "signature")
    items = t.as_list(); check_text_pdf(f, items)
    mark_ocr([i for i in items if "image_header" in i["locations"]], {1: page_image(f, dpi=300)}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "mixed_pdf_image_header", items, expected_output=f.name))
    return docs


def case_e(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-006"
    d = out / "documents" / ck
    hdr, body, foot, mr = name_forms(p)
    dob = dmy(p["dob"])
    docs = []
    src = out / ".build" / "e_source.pdf"
    render_pdf(src, [[
        ("title", "CORONARY ANGIOGRAPHY REPORT"),
        ("kv", [("Name", body), ("DOB", dob), ("MRN", p["mrn"]), ("Procedure date", "13/05/2025"),
                ("Trial ID", p["subject_id"])]),
        ("p", "Indication: unstable angina. Findings: 70% stenosis of the proximal left anterior descending artery, "
              "TIMI 3 flow. Plan: medical therapy and staged PCI."),
        ("p", f"Operator: {p['staff']}"),
    ]], letterhead=letterhead(p, "Cardiac Catheter Laboratory"))
    img = scanify(page_image(src, dpi=300), rng_np, angle=0.3, noise=5, rotate90=True)
    f = d / "angiography_report_rotated.pdf"; f.parent.mkdir(parents=True, exist_ok=True)
    image_only_pdf(img, f, 300)
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(body, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), ("13/05/2025", "CLINICAL_DATE"),
               (p["subject_id"], "SUBJECT_ID"), (p["staff"], "STAFF_NAME")], "scan")
    items = t.as_list(); mark_ocr(items, {1: img}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "image_pdf_rotated_90", items, expected_output=f.name))

    f = d / "clinic_letter.pdf"
    render_pdf(f, [[
        ("p", "To: Dr Thomas Reed, Halbridge Health Centre"),
        ("table", [["Re:", f"{mr} {body}"], ["Born:", dob], ["Address:", p["address"]], ["Hospital no:", p["mrn"]],
                   ["Trial ID:", p["subject_id"]]], [30, 140]),
        ("p", "Dear Dr Reed, I reviewed your patient in clinic on 02/06/2025 after her admission with unstable angina "
              "on 12/05/2025. She has a past history of Hodgkin lymphoma, treated in 2009, and remains in remission."),
        ("p", "She continues study medication (Zelvoran or placebo) and aspirin 75 mg daily."),
        ("p", f"Yours sincerely, {p['staff']}, Consultant Cardiologist"),
    ]], letterhead=letterhead(p, "Cardiology Outpatients"), footer=lambda n, N: f"cc: patient ({p['email']})")
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [("Dr Thomas Reed", "STAFF_NAME"), ("Halbridge Health Centre", "FACILITY")], "body")
    t.page(1, [(body, "PATIENT_NAME"), (dob, "DOB"), (p["address"], "ADDRESS"), (p["mrn"], "MRN"),
               (p["subject_id"], "SUBJECT_ID")], "table")
    t.page(1, [("02/06/2025", "CLINICAL_DATE"), ("12/05/2025", "CLINICAL_DATE"), ("Hodgkin", "EPONYM"),
               ("Zelvoran", "DRUG"), (p["staff"], "STAFF_NAME")], "body")
    t.page(1, [(p["email"], "EMAIL")], "footer")
    items = t.as_list(); check_text_pdf(f, items)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "text_pdf", items, expected_output=f.name))
    return docs


def case_f(out, p, rng_np, use_ocr):
    ck = f"{p['subject_id']}_AE-007"
    d = out / "documents" / ck
    hdr = f"{p['last'].upper()}, {p['first']}"
    body = f"{p['first']} {p['last']}"
    foot = f"{p['first'][0]}. {p['last']}"
    dob = dmy(p["dob"], ".")
    docs = []
    pages = [[
        ("kv", [("Patient", hdr), ("Geburtsdatum", dob), ("Pat.-Nr.", p["mrn"]), ("Versichertennr.", p["insurance"]),
                ("Anschrift", p["address"]), ("Telefon", p["phone"]), ("Studien-ID", p["subject_id"])]),
        ("title", "ENTLASSUNGSBRIEF"),
        ("p", f"Herr {body}, geb. {dob}, wurde am 08.01.2026 mit einer transitorischen ischämischen Attacke "
              "(rechtsseitige Schwäche über 40 Minuten) aufgenommen. Die MRT vom 08.01.2026 zeigte keine "
              "Diffusionsstörung."),
        ("p", "Der Patient nimmt an der Studie DEMO01 teil (Zelvoran oder Placebo). Ein Morbus Parkinson wurde ausgeschlossen."),
        ("p", f"Entlassung am 09.01.2026. {p['staff']}, Oberärztin Neurologie"),
    ]]
    f = d / "entlassungsbrief.pdf"
    render_pdf(f, pages, letterhead=letterhead(p, "Klinik für Neurologie"),
               footer=lambda n, N: f"{foot} · Pat.-Nr. {p['mrn']} · Seite {n}/{N}")
    t = Truth(); add_letterhead_truth(t, p, 1)
    t.page(1, [(hdr, "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"), (p["insurance"], "INSURANCE_NO"),
               (p["address"], "ADDRESS"), (p["phone"], "PHONE"), (p["subject_id"], "SUBJECT_ID")], "banner")
    t.page(1, [(body, "PATIENT_NAME"), ("08.01.2026", "CLINICAL_DATE"), ("Zelvoran", "DRUG"), ("Parkinson", "EPONYM"),
               ("09.01.2026", "CLINICAL_DATE"), (p["staff"], "STAFF_NAME")], "body")
    t.page(1, [(foot, "PATIENT_NAME")], "footer")
    items = t.as_list(); check_text_pdf(f, items)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{f.name}", "text_pdf", items, language="de", expected_output=f.name))

    img = scanify(page_image(f, dpi=150), rng_np, angle=-0.6, noise=8)
    g = d / "entlassungsbrief_scan_150dpi.png"; img.save(g, dpi=(150, 150))
    scan_items = [{**i, "locations": ["scan"]} for i in items]
    mark_ocr(scan_items, {1: img}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{g.name}", "scan_png_150dpi", scan_items, language="de",
                          expected_output="entlassungsbrief_scan_150dpi.pdf"))

    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(); w.append(PdfReader(f)); w.encrypt(user_password="site104", owner_password="owner104")
    g = d / "befund_verschluesselt.pdf"
    with open(g, "wb") as fh:
        w.write(fh)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{g.name}", "encrypted_pdf", [], language="de", handling="reject:ENCRYPTED"))

    g = d / "ekg_beschaedigt.pdf"
    g.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + np.random.default_rng(SEED).bytes(4096))
    docs.append(doc_entry(ck, p, f"documents/{ck}/{g.name}", "corrupt_pdf", [], language="de", handling="reject:CORRUPT"))

    xr = Image.new("L", (1400, 1400), 20)
    dr = ImageDraw.Draw(xr)
    dr.ellipse([300, 250, 1100, 1250], fill=70); dr.ellipse([380, 330, 680, 1150], fill=35); dr.ellipse([720, 330, 1020, 1150], fill=35)
    fnt = _font(34)
    lines = ["RÖNTGEN THORAX p.a.", f"{p['last'].upper()}, {p['first']}  *{dob}", f"Pat.-Nr. {p['mrn']}", "08.01.2026"]
    for i, ln in enumerate(lines):
        dr.text((30, 30 + i * 46), ln, fill=235, font=fnt)
    dr.text((30, 1340), p["hospital"], fill=235, font=fnt)
    g = d / "roentgen_thorax.pdf"   # deliberately a PNG with a .pdf extension
    xr.save(g, format="PNG")
    t = Truth()
    t.page(1, [(f"{p['last'].upper()}, {p['first']}", "PATIENT_NAME"), (dob, "DOB"), (p["mrn"], "MRN"),
               (p["hospital"], "FACILITY"), ("08.01.2026", "CLINICAL_DATE")], "burned_in")
    items = t.as_list(); mark_ocr(items, {1: xr}, use_ocr)
    docs.append(doc_entry(ck, p, f"documents/{ck}/{g.name}", "wrong_extension_png", items, language="de",
                          expected_output="roentgen_thorax.pdf"))

    g = d / "notizen.txt"
    g.write_text(f"Bitte Befunde von Herrn {p['last']} nachreichen.\n", encoding="utf-8")
    docs.append(doc_entry(ck, p, f"documents/{ck}/{g.name}", "unsupported_txt", [], language="de", handling="reject:UNSUPPORTED_TYPE"))
    return docs


# ======================================================================================
# Main
# ======================================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=".", help="kit root to write into")
    ap.add_argument("--no-ocr", action="store_true", help="skip OCR visibility baseline")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for sub in ["config", "edc_mock", "inbox_scenarios", "expected", "documents", "ground_truth", "identities", ".build"]:
        shutil.rmtree(out / sub, ignore_errors=True)
    rng = random.Random(SEED)
    rng_np = np.random.default_rng(SEED)
    use_ocr = not a.no_ocr and shutil.which("tesseract") is not None

    write_config(out); write_edc(out, rng); write_inbox_scenarios(out); write_expected(out)
    ids = make_identities(rng)
    write_json(out / "identities" / "identities_SYNTHETIC.json",
               {"_warning": "Synthetic identities used ONLY inside source documents. They must never appear in the EDC, logs or redacted output.",
                "subjects": {v["subject_id"]: {**v, "dob": v["dob"].isoformat()} for v in ids.values()}})

    docs = []
    for fn, idx in [(case_a, 1), (case_b, 2), (case_c, 3), (case_d, 4), (case_e, 5), (case_f, 6)]:
        print(f"  building documents for {sid(idx)}")
        docs += fn(out, ids[idx], rng_np, use_ocr)

    index = []
    for n, dd in enumerate(docs, start=1):
        doc_id = f"D{n:02d}"
        items = dd.pop("items")
        gt_rel = f"ground_truth/{doc_id}.json"
        write_json(out / gt_rel, {"doc_id": doc_id, **dd, "items": items})
        index.append({"doc_id": doc_id, **dd, "ground_truth": gt_rel,
                      "counts": {r: sum(i["requirement"] == r for i in items) for r in ("must_redact", "should_redact", "must_keep")}})
    write_json(out / "ground_truth" / "corpus_index.json", {
        "generated_with_seed": SEED, "ocr_baseline": use_ocr, "documents": index,
        "cases": {f"{sid(i)}_{a_}": {"subject_id": sid(i), "ae_id": a_} for i, a_ in
                  [(1, "AE-001"), (2, "AE-002"), (3, "AE-003"), (4, "AE-005"), (5, "AE-006"), (6, "AE-007")]},
    })
    shutil.rmtree(out / ".build", ignore_errors=True)
    total = sum(sum(d["counts"].values()) for d in index)
    print(f"Done: {len(index)} documents, {total} ground-truth items, OCR baseline {'on' if use_ocr else 'off'}")


if __name__ == "__main__":
    main()
