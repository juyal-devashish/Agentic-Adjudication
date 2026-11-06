"""An append-only, hash-chained JSONL log of flat records, with a file lock.

Every record is `{"seq", "at", ...fields, "prev_hash", "hash"}`. `hash` is SHA-256 over
the canonical JSON of the record without `hash`; `prev_hash` of the first record is
GENESIS_HASH. Used for every case's events.jsonl and for intake/evaluations.jsonl.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

from adjud.events import (
    GENESIS_HASH,
    Clock,
    canonical_json,
    format_time,
    record_hash,
    utc_now,
)

RESERVED = frozenset({"seq", "at", "prev_hash", "hash"})

Record = dict[str, Any]


class LogCorrupted(Exception):
    """The log cannot be read as a valid chain (e.g. an unparseable complete line)."""


@dataclass
class LogRead:
    records: list[Record]
    torn_tail: bytes | None = None  # bytes after the last newline, if any


@dataclass
class ChainedLog:
    path: Path
    clock: Clock = field(default=utc_now)

    @property
    def lock_path(self) -> Path:
        return self.path.with_name("." + self.path.name + ".lock")

    @property
    def quarantine_path(self) -> Path:
        return self.path.with_name(self.path.name + ".torn")

    def lock(self) -> FileLock:
        return FileLock(self.lock_path)

    def read(self) -> LogRead:
        """Parse every complete line. A trailing fragment without a newline is a
        write interrupted by a crash; it is returned separately, never parsed."""
        if not self.path.exists():
            return LogRead(records=[])
        raw = self.path.read_bytes()
        complete, sep, tail = raw.rpartition(b"\n")
        if not sep:  # no newline at all: everything is a fragment
            complete, tail = b"", raw
        records: list[Record] = []
        if sep:
            for n, line in enumerate(complete.split(b"\n"), start=1):
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise LogCorrupted(f"{self.path}: line {n} is not valid JSON") from exc
                if not isinstance(rec, dict):
                    raise LogCorrupted(f"{self.path}: line {n} is not a record")
                records.append(rec)
        return LogRead(records=records, torn_tail=tail or None)

    def records(self) -> list[Record]:
        return self.read().records

    def append(
        self,
        fields: Record,
        *,
        validate: Callable[[list[Record]], None] | None = None,
        after: Callable[[list[Record]], None] | None = None,
    ) -> Record:
        """Append one record under the lock.

        `validate(prior)` runs before anything is written and may raise to refuse.
        `after(all_records)` runs after the write, still under the lock (cache updates).
        """
        if RESERVED & fields.keys():
            raise ValueError(f"fields may not set {sorted(RESERVED & fields.keys())}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock():
            current = self.read()
            if current.torn_tail is not None:
                self._handle_torn_tail(current)
            prior = current.records
            if validate is not None:
                validate(prior)
            rec: Record = {
                "seq": len(prior) + 1,
                "at": format_time(self.clock()),
                **fields,
                "prev_hash": prior[-1]["hash"] if prior else GENESIS_HASH,
            }
            rec["hash"] = record_hash(rec)
            with self.path.open("ab") as fh:
                fh.write((canonical_json(rec) + "\n").encode())
                fh.flush()
                os.fsync(fh.fileno())
            if after is not None:
                after([*prior, rec])
            return rec

    def _handle_torn_tail(self, current: LogRead) -> None:
        """Called under the lock when the log ends in a fragment with no newline.

        That fragment is a write interrupted by a crash: its caller never got a record
        back, so nothing downstream can depend on it. Keep it for audit in a sidecar
        that is never read as part of the chain, then cut the log back to the end of
        its last complete line. fsync both, so a second crash stays valid.
        """
        assert current.torn_tail is not None
        with self.quarantine_path.open("ab") as fh:
            fh.write(current.torn_tail + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        valid_len = self.path.stat().st_size - len(current.torn_tail)
        with self.path.open("r+b") as fh:
            fh.truncate(valid_len)
            fh.flush()
            os.fsync(fh.fileno())


@dataclass
class ChainCheck:
    records: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_chain(log: ChainedLog, *, expect: Record | None = None) -> ChainCheck:
    """Check seq order, every hash and every prev_hash link. `expect` lists field
    values every record must have (e.g. {"case": "DEMO01-0001"})."""
    result = ChainCheck()
    try:
        read = log.read()
    except LogCorrupted as exc:
        result.problems.append(str(exc))
        return result
    result.records = len(read.records)
    prev = GENESIS_HASH
    for i, rec in enumerate(read.records, start=1):
        where = f"line {i}"
        if rec.get("seq") != i:
            result.problems.append(f"{where}: sequence broken (expected seq {i})")
        if rec.get("prev_hash") != prev:
            result.problems.append(f"{where}: chain broken (prev_hash does not match)")
        if record_hash(rec) != rec.get("hash"):
            result.problems.append(f"{where}: content altered (does not match its hash)")
        for key, value in (expect or {}).items():
            if rec.get(key) != value:
                result.problems.append(f"{where}: {key} is {rec.get(key)!r}, expected {value!r}")
        prev = rec.get("hash")
    if read.torn_tail is not None:
        result.problems.append(f"line {result.records + 1}: incomplete (torn) write")
    return result
