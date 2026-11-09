"""Reset the demo: wipe runtime data and load the mock EDC from the test kit (P-11).

    uv run python -m tools.reset_demo              # empty inbox
    uv run python -m tools.reset_demo --preload    # also put 01_initial.csv in the inbox

Only these runtime folders under the data dir are removed: cases, intake, inbox,
processed, outbox, logs, edc_mock, demo_uploads. data/config/, data/flow1-testkit/ and
data/metrics/ (corpus measurements shown on page 3, not case data) are kept.

data/demo_uploads/ gets copies of the kit's import files (inbox_files/) and source
documents (one folder per case key), ready to drag into the UI during the demo.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from adjud.paths import DataPaths
from tools import make_flow2_fixtures

RUNTIME = (
    "cases",
    "intake",
    "inbox",
    "processed",
    "outbox",
    "logs",
    "edc_mock",
    "demo_uploads",
    "uploads",
)  # data/ai/usage.jsonl is kept: AI spend is tracked across demo resets
KIT = Path(__file__).resolve().parents[1] / "data" / "flow1-testkit"


def reset(paths: DataPaths, *, preload: bool = False) -> None:
    for name in RUNTIME:
        target = paths.root / name
        if target.resolve().parent != paths.root.resolve():
            raise RuntimeError(f"refusing to remove {target}")
        if target.exists():
            shutil.rmtree(target)
    shutil.copytree(KIT / "edc_mock", paths.edc_mock)
    for name in ("inbox", "processed", "outbox", "cases", "intake"):
        (paths.root / name).mkdir(parents=True, exist_ok=True)
    staging = paths.root / "demo_uploads"
    shutil.copytree(KIT / "inbox_scenarios", staging / "inbox_files")
    shutil.copytree(KIT / "documents", staging / "documents")
    make_flow2_fixtures.make(staging / "flow2")  # Flow 2 QC scenarios (plan step 14)
    if preload:
        shutil.copy(KIT / "inbox_scenarios" / "01_initial.csv", paths.inbox)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preload", action="store_true", help="put 01_initial.csv in the inbox")
    args = parser.parse_args()
    paths = DataPaths.from_env()
    reset(paths, preload=args.preload)
    print(f"demo data reset in {paths.root}" + (" (inbox preloaded)" if args.preload else ""))


if __name__ == "__main__":
    main()
