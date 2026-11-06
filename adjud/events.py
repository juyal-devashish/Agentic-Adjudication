"""Event records and their hashes (mvp-spec.md §4.3).

`hash` is SHA-256 over the canonical JSON of the event without its own `hash` field.
`prev_hash` of the first event in a log is GENESIS_HASH.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

GENESIS_HASH = "sha256:" + "0" * 64

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_time(dt: datetime) -> str:
    """ISO 8601 UTC with seconds precision, e.g. 2026-11-05T10:02:11Z."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class EventDraft(BaseModel):
    """What a caller supplies; the log adds seq, time and the hash chain."""

    model_config = ConfigDict(frozen=True)

    type: str
    actor: str  # "user:<id>" or "system:<component>"
    data: dict[str, Any] = {}
    reason: str | None = None
    signature: dict[str, Any] | None = None


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    at: str
    case: str | None  # None for entries in the intake log
    actor: str
    type: str
    data: dict[str, Any]
    reason: str | None
    signature: dict[str, Any] | None
    prev_hash: str
    hash: str

    def body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"hash"})

    def computed_hash(self) -> str:
        return sha256_bytes(canonical_json(self.body()).encode())

    def to_line(self) -> str:
        return canonical_json(self.model_dump(mode="json")) + "\n"

    @classmethod
    def build(
        cls, draft: EventDraft, *, seq: int, at: str, case: str | None, prev_hash: str
    ) -> Event:
        body = {
            "seq": seq,
            "at": at,
            "case": case,
            "actor": draft.actor,
            "type": draft.type,
            "data": draft.data,
            "reason": draft.reason,
            "signature": draft.signature,
            "prev_hash": prev_hash,
        }
        return cls(**body, hash=sha256_bytes(canonical_json(body).encode()))


def file_refs(data: Any) -> Iterator[dict[str, str]]:
    """Yield every file reference in event data.

    Convention: a file reference is any dict with string keys "path" and "sha256".
    Paths are relative to the case folder.
    """
    if isinstance(data, dict):
        if isinstance(data.get("path"), str) and isinstance(data.get("sha256"), str):
            yield {"path": data["path"], "sha256": data["sha256"]}
        for value in data.values():
            yield from file_refs(value)
    elif isinstance(data, list):
        for item in data:
            yield from file_refs(item)
