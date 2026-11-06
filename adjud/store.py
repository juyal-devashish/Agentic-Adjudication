"""CaseStore interface and the file-based implementation (mvp-spec.md §4.4, §4.5).

Rules enforced here:
- one writer per case (file lock around every append);
- an event is appended only if the workflow allows it from the current state;
- files are written first (temp file + atomic link, never overwriting), then the event
  that references them by path and SHA-256;
- case.json is a cache that can always be rebuilt from events.jsonl.

The read-side API (`events`, `list_cases`, `get_json`, `read_log`, `verify_log`, the
intake index) matches the Flow 1 test kit, so its tests run against this package.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from filelock import FileLock

from adjud.chain import ChainedLog, Record, check_chain
from adjud.events import Clock, EventDraft, file_refs, sha256_bytes, utc_now
from adjud.paths import DataPaths
from adjud.workflow import CaseState, State, TransitionRefused, next_state, replay

CASE_ID_RE = re.compile(r"^[A-Z0-9]+-\d{4,}$")


class CaseStore(Protocol):
    def create_case(self, case_id: str, first_event: EventDraft) -> Record: ...
    def append_event(self, case_id: str, event: EventDraft) -> Record: ...
    def events(self, case_id: str) -> list[Record]: ...
    def state(self, case_id: str) -> CaseState: ...
    def list_cases(self) -> list[str]: ...
    def put_file(self, case_id: str, rel_path: str, content: bytes) -> str: ...
    def get_file(self, case_id: str, rel_path: str) -> bytes: ...


class CaseExists(Exception):
    pass


class CaseNotFound(Exception):
    pass


# --- module-level helpers (kit interface) ----------------------------------------------


def read_log(path: Path) -> list[Record]:
    """All complete records of a chained log (case events or intake evaluations)."""
    return ChainedLog(Path(path)).records()


def verify_log(path: Path) -> tuple[bool, str]:
    """(True, "ok") if the chain verifies, else (False, first problem with its line)."""
    path = Path(path)
    expect = {"case": path.parent.name} if path.name == "events.jsonl" else None
    result = check_chain(ChainedLog(path), expect=expect)
    return (True, "ok") if result.ok else (False, result.problems[0])


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


def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def intake_log(paths: DataPaths, clock: Clock = utc_now) -> ChainedLog:
    """The hash-chained log of every evaluated AE record, eligible or not."""
    return ChainedLog(paths.intake / "evaluations.jsonl", clock=clock)


# --- the store ---------------------------------------------------------------------------


@dataclass
class FileCaseStore:
    paths: DataPaths
    clock: Clock = field(default=utc_now)

    @property
    def root(self) -> Path:
        return self.paths.root

    # --- helpers -------------------------------------------------------------------

    def case_dir(self, case_id: str) -> Path:
        if not CASE_ID_RE.match(case_id):
            raise ValueError(f"invalid case id {case_id!r}")
        return self.paths.cases / case_id

    def _log(self, case_id: str) -> ChainedLog:
        return ChainedLog(self.case_dir(case_id) / "events.jsonl", clock=self.clock)

    def _safe_path(self, case_id: str, rel_path: str) -> Path:
        rel = PurePosixPath(rel_path)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError(f"unsafe case file path {rel_path!r}")
        return self.case_dir(case_id).joinpath(*rel.parts)

    def _write_cache(self, case_id: str, events: list[Record]) -> None:
        write_atomic(
            self.case_dir(case_id) / "case.json", replay(events).to_json().encode(), overwrite=True
        )

    # --- writes ----------------------------------------------------------------------

    def new_case_id(self, study: str) -> str:
        """Allocate the next free case id for a study (DEMO01-0001, ...) and create its
        folder, under a store-wide lock so two importers never get the same id."""
        self.paths.cases.mkdir(parents=True, exist_ok=True)
        with FileLock(self.paths.cases / ".id.lock"):
            taken = [
                int(p.name.rsplit("-", 1)[1])
                for p in self.paths.cases.glob(f"{study}-*")
                if p.is_dir() and CASE_ID_RE.match(p.name)
            ]
            case_id = f"{study}-{max(taken, default=0) + 1:04d}"
            self.case_dir(case_id).mkdir()
            return case_id

    def create_case(self, case_id: str, first_event: EventDraft) -> Record:
        """Write case.created as the first event. The folder may already exist (from
        new_case_id) but must not hold a log yet."""
        if first_event.type != "case.created":
            raise ValueError("the first event of a case must be case.created")
        folder = self.case_dir(case_id)
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "events.jsonl").exists():
            raise CaseExists(case_id)
        return self._append(case_id, first_event)

    def append_event(self, case_id: str, event: EventDraft) -> Record:
        if event.type == "case.created":
            raise ValueError("use create_case for case.created")
        if not (self.case_dir(case_id) / "events.jsonl").exists():
            raise CaseNotFound(case_id)
        return self._append(case_id, event)

    def _append(self, case_id: str, draft: EventDraft) -> Record:
        def validate(prior: list[Record]) -> None:
            if draft.type == "case.created" and prior:
                raise CaseExists(case_id)
            current = replay(prior).state if prior else None
            next_state(current, draft.type)  # raises TransitionRefused
            for ref in file_refs(draft.data):
                path = self._safe_path(case_id, ref["path"])
                if not path.exists() or sha256_bytes(path.read_bytes()) != ref["sha256"]:
                    raise ValueError(f"referenced file missing or changed: {ref['path']}")

        return self._log(case_id).append(
            draft.fields(case_id),
            validate=validate,
            after=lambda evs: self._write_cache(case_id, evs),
        )

    def put_file(self, case_id: str, rel_path: str, content: bytes) -> str:
        """Write a new case file and return its sha256. Never overwrites."""
        write_atomic(self._safe_path(case_id, rel_path), content, overwrite=False)
        return sha256_bytes(content)

    def put_json(self, case_id: str, rel_path: str, obj: Any) -> str:
        return self.put_file(case_id, rel_path, json_bytes(obj))

    # --- reads -----------------------------------------------------------------------

    def events(self, case_id: str) -> list[Record]:
        return self._log(case_id).records()

    def state(self, case_id: str) -> CaseState:
        """Use case.json if it matches the end of the log; otherwise replay."""
        events = self.events(case_id)
        if not events:
            raise CaseNotFound(case_id)
        cache = self.case_dir(case_id) / "case.json"
        if cache.exists():
            try:
                cached = CaseState.model_validate_json(cache.read_bytes())
                if cached.seq == events[-1]["seq"] and cached.last_hash == events[-1]["hash"]:
                    return cached
            except ValueError:
                pass
        return replay(events)

    def list_cases(self) -> list[str]:
        """Ids of every case that has an event log, sorted."""
        if not self.paths.cases.exists():
            return []
        return sorted(p.name for p in self.paths.cases.iterdir() if (p / "events.jsonl").exists())

    def list_case_states(self, state: State | None = None) -> list[CaseState]:
        states = [self.state(cid) for cid in self.list_cases()]
        return [cs for cs in states if state is None or cs.state == state]

    def get_file(self, case_id: str, rel_path: str) -> bytes:
        return self._safe_path(case_id, rel_path).read_bytes()

    def get_json(self, case_id: str, rel_path: str) -> Any:
        return json.loads(self.get_file(case_id, rel_path))

    # --- intake log --------------------------------------------------------------------

    @property
    def evaluations_path(self) -> Path:
        return self.paths.intake / "evaluations.jsonl"

    def intake_log(self) -> ChainedLog:
        return intake_log(self.paths, self.clock)


__all__ = [
    "CaseExists",
    "CaseNotFound",
    "CaseStore",
    "FileCaseStore",
    "TransitionRefused",
    "intake_log",
    "read_log",
    "verify_log",
    "write_atomic",
]
