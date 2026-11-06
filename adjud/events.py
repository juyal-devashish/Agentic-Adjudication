"""Case event records and hashing helpers (mvp-spec.md §4.3).

A case event is a record in a ChainedLog with the fields below. `Event` is a typed,
read-only view of such a record.
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


def record_hash(record: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of a record without its own `hash` field."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return sha256_bytes(canonical_json(body).encode())


class EventDraft(BaseModel):
    """What a caller supplies; the log adds seq, time and the hash chain."""

    model_config = ConfigDict(frozen=True)

    type: str
    actor: str  # a user id ("ss.anna") or "system:<component>"
    data: dict[str, Any] = {}
    reason: str | None = None
    signature: dict[str, Any] | None = None

    def fields(self, case_id: str) -> dict[str, Any]:
        return {
            "case": case_id,
            "actor": self.actor,
            "type": self.type,
            "data": self.data,
            "reason": self.reason,
            "signature": self.signature,
        }


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    at: str
    case: str
    actor: str
    type: str
    data: dict[str, Any]
    reason: str | None
    signature: dict[str, Any] | None
    prev_hash: str
    hash: str


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
