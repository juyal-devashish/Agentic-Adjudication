"""Rebuild every case.json from its events.jsonl.

python -m tools.rebuild_state           rewrite any case.json that differs
python -m tools.rebuild_state --check   only report; exit 1 if any differs
"""

from __future__ import annotations

import argparse
import sys

from adjud.integrity import rebuild_all
from adjud.paths import DataPaths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare only, write nothing")
    args = parser.parse_args()
    results = rebuild_all(DataPaths.from_env(), write=not args.check)
    changed = [r for r in results if r.changed]
    verb = "differs" if args.check else "rebuilt"
    for r in changed:
        print(f"{r.case_id}: {verb}")
    print(f"{len(results)} cases, {len(changed)} {verb}.")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
