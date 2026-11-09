"""The mock EDC: CSV files that stand in for the study database."""

from __future__ import annotations

import csv
from pathlib import Path


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class MockEdc:
    def __init__(self, edc_dir: Path):
        edc_dir = Path(edc_dir)
        self.demographics = {r["subject_id"]: r for r in _load(edc_dir / "demographics.csv")}
        self.medical_history = _load(edc_dir / "medical_history.csv")
        self.conmeds = _load(edc_dir / "conmeds.csv")
        self.labs = _load(edc_dir / "labs.csv")
        self.ae = _load(edc_dir / "ae.csv")

    def subject(self, subject_id: str) -> dict[str, str] | None:
        return self.demographics.get(subject_id)
