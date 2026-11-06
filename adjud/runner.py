"""Runner loop (mvp-spec.md §5.1): the only background process.

Every tick it runs one step for each case in an automatic state; every
`inbox_poll_minutes` it imports the files waiting in inbox/. Each step is safe to
re-run after a crash, because every write goes through the CaseStore rules.

    uv run python -m adjud.runner            # loop forever
    uv run python -m adjud.runner --once     # one pass (inbox + cases), then exit
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field

import yaml

from adjud.events import Clock, utc_now
from adjud.intake import IntakePipeline
from adjud.paths import DataPaths

log = logging.getLogger("adjud.runner")


@dataclass
class Runner:
    paths: DataPaths
    study: str = "DEMO01"
    clock: Clock = field(default=utc_now)

    def __post_init__(self) -> None:
        cfg = yaml.safe_load((self.paths.study_config(self.study) / "study.yaml").read_text())
        self.inbox_poll_seconds = 60 * int(cfg.get("inbox_poll_minutes", 5))
        self.tick_seconds = int(cfg.get("runner_tick_seconds", 10))
        self.pipeline = IntakePipeline(
            self.paths.root, self.paths.config, self.paths.edc_mock, self.study, self.clock
        )

    def import_inbox(self) -> int:
        self.paths.inbox.mkdir(parents=True, exist_ok=True)
        results = self.pipeline.process_inbox(self.paths.inbox, self.paths.processed)
        for r in results:
            log.info(
                "imported %s: %s, %d records, %d new cases",
                r.file,
                r.status,
                r.records,
                r.new_cases,
            )
        return len(results)

    def run_case_steps(self) -> int:
        """Automatic case steps (de-identification arrives in step 4)."""
        return 0

    def run_once(self) -> None:
        self.import_inbox()
        self.run_case_steps()

    def run_forever(self) -> None:
        last_inbox = 0.0
        while True:
            try:
                if time.monotonic() - last_inbox >= self.inbox_poll_seconds:
                    self.import_inbox()
                    last_inbox = time.monotonic()
                self.run_case_steps()
            except Exception:  # keep the loop alive; the failed step is retried next tick
                log.exception("runner step failed")
            time.sleep(self.tick_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    paths = DataPaths.from_env()
    runner = Runner(paths)
    log.info("runner started, data dir %s", paths.root)
    if args.once:
        runner.run_once()
    else:
        runner.run_forever()


if __name__ == "__main__":
    main()
