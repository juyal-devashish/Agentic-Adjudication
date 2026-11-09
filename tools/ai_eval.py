"""Evaluate the Flow 2 agents live (plan step 22). Real API calls: ask first (AGENTS.md).
Synthetic data only.

    uv run --env-file .env python -m tools.ai_eval --runs 5 [--record]

Each run takes the kit's cases and the Flow 2 fixtures through Flows 1 and 2 in a
throwaway data folder with `ai.mode: live`, exactly as the demo does (sites upload and
sign; everything else is the runner). All runs share one usage ledger, so the study's
budget cap covers the whole evaluation. The report, written to
data/metrics/ai_eval/<timestamp>.json and latest.json, gives per run and per case:

  - the outcome (final state, QC decision, who approved the narrative) against the
    expected one: kit cases from the replay demo, fixtures from make_flow2_fixtures
  - every agent call: valid or not, the gate's verdict and reasons, cost, latency
  - repeatability: per agent input, whether every run gave the same answer (document
    type, event verdict) or passed the gate (narrative)

`--record` writes the first run's valid live answers as the replay answers in
data/config/DEMO01/ai_replays/ (`"simulated": false`) and removes the simulated ones,
so replay mode shows real recorded AI.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.simulate_ai import KIT, REPLAYS, ROOT, _submit_and_check

HUMAN = ("site", "dm.", "safety")
# What the replay demo shows for the kit cases (README, tests/test_demo_script.py)
KIT_EXPECTED = {
    "AE-001": "pass",
    "AE-002": "pass",
    "AE-003": "pass",
    "AE-005": "pass",
    "AE-006": "query",
    "AE-007": "pass",
}
# make_flow2_fixtures.EXPECTED is the AI-off outcome; with the agents on, the event-match
# agent tells the different event apart and the site gets a query (tests/test_qc.py)
FIXTURE_WITH_AI = {"different_event": "query"}


def _demo(tmp: Path, ledger: Path):
    from adjud.paths import DataPaths
    from adjud.runner import Runner
    from tools.reset_demo import reset

    data = tmp / "data"
    data.mkdir(parents=True)
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    (data / "ai").symlink_to(ledger)  # one ledger, one budget for the whole evaluation
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    return runner


def _settle(runner, ticks: int = 6) -> None:
    for _ in range(ticks):
        runner.run_case_steps()


def _case_report(store, cid: str, label: str, expected: str) -> dict[str, Any]:
    events = store.events(cid)
    decisions = [e["data"].get("decision") for e in events if e["type"] == "qc.checks_completed"]
    calls = []
    gates = {e["data"]["call"]["path"]: e["data"] for e in events
             if e["type"] == "ai.gate" and e["data"].get("call")}  # fmt: skip
    for e in events:
        if e["type"] != "ai.called":
            continue
        rec = store.get_json(cid, e["data"]["call"]["path"])
        g = gates.get(e["data"]["call"]["path"])
        calls.append({
            "function": rec["function"], "input_hash": rec["input_hash"],
            "valid": rec["valid"], "output": rec["output"], "note": rec["note"],
            "gate": None if g is None else {"passed": g["passed"], "reasons": g["reasons"]},
            "cost_usd": rec["cost_usd"], "latency_ms": rec["latency_ms"],
            "requests": rec.get("requests"), "usage": rec["usage"],
            "record": str(store.case_dir(cid) / e["data"]["call"]["path"]),
        })  # fmt: skip
    gate_events = [e["data"] for e in events if e["type"] == "ai.gate"]
    for c in calls:  # QC gates are in the QC log, not in ai.gate events: match by order
        if c["gate"] is None and c["function"] != "narrative":
            c["gate"] = _qc_gate(store, cid, c)
    approved = next((e["actor"] for e in events if e["type"] in
                     ("narrative.approved", "narrative.signed")), None)  # fmt: skip
    outcome = decisions[0] if decisions else None
    people = sorted({e["actor"] for e in events if e["actor"].startswith(HUMAN)})
    return {
        "case": label, "case_id": cid, "state": str(store.state(cid).state),
        "qc_decisions": decisions, "expected_qc": expected, "as_expected": outcome == expected,
        "narrative_by": approved, "people": people, "calls": calls,
        "narrative_gates": [g for g in gate_events if g.get("about") == "narrative"],
    }  # fmt: skip


def _qc_gate(store, cid: str, call: dict[str, Any]) -> dict[str, Any] | None:
    for e in reversed(store.events(cid)):
        if e["type"] == "qc.checks_completed":
            log = store.get_json(cid, e["data"]["results"]["path"]).get("ai") or []
            for item in log:
                ref, g = item.get("call"), item.get("gate")
                if ref and g and call["record"].endswith(ref["path"]):
                    return {"passed": g["passed"], "reasons": g["reasons"]}
    return None


def one_run(tmp: Path, ledger: Path) -> list[dict[str, Any]]:
    from tools import make_flow2_fixtures

    cases = []
    runner = _demo(tmp / "kit", ledger)
    keys = []
    for folder in sorted((KIT / "documents").iterdir()):
        subject, ae = folder.name.split("_")
        key = f"DEMO01|{subject}|{ae}"
        keys.append((ae, _submit_and_check(runner, key, sorted(folder.iterdir()))))
    _settle(runner)
    store = runner.pipeline.store
    cases += [_case_report(store, cid, ae, KIT_EXPECTED.get(ae, "?")) for ae, cid in keys]
    fixtures = tmp / "flow2"
    make_flow2_fixtures.make(fixtures)
    key = json.loads((fixtures / "expected.json").read_text())["case"]
    for scenario, exp in make_flow2_fixtures.EXPECTED.items():
        runner = _demo(tmp / scenario, ledger)
        cid = _submit_and_check(runner, key, sorted((fixtures / scenario).iterdir()))
        _settle(runner)
        expected = FIXTURE_WITH_AI.get(scenario, exp["decision"])
        cases.append(_case_report(runner.pipeline.store, cid, f"fixture:{scenario}", expected))
    return cases


def _answer(call: dict[str, Any]) -> Any:
    out = call["output"] or {}
    if call["function"] == "document_type":
        return out.get("document_type")
    if call["function"] == "event_match":
        return out.get("verdict")
    return bool(call["gate"] and call["gate"]["passed"])  # narrative: did it pass the gate


def summarise(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    calls = [c for run in runs for case in run for c in case["calls"]]
    by_fn: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in calls:
        by_fn[c["function"]].append(c)
    functions = {}
    for fn, cs in sorted(by_fn.items()):
        gated = [c for c in cs if c["gate"] is not None]
        lat = sorted(c["latency_ms"] for c in cs)
        functions[fn] = {
            "calls": len(cs),
            "valid": sum(c["valid"] for c in cs),
            "gate_passed": sum(bool(c["gate"]["passed"]) for c in gated),
            "gate_failed_reasons": Counter(
                r for c in gated if not c["gate"]["passed"] for r in c["gate"]["reasons"]
            ).most_common(),
            "cost_usd": round(sum(c["cost_usd"] for c in cs), 6),
            "mean_cost_usd": round(sum(c["cost_usd"] for c in cs) / len(cs), 6),
            "median_latency_ms": lat[len(lat) // 2],
            "mean_requests": round(sum(c["requests"] or 0 for c in cs) / len(cs), 2),
        }
    answers: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for run in runs:
        for case in run:
            for c in case["calls"]:
                answers[(case["case"], c["function"], c["input_hash"])].append(_answer(c))
    repeat = [{"case": k[0], "function": k[1], "answers": v, "consistent": len(set(map(
        json.dumps, v))) == 1} for k, v in sorted(answers.items())]  # fmt: skip
    outcomes: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        for case in run:
            outcomes[case["case"]].append({
                "qc": case["qc_decisions"][0] if case["qc_decisions"] else None,
                "state": case["state"], "narrative_by": case["narrative_by"],
            })  # fmt: skip
    per_case_cost = defaultdict(list)
    for run in runs:
        for case in run:
            per_case_cost[case["case"]].append(sum(c["cost_usd"] for c in case["calls"]))
    kit_runs = [[c for c in run if not c["case"].startswith("fixture:")] for run in runs]
    return {
        "runs": len(runs),
        "as_expected": {
            case: sum(c["as_expected"] for run in runs for c in run if c["case"] == case)
            for case in outcomes
        },  # fmt: skip
        "outcomes": dict(outcomes),
        "functions": functions,
        "repeatability": repeat,
        "cost_usd_total": round(sum(c["cost_usd"] for c in calls), 6),
        "cost_usd_per_case_mean": {k: round(sum(v) / len(v), 6) for k, v in per_case_cost.items()},
        "kit_cases_without_specialist": [
            sum(
                1
                for c in run
                if not any(p.startswith("safety") for p in c["people"])
                and c["state"].endswith("READY_FOR_ADJUDICATION")
            )
            for run in kit_runs
        ],  # fmt: skip
    }


def record(run: list[dict[str, Any]]) -> int:
    """Replace the simulated replay answers with this run's valid live answers."""
    for path in REPLAYS.rglob("*.json"):
        if json.loads(path.read_text()).get("simulated", True):
            path.unlink()
    n = 0
    for case in run:
        for c in case["calls"]:
            if not c["valid"]:
                continue
            rec = json.loads(Path(c["record"]).read_text())
            path = REPLAYS / c["function"] / f"{c['input_hash']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "simulated": False, "function": c["function"], "model": rec["model"],
                "prompt": rec["prompt"], "input": rec["input"], "output": rec["output"],
                "tool_calls": rec["tool_calls"], "usage": rec["usage"],
                "cost_usd": rec["cost_usd"],
            }, indent=2, ensure_ascii=False) + "\n")  # fmt: skip
            n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--record", action="store_true", help="write run 1 as replay answers")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "metrics" / "ai_eval")
    parser.add_argument("--mode", choices=("live", "replay"), default="live",
                        help="replay: a free dry run on the recorded answers")  # fmt: skip
    args = parser.parse_args()
    os.environ["ADJUD_AI_MODE"] = args.mode
    if args.mode == "live" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("needs ANTHROPIC_API_KEY (live calls)")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work = args.out / stamp
    ledger = work / "ai"
    ledger.mkdir(parents=True)
    runs = []
    for n in range(1, args.runs + 1):
        tmp = Path(tempfile.mkdtemp(prefix=f"run{n}-", dir=work))
        runs.append(one_run(tmp, ledger))
        spent = sum(c["cost_usd"] for case in runs[-1] for c in case["calls"])
        print(f"run {n}: {sum(len(c['calls']) for c in runs[-1])} calls, ${spent:.4f}")
    report = {"at": stamp, "summary": summarise(runs), "runs": runs}
    if args.record:
        report["recorded"] = record(runs[0])
    for name in (f"{stamp}.json", "latest.json"):
        (args.out / name).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                                default=str))  # fmt: skip
    s = report["summary"]
    print(json.dumps({k: s[k] for k in ("as_expected", "functions", "cost_usd_total",
                                        "cost_usd_per_case_mean",
                                        "kit_cases_without_specialist")}, indent=1))  # fmt: skip
    print("inconsistent:", [r for r in s["repeatability"] if not r["consistent"]])
    if args.record:
        print(f"{report['recorded']} live answers written to {REPLAYS}")


if __name__ == "__main__":
    main()
