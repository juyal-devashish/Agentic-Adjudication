"""'Pull data from EDC' for the demo (plan step 21, P-34). Not an integration.

Reads the mock EDC's AE table (`edc_mock/ae.csv`), leaves out the history records the
mock keeps only for the earlier-events lookup (`edc_pull.exclude_ae_id_prefixes` in
study.yaml), writes the rest as `edc_pull_<time>.csv` to the inbox and imports it like
any other AE file. Intake is idempotent on (study, subject, AE ID), so pulling again
only adds new records and updates changed ones.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import yaml

from adjud.intake import ImportResult, IntakePipeline
from adjud.store import write_atomic


def pull(pipe: IntakePipeline, actor: str) -> list[ImportResult]:
    paths = pipe.paths
    study_cfg = yaml.safe_load((pipe.study_dir / "study.yaml").read_text())
    skip = tuple((study_cfg.get("edc_pull") or {}).get("exclude_ae_id_prefixes") or [])
    with (pipe.edc.root / "ae.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = [
            r for r in reader if r["study_id"] == pipe.study and not r["ae_id"].startswith(skip)
        ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    name = f"edc_pull_{pipe.clock().strftime('%Y%m%dT%H%M%S')}.csv"
    write_atomic(Path(paths.inbox) / name, out.getvalue().encode(), overwrite=False)
    return [
        r for r in pipe.process_inbox(paths.inbox, paths.processed, actor=actor) if r.file == name
    ]
