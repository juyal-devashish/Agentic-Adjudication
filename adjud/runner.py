"""Runner loop (mvp-spec.md §5.1): the only background process.

Every tick it runs one step for each case in an automatic state; every
`inbox_poll_minutes` it imports the files waiting in inbox/. Each step is safe to
re-run after a crash, because every write goes through the CaseStore rules.

    uv run python -m adjud.runner            # loop forever
    uv run python -m adjud.runner --once     # one pass (inbox + cases), then exit
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import yaml

from adjud.delivery import EMAIL, SLACK, Deliverer, DeliveryLog, channels_from_env
from adjud.documents import DeidBusy, DocumentProcessor
from adjud.events import Clock, format_time, utc_now
from adjud.flow2 import Flow2
from adjud.flow3 import Flow3
from adjud.flow4 import Flow4
from adjud.handoff import pending_handoffs, write_handoff
from adjud.intake import IntakePipeline
from adjud.mailer import Mailer, SmtpSettings
from adjud.paths import DataPaths
from adjud.slack_app.core import Directory, Workspace
from adjud.slack_app.dm import ACTOR as SLACK_ACTOR
from adjud.slack_app.dm import NoticeDetail, SlackDmChannel, SlackSettings
from adjud.upload_folder import UploadFolders

log = logging.getLogger("adjud.runner")


def slack_since(delivery: DeliveryLog, clock: Clock) -> str:
    """When Slack was switched on: the first Notification it tried, if any (so a runner
    restart does not skip what was queued meanwhile); otherwise now."""
    notes = {n["id"]: n["created_at"] for n in delivery.notes()}
    tried = [notes[i] for i in delivery.attempts(SLACK) if i in notes]
    return min(tried) if tried else format_time(clock())


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
        self.documents = DocumentProcessor(self.pipeline.store, self.paths.config, self.study)
        self.last_inbox: float | None = None
        self.flow2 = Flow2(self.pipeline.store, self.paths.config, self.study)
        self.flow3 = Flow3(self.pipeline.store, self.paths.config, self.study)
        self.flow4 = Flow4(self.pipeline.store, self.paths.config, self.study)
        self.uploads = UploadFolders(self.pipeline.store, self.documents.outbox, self.clock)
        channels = channels_from_env()  # None: as before P-54, e-mail when SMTP is set
        smtp = SmtpSettings.from_env()
        if channels is not None and EMAIL in channels and smtp is None:
            log.warning("NOTIFY_CHANNELS lists smtp, but SMTP_HOST is not set: e-mail off")
        if channels is not None and EMAIL not in channels:
            smtp = None
        self.mailer = (
            Mailer(self.pipeline.store, self.paths.config, smtp, clock=self.clock) if smtp else None
        )
        self.slack: Deliverer | None = None
        if channels is not None and SLACK in channels:
            settings = SlackSettings.from_env()
            if settings is None:
                log.warning("NOTIFY_CHANNELS lists slack, but SLACK_BOT_TOKEN is not set: off")
            else:  # Slack posts what is queued from when it was first switched on (P-54)
                workspace = Workspace(self.paths, self.study, clock=self.clock)
                self.slack = Deliverer(
                    self.pipeline.store,
                    self.paths.config,
                    SlackDmChannel(
                        Directory(self.paths.config, self.paths.root / "slack", self.study),
                        settings,
                        settings.client(),
                        next_step=workspace.next_step,
                        detail=NoticeDetail(workspace),  # the Site Query, the narrative; PHI-gated
                    ),
                    SLACK_ACTOR,
                    since=slack_since(DeliveryLog(self.pipeline.store), self.clock),
                    clock=self.clock,
                )
                log.info("Slack on: notifications queued from now on are sent as DMs too")

    def send_mail(self) -> None:
        """E-mail queued notifications when SMTP is configured (plan P-29)."""
        if self.mailer is None:
            return
        counts = self.mailer.deliver_pending()
        if counts["sent"] or counts["failed"]:
            log.info("e-mail: %d sent, %d failed", counts["sent"], counts["failed"])

    def send_slack(self) -> None:
        """Post queued notifications to Slack when it is switched on (P-54). Runs after
        e-mail, and nothing that goes wrong here reaches the e-mail or the workflow."""
        if self.slack is None:
            return
        try:
            counts = self.slack.deliver_pending()
        except Exception:
            log.exception("Slack delivery failed this tick; e-mail and cases are unaffected")
            return
        if counts["sent"] or counts["failed"]:
            log.info("Slack: %d sent, %d failed", counts["sent"], counts["failed"])

    def announce_waiting_files(self) -> bool:
        """Tell the data manager, once per file, that AE records wait in the import queue
        (P-57): a DM with an Import button in Slack and an e-mail. The files announced are
        kept in a hidden `inbox/.announced`, which the import ignores and a reset removes.
        True for the first notice since the inbox was reset (no marker yet)."""
        waiting = {f"{f.name}|{f.stat().st_size}|{f.stat().st_mtime_ns}"
                   for f in self.paths.waiting_files()}  # fmt: skip
        if not waiting:
            return False
        marker = self.paths.inbox / ".announced"
        seen = set(json.loads(marker.read_text())) if marker.exists() else set()
        if waiting <= seen:
            return False
        self.documents.outbox.queue(None, "role:data_manager", "ae_file_waiting", page="import")
        first = not marker.exists()
        marker.write_text(json.dumps(sorted(seen | waiting)))
        log.info("data manager told: %d AE file(s) waiting", len(waiting))
        return first

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

    def take_uploads(self) -> None:
        """Per-case upload folders (plan P-31): create them, import complete ZIPs."""
        try:
            self.uploads.tick()
        except Exception:
            log.exception("upload folders failed")

    def run_case_steps(self) -> int:
        """Automatic case steps: de-identify every case waiting in DEIDENTIFYING, and write
        the Flow 2 handoff for every newly SUBMITTED case. A failure in one case is logged
        and retried next tick; it never stops the others."""
        from adjud.workflow import State

        done = 0
        later = self.flow34_steps()  # chosen now: one step per case per tick
        for cs in self.pipeline.store.list_case_states(State.DEIDENTIFYING):
            try:
                summary = self.documents.process_case(cs.case_id)
                log.info("de-identified %s run %s: %s", cs.case_id, summary["run"],
                         summary["files"])  # fmt: skip
                done += 1
            except DeidBusy:  # "Remove identifiers now" on the page is running it
                log.info("%s is being de-identified elsewhere; skipped", cs.case_id)
            except Exception:
                log.exception("de-identification failed for %s", cs.case_id)
        for case_id in pending_handoffs(self.pipeline.store):
            try:
                write_handoff(
                    self.pipeline.store,
                    case_id,
                    self.paths.config,
                    self.documents.outbox,
                    self.study,
                )
                log.info("handoff written for %s", case_id)
                done += 1
            except Exception:
                log.exception("handoff failed for %s", case_id)
        done += self.run_flow2_steps()
        done += self.run_steps(later)
        return done

    def run_flow2_steps(self) -> int:
        """Flow 2's automatic steps, one per case per tick: start QC from the manifest,
        run the QC round, build the dossier and draft the narrative, hand off to Flow 3."""
        from adjud.workflow import State

        store, done = self.pipeline.store, 0
        steps = [(cid, self.flow2.start_qc, "QC start") for cid in self.flow2.pending_qc_starts()]
        steps += [(cs.case_id, self.flow2.run_qc, "QC")
                  for cs in store.list_case_states(State.QC_CHECKS)]  # fmt: skip
        steps += [(cs.case_id, self.flow2.run_narrative, "narrative")
                  for cs in store.list_case_states(State.NARRATIVE)]  # fmt: skip
        steps += [(cid, self.flow2.write_flow3_handoff, "Flow 3 handoff")
                  for cid in self.flow2.pending_flow3_handoffs()]  # fmt: skip
        for case_id, step, name in steps:
            try:
                step(case_id)
                log.info("%s done for %s", name, case_id)
                done += 1
            except Exception:
                log.exception("%s failed for %s", name, case_id)
        return done

    def flow34_steps(self) -> list[tuple[str, Any, str]]:
        """Flows 3 and 4's automatic steps (P-41 onwards): assign the reviews, compare the
        signed answers, send more-data queries, derive the outcome, export it and take the
        mock EDC's acknowledgement. Code only; people adjudicate."""
        from adjud.workflow import State

        store = self.pipeline.store
        steps = [(cid, self.flow3.start_round, "adjudication start")
                 for cid in self.flow3.pending_starts()]  # fmt: skip
        steps += [(cid, self.flow3.compare, "comparison")
                  for cid in self.flow3.pending_comparisons()]  # fmt: skip
        steps += [(cs.case_id, self.flow3.send_more_data, "more-data query")
                  for cs in store.list_case_states(State.MORE_DATA)]  # fmt: skip
        steps += [(cid, lambda c, s=step: self.flow4.run(c, s), f"outcome {step}")
                  for cid, step in self.flow4.pending()]  # fmt: skip
        return steps

    def run_steps(self, steps: list[tuple[str, Any, str]]) -> int:
        done = 0
        for case_id, fn, name in steps:
            try:
                fn(case_id)
                log.info("%s done for %s", name, case_id)
                done += 1
            except Exception:
                log.exception("%s failed for %s", name, case_id)
        return done

    def run_once(self) -> None:
        self.import_inbox()
        self.announce_waiting_files()
        self.take_uploads()
        self.run_case_steps()
        self.send_mail()
        self.send_slack()

    def tick(self, now: float) -> None:
        """One loop iteration at monotonic time `now`. The inbox is checked every
        `inbox_poll_minutes`, counted from the first tick and again from the first notice
        after a reset: a file queued by a demo reset (also the app's Reset the demo, with the
        runner still going) stays for the data manager's "Import queued files" until one interval
        has passed."""
        if self.last_inbox is None:
            self.last_inbox = now
        elif now - self.last_inbox >= self.inbox_poll_seconds:
            self.import_inbox()
            self.last_inbox = now
        if self.announce_waiting_files():
            self.last_inbox = now
        self.take_uploads()
        self.run_case_steps()
        self.send_mail()
        self.send_slack()

    def run_forever(self) -> None:
        while True:
            try:
                self.tick(time.monotonic())
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
