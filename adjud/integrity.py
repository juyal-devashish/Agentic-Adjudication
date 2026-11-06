"""Chain verification and state rebuild, shared by tools/verify_log.py and
tools/rebuild_state.py (mvp-spec.md §4.4, MNF-04/05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from adjud.chain import ChainedLog, check_chain
from adjud.events import file_refs, sha256_bytes
from adjud.paths import DataPaths
from adjud.store import intake_log, write_atomic
from adjud.workflow import TransitionRefused, replay


@dataclass
class VerifyReport:
    log: Path
    records: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_case_log(case_dir: Path) -> VerifyReport:
    """Chain, case id on every line, workflow replay, and every referenced file."""
    log = ChainedLog(case_dir / "events.jsonl")
    check = check_chain(log, expect={"case": case_dir.name})
    report = VerifyReport(log=log.path, records=check.records, problems=list(check.problems))
    if not check.ok:
        return report
    records = log.records()
    for i, rec in enumerate(records, start=1):
        for ref in file_refs(rec.get("data")):
            path = case_dir / ref["path"]
            if not path.exists():
                report.problems.append(f"line {i}: referenced file missing: {ref['path']}")
            elif sha256_bytes(path.read_bytes()) != ref["sha256"]:
                report.problems.append(f"line {i}: referenced file changed: {ref['path']}")
    try:
        replay(records)
    except TransitionRefused as exc:
        report.problems.append(f"workflow replay failed: {exc}")
    return report


def case_dirs(paths: DataPaths) -> list[Path]:
    if not paths.cases.exists():
        return []
    return sorted(p for p in paths.cases.iterdir() if (p / "events.jsonl").exists())


def verify_all(paths: DataPaths) -> list[VerifyReport]:
    reports = [verify_case_log(d) for d in case_dirs(paths)]
    intake = intake_log(paths)
    if intake.path.exists():
        check = check_chain(intake)
        reports.append(VerifyReport(intake.path, check.records, list(check.problems)))
    return reports


@dataclass
class RebuildResult:
    case_id: str
    changed: bool  # True if the existing case.json differed (or was missing)


def rebuild_all(paths: DataPaths, *, write: bool = True) -> list[RebuildResult]:
    """Replay every case log into case.json. With write=False, only compare."""
    results = []
    for folder in case_dirs(paths):
        expected = replay(ChainedLog(folder / "events.jsonl").records()).to_json().encode()
        cache = folder / "case.json"
        changed = not cache.exists() or cache.read_bytes() != expected
        if write and changed:
            write_atomic(cache, expected, overwrite=True)
        results.append(RebuildResult(case_id=folder.name, changed=changed))
    return results
