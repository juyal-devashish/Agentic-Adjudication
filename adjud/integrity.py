"""Chain verification and state rebuild, shared by tools/verify_log.py and
tools/rebuild_state.py (mvp-spec.md §4.4, MNF-04/05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from adjud.chain import ChainedLog, LogCorrupted
from adjud.events import GENESIS_HASH, file_refs, sha256_bytes
from adjud.paths import DataPaths
from adjud.store import intake_log, write_atomic
from adjud.workflow import TransitionRefused, replay


@dataclass
class VerifyReport:
    log: Path
    events: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_chain(log: ChainedLog, files_root: Path | None = None) -> VerifyReport:
    """Check seq order, every hash, every prev_hash link and (if `files_root` is
    given) that every referenced file still exists with the recorded hash."""
    report = VerifyReport(log=log.path)
    try:
        read = log.read()
    except LogCorrupted as exc:
        report.problems.append(str(exc))
        return report
    report.events = len(read.events)
    if read.torn_tail is not None:
        report.problems.append("log ends with an incomplete (torn) write")
    prev = GENESIS_HASH
    for i, ev in enumerate(read.events, start=1):
        where = f"seq {ev.seq}"
        if ev.seq != i:
            report.problems.append(f"{where}: expected seq {i}")
        if ev.prev_hash != prev:
            report.problems.append(f"{where}: prev_hash does not match the previous event")
        if ev.computed_hash() != ev.hash:
            report.problems.append(f"{where}: content does not match its hash (edited)")
        if ev.case != log.case:
            report.problems.append(f"{where}: belongs to {ev.case!r}, not {log.case!r}")
        if files_root is not None:
            for ref in file_refs(ev.data):
                path = files_root / ref["path"]
                if not path.exists():
                    report.problems.append(f"{where}: referenced file missing: {ref['path']}")
                elif sha256_bytes(path.read_bytes()) != ref["sha256"]:
                    report.problems.append(f"{where}: referenced file changed: {ref['path']}")
        prev = ev.hash
    return report


def case_logs(paths: DataPaths) -> list[ChainedLog]:
    if not paths.cases.exists():
        return []
    return [
        ChainedLog(folder / "events.jsonl", case=folder.name)
        for folder in sorted(paths.cases.iterdir())
        if (folder / "events.jsonl").exists()
    ]


def verify_all(paths: DataPaths) -> list[VerifyReport]:
    reports = [verify_chain(log, files_root=log.path.parent) for log in case_logs(paths)]
    for report, log in zip(reports, case_logs(paths), strict=True):
        if report.ok:
            try:
                replay(log.events())
            except TransitionRefused as exc:
                report.problems.append(f"workflow replay failed: {exc}")
    intake = intake_log(paths)
    if intake.path.exists():
        reports.append(verify_chain(intake))
    return reports


@dataclass
class RebuildResult:
    case_id: str
    changed: bool  # True if the existing case.json differed (or was missing)


def rebuild_all(paths: DataPaths, *, write: bool = True) -> list[RebuildResult]:
    """Replay every case log into case.json. With write=False, only compare."""
    results = []
    for log in case_logs(paths):
        expected = replay(log.events()).to_json().encode()
        cache = log.path.parent / "case.json"
        changed = not cache.exists() or cache.read_bytes() != expected
        if write and changed:
            write_atomic(cache, expected, overwrite=True)
        results.append(RebuildResult(case_id=log.case or "", changed=changed))
    return results
