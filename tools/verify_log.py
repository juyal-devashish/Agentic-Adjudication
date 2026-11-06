"""Verify every hash chain (cases and intake log) and every referenced file.

Exit code 0 if everything verifies, 1 otherwise.
"""

from __future__ import annotations

import sys

from adjud.integrity import verify_all
from adjud.paths import DataPaths


def main() -> int:
    paths = DataPaths.from_env()
    reports = verify_all(paths)
    bad = [r for r in reports if not r.ok]
    for r in reports:
        status = "OK " if r.ok else "BAD"
        print(f"{status} {r.log.relative_to(paths.root)} ({r.records} records)")
        for problem in r.problems:
            print(f"    - {problem}")
    print(f"\n{len(reports) - len(bad)}/{len(reports)} logs verified.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
