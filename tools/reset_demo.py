"""Reset the demo: wipe runtime data and load the mock EDC from the test kit (P-11).

    uv run python -m tools.reset_demo              # empty inbox
    uv run python -m tools.reset_demo --preload    # also put 01_initial.csv in the inbox

Only these runtime folders under the data dir are removed: cases, intake, inbox,
processed, outbox, metrics, logs, edc_mock. data/config/ and data/flow1-testkit/ are
never touched.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from adjud.paths import DataPaths

RUNTIME = ("cases", "intake", "inbox", "processed", "outbox", "metrics", "logs", "edc_mock")
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
