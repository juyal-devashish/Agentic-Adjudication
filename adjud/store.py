"""CaseStore interface and the file-based implementation (mvp-spec.md §4.4, §4.5).

Rules enforced here:
- one writer per case (file lock around every append);
- an event is appended only if the workflow allows it from the current state;
- files are written first (temp file + atomic link, never overwriting), then the event
  that references them by path and SHA-256;
- case.json is a cache that can always be rebuilt from events.jsonl.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from adjud.chain import ChainedLog
from adjud.events import Clock, Event, EventDraft, file_refs, sha256_bytes, utc_now
from adjud.paths import DataPaths
from adjud.workflow import CaseState, State, TransitionRefused, next_state, replay

CASE_ID_RE = re.compile(r"^[A-Z0-9]+-\d{4,}$")


class CaseStore(Protocol):
    def create_case(self, case_id: str, first_event: EventDraft) -> Event: ...
    def append_event(self, case_id: str, event: EventDraft) -> Event: ...
    def events(self, case_id: str) -> list[Event]: ...
    def state(self, case_id: str) -> CaseState: ...
    def list_cases(self, state: State | None = None) -> list[CaseState]: ...
    def put_file(self, case_id: str, rel_path: str, content: bytes) -> str: ...
    def get_file(self, case_id: str, rel_path: str) -> bytes: ...


class CaseExists(Exception):
    pass


class CaseNotFound(Exception):
    pass


def write_atomic(dest: Path, content: bytes, *, overwrite: bool) -> None:
    """Write via a temp file in the same folder. Without `overwrite`, an existing
    destination is never replaced (os.link fails if it exists)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, dest)
        else:
            os.link(tmp, dest)  # raises FileExistsError
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass
class FileCaseStore:
    paths: DataPaths
    clock: Clock = field(default=utc_now)

    # --- helpers -------------------------------------------------------------------

    def case_dir(self, case_id: str) -> Path:
        if not CASE_ID_RE.match(case_id):
            raise ValueError(f"invalid case id {case_id!r}")
        return self.paths.cases / case_id

    def _log(self, case_id: str) -> ChainedLog:
        return ChainedLog(self.case_dir(case_id) / "events.jsonl", case=case_id, clock=self.clock)

    def _safe_path(self, case_id: str, rel_path: str) -> Path:
        rel = PurePosixPath(rel_path)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError(f"unsafe case file path {rel_path!r}")
        return self.case_dir(case_id).joinpath(*rel.parts)

    def _write_cache(self, case_id: str, events: list[Event]) -> None:
        write_atomic(
            self.case_dir(case_id) / "case.json", replay(events).to_json().encode(), overwrite=True
        )

    # --- CaseStore -----------------------------------------------------------------

    def create_case(self, case_id: str, first_event: EventDraft) -> Event:
        if first_event.type != "case.created":
            raise ValueError("the first event of a case must be case.created")
        folder = self.case_dir(case_id)
        try:
            folder.mkdir(parents=True)
        except FileExistsError as exc:
            raise CaseExists(case_id) from exc
        return self._append(case_id, first_event)

    def append_event(self, case_id: str, event: EventDraft) -> Event:
        if event.type == "case.created":
            raise ValueError("use create_case for case.created")
        if not (self.case_dir(case_id) / "events.jsonl").exists():
            raise CaseNotFound(case_id)
        return self._append(case_id, event)

    def _append(self, case_id: str, draft: EventDraft) -> Event:
        def validate(prior: list[Event]) -> None:
            current = replay(prior).state if prior else None
            next_state(current, draft.type)  # raises TransitionRefused
            for ref in file_refs(draft.data):
                path = self._safe_path(case_id, ref["path"])
                if not path.exists() or sha256_bytes(path.read_bytes()) != ref["sha256"]:
                    raise ValueError(f"referenced file missing or changed: {ref['path']}")

        return self._log(case_id).append(
            draft, validate=validate, after=lambda evs: self._write_cache(case_id, evs)
        )

    def events(self, case_id: str) -> list[Event]:
        return self._log(case_id).events()

    def state(self, case_id: str) -> CaseState:
        """Use case.json if it matches the end of the log; otherwise replay."""
        events = self.events(case_id)
        if not events:
            raise CaseNotFound(case_id)
        cache = self.case_dir(case_id) / "case.json"
        if cache.exists():
            try:
                cached = CaseState.model_validate_json(cache.read_bytes())
                if cached.seq == events[-1].seq and cached.last_hash == events[-1].hash:
                    return cached
            except ValueError:
                pass
        return replay(events)

    def list_cases(self, state: State | None = None) -> list[CaseState]:
        if not self.paths.cases.exists():
            return []
        result = []
        for folder in sorted(self.paths.cases.iterdir()):
            if (folder / "events.jsonl").exists():
                cs = self.state(folder.name)
                if state is None or cs.state == state:
                    result.append(cs)
        return result

    def put_file(self, case_id: str, rel_path: str, content: bytes) -> str:
        """Write a new case file and return its sha256. Never overwrites."""
        write_atomic(self._safe_path(case_id, rel_path), content, overwrite=False)
        return sha256_bytes(content)

    def get_file(self, case_id: str, rel_path: str) -> bytes:
        return self._safe_path(case_id, rel_path).read_bytes()


def intake_log(paths: DataPaths, clock: Clock = utc_now) -> ChainedLog:
    """The hash-chained log of every evaluated AE record, eligible or not."""
    return ChainedLog(paths.intake / "evaluations.jsonl", case=None, clock=clock)


__all__ = [
    "CaseExists",
    "CaseNotFound",
    "CaseStore",
    "FileCaseStore",
    "intake_log",
    "TransitionRefused",
    "write_atomic",
]
