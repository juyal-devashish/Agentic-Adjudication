"""Run our de-identification pipeline over the test kit's corpus.

    uv run python -m tools.run_corpus --out data/metrics/corpus_out

Documents are grouped by case (so identifiers learned in one document are removed from
the others) and written to <out>/<case_key>/<expected_output>, the layout the kit's
harness expects. A JSON report per file goes to <out>/_reports/<case_key>/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from adjud.deid.pipeline import DocInput, deidentify_case
from adjud.deid.policy import Policy

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"


def run(out: Path, *, kit: Path = KIT, config: Path = ROOT / "data" / "config") -> dict:
    policy = Policy.for_study(config, "DEMO01")
    index = json.loads((kit / "ground_truth" / "corpus_index.json").read_text(encoding="utf-8"))
    by_case: dict[str, list[dict]] = {}
    for d in index["documents"]:
        by_case.setdefault(d["case_key"], []).append(d)
    if out.exists():
        shutil.rmtree(out)
    summary: dict = {"cases": {}, "seconds": 0.0}
    t0 = time.perf_counter()
    for case_key, docs in by_case.items():
        inputs = [DocInput(Path(d["file"]).name, (kit / d["file"]).read_bytes()) for d in docs]
        results = deidentify_case(inputs, policy)
        case_out = out / case_key
        reports = out / "_reports" / case_key
        case_out.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.output is not None and r.output_name:
                (case_out / r.output_name).write_bytes(r.output)
            (reports / f"{r.name}.json").write_text(json.dumps(r.report(), indent=1))
        summary["cases"][case_key] = {r.name: r.status for r in results}
    summary["seconds"] = round(time.perf_counter() - t0, 2)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "metrics" / "corpus_out")
    args = parser.parse_args()
    summary = run(args.out)
    for case, files in summary["cases"].items():
        print(case)
        for name, status in files.items():
            print(f"  {status:9} {name}")
    print(f"done in {summary['seconds']} s -> {args.out}")


if __name__ == "__main__":
    main()
