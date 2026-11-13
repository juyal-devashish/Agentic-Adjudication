"""Synthetic document sets for Flow 2 quality checks (plan step 14). Synthetic data only.

    uv run python -m tools.make_flow2_fixtures --out data/demo_uploads/flow2

Each scenario is a folder of PDFs for kit case AE-001 (subject DEMO01-101-0001,
myocardial infarction, onset 2026-03-14; the patient is the kit's seeded identity) plus
`expected.json` with the QC decision it should get:

    complete          discharge summary + ECG report              → pass
    missing_document  discharge summary only (a cardiac event     → query missing_document
                      needs an ECG)
    wrong_patient     the ECG report is another person's         → query wrong_patient
    different_event   summary and ECG are from another episode,  → AI off: review (onset
                      months earlier                                not found; a person
                                                                    decides). AI on: the
                                                                    agent queries the site
                                                                    (wrong_event)
    unreadable        the ECG report is a blank scan              → query unreadable

The kit is never modified; the other patient comes from Faker with a fixed seed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from faker import Faker

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
SUBJECT = "DEMO01-101-0001"
EXPECTED = {
    "complete": {"decision": "pass", "codes": []},
    "missing_document": {"decision": "query", "codes": ["missing_document"]},
    "wrong_patient": {"decision": "query", "codes": ["wrong_patient"]},
    "different_event": {
        "decision": "review",
        "codes": [],
        "with_ai": {"decision": "query", "codes": ["wrong_event"]},  # the agent's verdict
    },
    "unreadable": {"decision": "query", "codes": ["unreadable"]},
}


def _pdf(lines: list[str]) -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 18
        return doc.tobytes()


def _blank_scan() -> bytes:
    """An image-only page with nothing legible on it, like a fax that came through empty."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 400, 560), False)
        pix.set_rect(pix.irect, (235,))
        page.insert_image(page.rect, pixmap=pix)
        return doc.tobytes()


def _summary(p: dict, *, event: str, date: str, story: str) -> bytes:
    return _pdf([
        p["hospital"],
        "Department of Cardiology",
        f"Patient: {p['first']} {p['last']}",
        f"DOB: {p['dob_uk']}",
        f"MRN: {p['mrn']}",
        f"Trial ID: {SUBJECT}",
        "",
        "DISCHARGE SUMMARY",
        f"Diagnosis: {event}",
        f"Admitted on {date}. {story}",
        "Discharged home in a stable condition.",
    ])  # fmt: skip


def _ecg(p: dict, *, trial_id: bool = True, recorded: str = "14/03/2026 08:52") -> bytes:
    return _pdf([
        "Cardiac Physiology",
        "12-LEAD ECG REPORT",
        f"Name: {p['first']} {p['last']}",
        f"DOB: {p['dob_uk']}",
        f"Recorded: {recorded}",
        *([f"Trial ID: {SUBJECT}"] if trial_id else []),
        "Sinus rhythm 88/min. ST elevation in V1-V4, consistent with an acute anterior",
        "infarct. QTc 452 ms.",
    ])  # fmt: skip


def _people() -> tuple[dict, dict]:
    ident = json.loads((KIT / "identities" / "identities_SYNTHETIC.json").read_text())
    p = dict(ident["subjects"][SUBJECT])
    y, m, d = p["dob"].split("-")
    p["dob_uk"] = f"{d}/{m}/{y}"
    fake = Faker("en_GB")
    fake.seed_instance(20260314)
    dob = fake.date_of_birth(minimum_age=50, maximum_age=80)
    other = {
        "first": fake.first_name(),
        "last": fake.last_name(),
        "dob_uk": dob.strftime("%d/%m/%Y"),
        "mrn": str(fake.random_number(digits=7, fix_len=True)),
        "hospital": p["hospital"],
    }
    return p, other


def make(out: Path) -> dict[str, dict]:
    p, other = _people()
    mi = {"event": "Acute anterior myocardial infarction", "date": "14/03/2026",
          "story": "Presented with central chest pain; troponin T rose to 3860 ng/L."}  # fmt: skip
    sets = {
        "complete": {"discharge_summary.pdf": _summary(p, **mi), "ecg_report.pdf": _ecg(p)},
        "missing_document": {"discharge_summary.pdf": _summary(p, **mi)},
        "wrong_patient": {
            "discharge_summary.pdf": _summary(p, **mi),
            "ecg_report.pdf": _ecg(other, trial_id=False),
        },
        "different_event": {
            "discharge_summary.pdf": _summary(
                p,
                event="Fractured neck of femur",
                date="02/11/2025",
                story="Fell at home; hemiarthroplasty on the day of admission.",
            ),  # fmt: skip
            "ecg_report.pdf": _ecg(p, recorded="02/11/2025 19:10"),
        },
        "unreadable": {"discharge_summary.pdf": _summary(p, **mi), "ecg_report.pdf": _blank_scan()},
    }
    for scenario, files in sets.items():
        folder = out / scenario
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (folder / name).write_bytes(content)
    (out / "expected.json").write_text(json.dumps({"case": f"DEMO01|{SUBJECT}|AE-001",
                                                   **EXPECTED}, indent=2))  # fmt: skip
    return EXPECTED


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "demo_uploads" / "flow2")
    args = parser.parse_args()
    make(args.out)
    print(f"Flow 2 fixtures in {args.out}")


if __name__ == "__main__":
    main()
