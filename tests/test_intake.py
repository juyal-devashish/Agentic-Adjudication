"""Intake behaviour beyond the kit's TC-INT cases: crash safety, integrity tools on a
real import, inbox edge cases. Uses the kit's data as input."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from adjud.intake import IntakePipeline
from adjud.integrity import rebuild_all, verify_all

KIT = Path(__file__).resolve().parents[1] / "data" / "flow1-testkit"
SCEN = KIT / "inbox_scenarios"
SEQUENCE = json.loads((KIT / "expected" / "intake_expected.json").read_text())["sequence"]


@pytest.fixture
def pipe(tmp_path, clock) -> IntakePipeline:
    return IntakePipeline(tmp_path / "data", KIT / "config", KIT / "edc_mock", clock=clock)


def test_full_sequence_leaves_every_log_verified_and_rebuildable(pipe):
    for f in SEQUENCE:
        pipe.import_file(SCEN / f)
    reports = verify_all(pipe.paths)
    assert reports and all(r.ok for r in reports), [r.problems for r in reports if not r.ok]
    assert not any(r.changed for r in rebuild_all(pipe.paths, write=False))
    for case_id in pipe.store.list_cases():
        (pipe.store.case_dir(case_id) / "case.json").unlink()
    rebuild_all(pipe.paths)
    assert all(r.ok for r in verify_all(pipe.paths))


def test_crash_after_case_creation_does_not_create_a_second_case(pipe):
    """Simulate a crash after the case was written but before the evaluation log and
    index were: the next import must find the existing case by its key."""
    pipe.import_file(SCEN / "01_initial.csv")
    cases_before = pipe.store.list_cases()
    pipe.store.index_path.unlink()
    pipe.store.evaluations_path.unlink()
    result = pipe.import_file(SCEN / "01_initial.csv")
    assert result.new_cases == 0
    assert pipe.store.list_cases() == cases_before


def test_rejected_file_writes_nothing(pipe):
    result = pipe.import_file(SCEN / "05_invalid_rows.csv")
    assert result.status == "REJECTED"
    assert not pipe.store.evaluations_path.exists()
    assert not any(pipe.paths.outbox.iterdir())


def test_unsupported_file_in_inbox_is_moved_with_a_rejection_report(pipe, tmp_path):
    inbox, done = tmp_path / "inbox", tmp_path / "processed"
    inbox.mkdir()
    (inbox / "notes.txt").write_text("not an AE file")
    (results,) = [pipe.process_inbox(inbox, done)]
    assert [r.status for r in results] == ["REJECTED"]
    report = json.loads((done / "notes.txt.report.json").read_text())
    assert report["errors"][0]["code"] == "UNSUPPORTED_TYPE"
    assert not any(inbox.iterdir())


def test_processed_files_are_never_overwritten(pipe, tmp_path):
    inbox, done = tmp_path / "inbox", tmp_path / "processed"
    inbox.mkdir()
    for _ in range(2):
        shutil.copy(SCEN / "01_initial.csv", inbox)
        pipe.process_inbox(inbox, done)
    csvs = sorted(p.name for p in done.glob("01_initial*.csv"))
    assert len(csvs) == 2
    assert len(list(done.glob("*.report.json"))) == 2


def test_case_created_event_records_actor_source_and_snapshot_hash(pipe):
    result = pipe.import_file(SCEN / "01_initial.csv")
    created = next(o for o in result.outcomes if o["outcome"] == "created")
    first = pipe.store.events(created["case_id"])[0]
    assert first["actor"] == "system:intake"
    assert first["data"]["source"] == "FILE"
    assert first["data"]["snapshot"]["path"] == "source/ae_snapshot_v1.json"
    assert pipe.case_state(created["case_id"])["state"] == "AWAITING_DOCUMENTS"


def test_runner_checks_the_inbox_one_interval_after_its_first_tick(tmp_path):
    """The first automatic import comes one interval after start, so a demo reset leaves
    the queued file for "Import queued files". Regression: it must not depend on the
    machine's monotonic clock (a fresh machine once waited until the clock passed it)."""
    from adjud.paths import DataPaths
    from adjud.runner import Runner

    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(KIT.parent / "config")
    (data / "edc_mock").symlink_to(KIT / "edc_mock")
    runner = Runner(DataPaths(data))
    (data / "inbox").mkdir()
    shutil.copy(SCEN / "01_initial.csv", data / "inbox")
    runner.tick(now=5.0)  # first tick, 5 s after boot: the timer starts, nothing imported
    assert not runner.pipeline.store.list_cases()
    runner.tick(now=60.0)  # before the 5-minute interval: still queued
    assert (data / "inbox" / "01_initial.csv").exists()
    runner.tick(now=5.0 + 300)  # one interval after the first tick
    assert len(runner.pipeline.store.list_cases()) == 11
    shutil.copy(SCEN / "03_updates.csv", data / "inbox")
    runner.tick(now=5.0 + 400)
    assert (data / "inbox" / "03_updates.csv").exists()
    runner.tick(now=5.0 + 600)
    assert not (data / "inbox" / "03_updates.csv").exists()


def test_edc_pull_imports_study_records_but_not_history(tmp_path, clock):
    from adjud.edc_pull import pull

    data = tmp_path / "data"
    (data / "edc_mock").mkdir(parents=True)
    for f in (KIT / "edc_mock").iterdir():
        (data / "edc_mock" / f.name).write_bytes(f.read_bytes())
    pipe = IntakePipeline(data, Path(__file__).resolve().parents[1] / "data" / "config",
                          data / "edc_mock", clock=clock)  # fmt: skip
    pipe.import_file(SCEN / "01_initial.csv")
    (result,) = pull(pipe, "dm.dana")
    assert result.status == "ACCEPTED" and result.file.startswith("edc_pull_")
    keys = [o["key"] for o in result.outcomes]
    assert keys and not any(k.split("|")[2].startswith("H-") for k in keys)
    assert result.new_cases > 0  # AEs the EDC has but the first file did not
    (again,) = pull(pipe, "dm.dana")
    assert again.new_cases == 0 and again.count("changed") == 0  # idempotent
