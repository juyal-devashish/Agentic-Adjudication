"""Record the agent answers the replay demo and the tests need (P-39). Synthetic data only.

    uv run python -m tools.record_ai                        # dry run: list what is missing
    uv run --env-file .env python -m tools.record_ai --live # real API calls: ask first

Takes the short demo's cases (AE-001, AE-005, AE-006) and the Flow 2 fixtures `complete`
and `different_event` through Flows 1 and 2 in a throwaway data folder. Every agent input
that already has a recorded answer in data/config/DEMO01/ai_replays/ is replayed; with
`--live` only the missing ones are sent to the API, and each valid answer is written as
a recorded answer (`"simulated": false`). The dry run makes no calls and lists the
inputs that would be sent.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adjud.ai.functions import AgentFunction
from adjud.ai.gateway import AiResult, Gateway, input_hash
from adjud.ai.tools import Toolbox
from tools.simulate_ai import KIT, REPLAYS, ROOT, _submit_and_check

DEMO = ("DEMO01-101-0001_AE-001", "DEMO01-102-0004_AE-005", "DEMO01-101-0005_AE-006")
FIXTURES = ("complete", "different_event")


@dataclass
class ReplayFirst(Gateway):
    """Replay what is recorded; call live for the rest (or, dry, only note it)."""

    live: bool = False

    def run(self, case_id: str, function: AgentFunction, payload: dict[str, Any],
            toolbox: Toolbox, images: list[bytes] | None = None) -> AiResult:  # fmt: skip
        if not self.cfg.enabled(function.name):
            return AiResult(function.name, "off", "unavailable", note="AI is off")
        h = input_hash(function, self.cfg.model, payload)
        if not self.live or (self.replay_dir / function.name / f"{h}.json").exists():
            return self._replay(case_id, function, payload, h)
        result = self._live(case_id, function, payload, h, toolbox, images or [])
        if result.ok and result.call:
            rec = self.store.get_json(case_id, result.call["path"])
            path = self.replay_dir / function.name / f"{h}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "simulated": False, "function": function.name, "model": rec["model"],
                "prompt": rec["prompt"], "input": rec["input"], "output": rec["output"],
                "tool_calls": rec["tool_calls"], "usage": rec["usage"],
                "cost_usd": rec["cost_usd"],
            }, indent=2, ensure_ascii=False) + "\n")  # fmt: skip
            self.recorded.append((function.name, h, case_id))
        return result

    def __post_init__(self) -> None:
        self.recorded: list[tuple[str, str, str]] = []


def _runner(tmp: Path, live: bool):
    from adjud.paths import DataPaths
    from adjud.runner import Runner
    from tools.reset_demo import reset

    data = tmp / "data"
    data.mkdir(parents=True)
    (data / "config").symlink_to(ROOT / "data" / "config")
    (data / "flow1-testkit").symlink_to(KIT)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    gw = runner.flow2.gateway
    runner.flow2.gateway = ReplayFirst(gw.store, gw.cfg, REPLAYS, live=live)
    return runner


def _settle(runner) -> None:
    for _ in range(3):
        runner.run_case_steps()


def main() -> None:
    from tools import make_flow2_fixtures

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send the missing inputs")
    args = parser.parse_args()
    os.environ["ADJUD_AI_MODE"] = "live" if args.live else "replay"
    if args.live and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("needs ANTHROPIC_API_KEY (live calls)")
    tmp = Path(tempfile.mkdtemp(prefix="record-ai-"))
    gateways = []
    runner = _runner(tmp / "demo", args.live)
    for folder in DEMO:
        subject, ae = folder.split("_")
        files = sorted((KIT / "documents" / folder).iterdir())
        _submit_and_check(runner, f"DEMO01|{subject}|{ae}", files)
    _settle(runner)
    gateways.append(("demo", runner.flow2.gateway))
    fixtures = tmp / "fixtures"
    make_flow2_fixtures.make(fixtures)
    key = json.loads((fixtures / "expected.json").read_text())["case"]
    for scenario in FIXTURES:
        runner = _runner(tmp / scenario, args.live)
        _submit_and_check(runner, key, sorted((fixtures / scenario).iterdir()))
        _settle(runner)
        gateways.append((scenario, runner.flow2.gateway))
    for name, gw in gateways:
        for function, h, _ in gw.missing:
            print(f"missing  {name:16} {function:14} {h[:12]}")
        for function, h, cid in gw.recorded:
            print(f"recorded {name:16} {function:14} {h[:12]} {cid}")
    spent = sum(gw.spent_usd() for _, gw in gateways)
    print(f"done; ledger in {tmp} (${spent:.2f})")


if __name__ == "__main__":
    main()
