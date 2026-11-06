"""File-based store: hash-chained append-only logs, atomic JSON writes, per-file locks.

Layout under the store root:
  cases/<case_id>/events.jsonl   case event log (source of truth)
  cases/<case_id>/source/...     snapshots referenced by events (with sha256)
  intake/evaluations.jsonl       every evaluated record, case or not
  intake/index.json              cache: latest state per AE key (rebuildable from evaluations.jsonl)
  outbox/*.json                  notifications (no clinical content)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock

GENESIS = "sha256:" + "0" * 64


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_log(path: Path, record: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        entries = read_log(path)
        prev = entries[-1]["hash"] if entries else GENESIS
        rec = {"seq": len(entries) + 1, "at": now(), **record, "prev_hash": prev}
        rec["hash"] = sha256_bytes(canonical(rec))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush(); os.fsync(f.fileno())
        return rec


def verify_log(path: Path) -> tuple[bool, str]:
    prev = GENESIS
    for i, rec in enumerate(read_log(path), start=1):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("seq") != i:
            return False, f"sequence broken at line {i}"
        if rec.get("prev_hash") != prev:
            return False, f"chain broken at line {i}"
        if sha256_bytes(canonical(body)) != rec.get("hash"):
            return False, f"content altered at line {i}"
        prev = rec["hash"]
    return True, "ok"


class FileStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        for sub in ("cases", "intake", "outbox"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---- intake log and index ------------------------------------------------------
    @property
    def evaluations_path(self) -> Path:
        return self.root / "intake" / "evaluations.jsonl"

    def log_evaluation(self, record: dict) -> dict:
        return append_log(self.evaluations_path, record)

    def load_index(self) -> dict:
        p = self.root / "intake" / "index.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_index(self, index: dict):
        atomic_write(self.root / "intake" / "index.json", json.dumps(index, indent=1, ensure_ascii=False).encode())

    def rebuild_index(self) -> dict:
        """Recreate index.json purely from the evaluation log."""
        index: dict = {}
        for e in read_log(self.evaluations_path):
            if e["outcome"] in ("stale", "unchanged"):
                continue
            entry = index.get(e["key"], {})
            entry.update({"last_modified": e["last_modified"], "hash": e["record_hash"], "eligible": e["eligible"]})
            if e.get("case_id"):
                entry["case_id"] = entry.get("case_id") or e["case_id"]
            entry.setdefault("case_id", None)
            index[e["key"]] = entry
        return index

    # ---- cases ------------------------------------------------------------------------
    def case_dir(self, case_id: str) -> Path:
        return self.root / "cases" / case_id

    def new_case_id(self, study: str) -> str:
        with FileLock(str(self.root / "cases" / ".id.lock")):
            n = len([p for p in (self.root / "cases").iterdir() if p.is_dir()]) + 1
            cid = f"{study}-{n:04d}"
            self.case_dir(cid).mkdir(parents=True)
            return cid

    def list_cases(self) -> list[str]:
        return sorted(p.name for p in (self.root / "cases").iterdir() if p.is_dir())

    def put_json(self, case_id: str, rel: str, obj) -> str:
        data = json.dumps(obj, indent=2, ensure_ascii=False).encode()
        atomic_write(self.case_dir(case_id) / rel, data)
        return sha256_bytes(data)

    def get_json(self, case_id: str, rel: str):
        return json.loads((self.case_dir(case_id) / rel).read_text(encoding="utf-8"))

    def append_event(self, case_id: str, type_: str, data: dict, actor: str, reason: str | None = None) -> dict:
        return append_log(self.case_dir(case_id) / "events.jsonl",
                          {"case": case_id, "actor": actor, "type": type_, "data": data, "reason": reason})

    def events(self, case_id: str) -> list[dict]:
        return read_log(self.case_dir(case_id) / "events.jsonl")

    # ---- notifications ------------------------------------------------------------------
    def queue_notification(self, case_id: str | None, to: str, template: str) -> dict:
        n = {"to": to, "template": template, "case_id": case_id,
             "link": f"/review?case={case_id}" if case_id else None, "created_at": now()}
        name = f"{now().replace(':', '').replace('.', '')}-{template}.json"
        atomic_write(self.root / "outbox" / name, json.dumps(n, indent=1).encode())
        return n
