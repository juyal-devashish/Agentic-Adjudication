"""Runner loop (mvp-spec.md §5.1). Placeholder until step 3 adds intake."""

from __future__ import annotations

import logging
import time

from adjud.paths import DataPaths

log = logging.getLogger("adjud.runner")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    paths = DataPaths.from_env()
    log.info("runner started, data dir %s (no steps implemented yet)", paths.root)
    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()
