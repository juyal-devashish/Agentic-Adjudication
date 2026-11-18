"""Reset the demo: wipe runtime data and load the mock EDC from the test kit (P-11).

    uv run python -m tools.reset_demo              # empty inbox
    uv run python -m tools.reset_demo --preload    # also put 01_initial.csv in the inbox
    uv run python -m tools.reset_demo --all        # the whole kit (22 records, 11 cases)

By default the demo is kept short: the mock EDC, the import file and the staged
documents hold only DEMO_AES, three cases and one record that is not eligible. History
records (H-...) stay in the mock EDC for the earlier-event lookups. Tests call reset()
without `only`, so they keep the whole kit.

Only these runtime folders under the data dir are removed: cases, intake, inbox,
processed, outbox, logs, edc_mock, demo_uploads. data/config/, data/flow1-testkit/ and
data/metrics/ (corpus measurements shown on page 3, not case data) are kept.

The demo can run in its own folder so a reset never touches ./data (P-57):

    ADJUD_DATA_DIR=data/demo uv run python -m tools.reset_demo --preload

A folder with no config/ gets a copy of the committed data/config; existing config is
kept as it is.

data/demo_uploads/ gets copies of the kit's import files (inbox_files/) and source
documents (one folder per case key), ready to drag into the UI during the demo.
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
from pathlib import Path

from adjud.paths import DataPaths
from tools import make_flow2_fixtures, make_flow3_fixtures

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
    "exports",  # outcome files for the EDC (Flow 4)
)  # data/ai/usage.jsonl is kept: AI spend is tracked across demo resets
KIT = Path(__file__).resolve().parents[1] / "data" / "flow1-testkit"
CONFIG = KIT.parent / "config"  # committed study config, copied into a new demo folder
# The short demo: a clean case (AE-001), an unsure agent on a poor scan (AE-005), missing
# documents (AE-006), and a record that is not eligible because it is not serious (AE-008).
DEMO_AES = ("AE-001", "AE-005", "AE-006", "AE-008")


def _rows_for(src: Path, only: tuple[str, ...], *, history: bool) -> str:
    """The CSV with only the rows of these AE IDs (and the H- history rows if asked)."""
    reader = csv.DictReader(io.StringIO(src.read_text(encoding="utf-8")))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        if row["ae_id"] in only or (history and row["ae_id"].startswith("H-")):
            writer.writerow(row)
    return out.getvalue()


def reset(paths: DataPaths, *, preload: bool = False, only: tuple[str, ...] | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    if not paths.config.exists():  # a separate demo folder (P-57); never overwritten
        shutil.copytree(CONFIG, paths.config)
    for name in RUNTIME:
        target = paths.root / name
        if target.resolve().parent != paths.root.resolve():
            raise RuntimeError(f"refusing to remove {target}")
        if target.exists():
            shutil.rmtree(target)
    # The Slack actions and conversation logs go with the cases they name; Acting As
    # (slack/acting_as.json) is kept, so DMs still reach the presenter after a reset (P-55).
    for name in ("actions.jsonl", ".actions.jsonl.lock", "dm_sent.jsonl",
                 "conversations.jsonl", ".conversations.jsonl.lock"):  # fmt: skip
        (paths.root / "slack" / name).unlink(missing_ok=True)
    shutil.copytree(KIT / "edc_mock", paths.edc_mock)
    for name in ("inbox", "processed", "outbox", "cases", "intake"):
        (paths.root / name).mkdir(parents=True, exist_ok=True)
    staging = paths.root / "demo_uploads"
    initial = KIT / "inbox_scenarios" / "01_initial.csv"
    if only is None:
        shutil.copytree(KIT / "inbox_scenarios", staging / "inbox_files")
        shutil.copytree(KIT / "documents", staging / "documents")
    else:
        ae = paths.edc_mock / "ae.csv"
        ae.write_text(_rows_for(KIT / "edc_mock" / "ae.csv", only, history=True))
        (staging / "inbox_files").mkdir(parents=True)
        (staging / "inbox_files" / initial.name).write_text(_rows_for(initial, only, history=False))
        for folder in sorted((KIT / "documents").iterdir()):
            if folder.name.split("_")[-1] in only:
                shutil.copytree(folder, staging / "documents" / folder.name)
    make_flow2_fixtures.make(staging / "flow2")  # Flow 2 QC scenarios (plan step 14)
    make_flow3_fixtures.make(staging / "flow3")  # Flow 3 answer sets (plan P-52)
    if preload:
        shutil.copy(staging / "inbox_files" / initial.name if only else initial, paths.inbox)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preload", action="store_true", help="put 01_initial.csv in the inbox")
    parser.add_argument("--all", action="store_true", help="the whole kit, not the short demo")
    args = parser.parse_args()
    paths = DataPaths.from_env()
    reset(paths, preload=args.preload, only=None if args.all else DEMO_AES)
    size = "the whole kit" if args.all else f"{len(DEMO_AES)} records ({', '.join(DEMO_AES)})"
    print(f"demo data reset in {paths.root} with {size}"
          + (" (inbox preloaded)" if args.preload else ""))  # fmt: skip


if __name__ == "__main__":
    main()
