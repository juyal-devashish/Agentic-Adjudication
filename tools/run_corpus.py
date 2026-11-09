"""Run our de-identification pipeline over the test kit's corpus.

    uv run python -m tools.run_corpus --out data/metrics/corpus_out

Documents are grouped by case (so identifiers learned in one document are removed from
the others) and written to <out>/<case_key>/<expected_output>, the layout the kit's
harness expects. A JSON report per file goes to <out>/_reports/<case_key>/.

`--ai` (or `run(..., ai=True)`) also runs AI-1 on every OCR'd page: real API calls with
unredacted synthetic page images, so ask first (AGENTS.md). It needs `ADJUD_AI_MODE=live`
and ANTHROPIC_API_KEY. Each corpus case gets an evaluation case in <out>/_ai/data, where
the gateway records every call and its cost (that folder's own ledger and budget cap).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from adjud.deid.pipeline import DocInput, deidentify_case
from adjud.deid.policy import Policy
from adjud.documents import phi_detector
from adjud.events import EventDraft

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"


def _ai_store(out: Path, config: Path):
    """An evaluation store under <out>/_ai with AI-1 switched on; the study mode must be
    live (`ADJUD_AI_MODE=live`), so this never runs by accident."""
    from adjud.ai.gateway import Gateway
    from adjud.paths import DataPaths
    from adjud.store import FileCaseStore

    store = FileCaseStore(DataPaths(out / "_ai" / "data"))
    gw = Gateway.for_study(store, config, "DEMO01")
    if gw.cfg.mode != "live" or not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("--ai needs ADJUD_AI_MODE=live and ANTHROPIC_API_KEY")
    gw.cfg.functions["phi_detection"] = True
    return store, gw


def run(
    out: Path, *, kit: Path = KIT, config: Path = ROOT / "data" / "config", ai: bool = False
) -> dict:
    policy = Policy.for_study(config, "DEMO01")
    index = json.loads((kit / "ground_truth" / "corpus_index.json").read_text(encoding="utf-8"))
    by_case: dict[str, list[dict]] = {}
    for d in index["documents"]:
        by_case.setdefault(d["case_key"], []).append(d)
    if out.exists():
        shutil.rmtree(out)
    store, gw = _ai_store(out, config) if ai else (None, None)
    summary: dict = {"cases": {}, "seconds": 0.0}
    t0 = time.perf_counter()
    for n, (case_key, docs) in enumerate(by_case.items(), 1):
        inputs = [DocInput(Path(d["file"]).name, (kit / d["file"]).read_bytes()) for d in docs]
        detect = None
        if store is not None:
            cid = f"EVAL-{n:04d}"
            study, subject, ae_id = (case_key.split("|") + ["", "", ""])[:3]
            data = {"study": study, "subject": subject, "ae_id": ae_id, "evaluation": True}
            store.create_case(
                cid, EventDraft(type="case.created", actor="system:deid-check", data=data)
            )
            detect = phi_detector(gw, cid, policy)
        results = deidentify_case(inputs, policy, ai_detect=detect)
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
    if gw is not None:
        calls = list(gw.ledger.records())
        summary["ai"] = {"calls": len(calls),
                         "cost_usd": round(sum(float(r.get("cost_usd", 0)) for r in calls), 6),
                         "model": gw.cfg.model}  # fmt: skip
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "metrics" / "corpus_out")
    parser.add_argument("--ai", action="store_true", help="also run AI-1 (real API calls)")
    args = parser.parse_args()
    summary = run(args.out, ai=args.ai)
    for case, files in summary["cases"].items():
        print(case)
        for name, status in files.items():
            print(f"  {status:9} {name}")
    print(f"done in {summary['seconds']} s -> {args.out}")


if __name__ == "__main__":
    main()
