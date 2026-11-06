"""An append-only, hash-chained JSONL log with a file lock.

Used for every case's events.jsonl and for intake/evaluations.jsonl.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from adjud.events import GENESIS_HASH, Clock, Event, EventDraft, format_time, utc_now


class LogCorrupted(Exception):
    """The log cannot be read as a valid chain (e.g. an unparseable complete line)."""


@dataclass
class LogRead:
    events: list[Event]
    torn_tail: bytes | None = None  # bytes after the last newline, if any


@dataclass
class ChainedLog:
    path: Path
    case: str | None = None
    clock: Clock = field(default=utc_now)

    @property
    def lock_path(self) -> Path:
        return self.path.with_name("." + self.path.name + ".lock")

    def lock(self) -> FileLock:
        return FileLock(self.lock_path)

    def read(self) -> LogRead:
        """Parse every complete line. A trailing fragment without a newline is a
        write interrupted by a crash; it is returned separately, never parsed."""
        if not self.path.exists():
            return LogRead(events=[])
        raw = self.path.read_bytes()
        complete, _, tail = raw.rpartition(b"\n")
        events: list[Event] = []
        if complete or raw.endswith(b"\n"):
            for n, line in enumerate(complete.split(b"\n"), start=1):
                try:
                    events.append(Event.model_validate(json.loads(line)))
                except (ValueError, TypeError) as exc:
                    raise LogCorrupted(f"{self.path}: line {n} is not a valid event") from exc
        else:
            tail = raw
        return LogRead(events=events, torn_tail=tail or None)

    def events(self) -> list[Event]:
        return self.read().events

    def append(
        self,
        draft: EventDraft,
        *,
        validate: Callable[[list[Event]], None] | None = None,
        after: Callable[[list[Event]], None] | None = None,
    ) -> Event:
        """Append one event under the lock.

        `validate(prior_events)` runs before anything is written and may raise to refuse.
        `after(all_events)` runs after the write, still under the lock (cache updates).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock():
            current = self.read()
            if current.torn_tail is not None:
                self._handle_torn_tail(current)
            prior = current.events
            if validate is not None:
                validate(prior)
            event = Event.build(
                draft,
                seq=len(prior) + 1,
                at=format_time(self.clock()),
                case=self.case,
                prev_hash=prior[-1].hash if prior else GENESIS_HASH,
            )
            with self.path.open("ab") as fh:
                fh.write(event.to_line().encode())
                fh.flush()
                os.fsync(fh.fileno())
            if after is not None:
                after([*prior, event])
            return event

    def _handle_torn_tail(self, current: LogRead) -> None:
        """Called under the lock when the log ends in a fragment with no newline.

        That fragment is a write interrupted by a crash: its caller never got an Event
        back, so nothing downstream can depend on it.
        """
        # TODO(human): decide how to recover from the torn last write before appending.
        raise NotImplementedError
