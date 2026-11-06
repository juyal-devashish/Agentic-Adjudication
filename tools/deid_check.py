"""Measure de-identification: run our pipeline over the kit corpus, then score the output
with the kit's own harness (data/flow1-testkit/tools/deid_check.py) and apply its gate.

    uv run python -m tools.deid_check              # with OCR (needs Tesseract)
    uv run python -m tools.deid_check --no-ocr     # faster; image content "unverified"

Writes data/metrics/<timestamp>.json (the harness result plus our run summary) and
data/metrics/latest.json. Exit code 1 if the gate fails.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.run_corpus import KIT, ROOT, run


def load_harness():
    """Import the kit's harness module by path (it is not a package)."""
    tools = KIT / "tools"
    sys.path.insert(0, str(tools))  # the harness imports its sibling textmatch.py
    spec = importlib.util.spec_from_file_location("kit_deid_check", tools / "deid_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--metrics", type=Path, default=ROOT / "data" / "metrics")
    args = parser.parse_args()
    out = args.metrics / "corpus_out"
    summary = run(out)
    harness = load_harness()
    ocr = not args.no_ocr and harness.ocr_available()
    result = harness.score(KIT, out, ocr)
    result["run"] = summary
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.metrics.mkdir(parents=True, exist_ok=True)
    for name in (f"{stamp}.json", "latest.json"):
        (args.metrics / name).write_text(json.dumps(result, indent=1, ensure_ascii=False))
    harness.print_report(result)
    print(f"\npipeline time {summary['seconds']} s; metrics -> {args.metrics / 'latest.json'}")
    return 0 if result["summary"]["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
