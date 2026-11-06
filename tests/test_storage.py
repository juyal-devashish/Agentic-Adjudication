"""Step 1: event log, hash chain, workflow table, rebuild and verify."""

from __future__ import annotations

import json

import pytest

from adjud.chain import ChainedLog, check_chain
from adjud.events import GENESIS_HASH, EventDraft, record_hash
from adjud.integrity import rebuild_all, verify_all
from adjud.store import CaseExists, intake_log, verify_log
from adjud.workflow import State, TransitionRefused

CASE = "DEMO01-0001"
SYSTEM = "system:runner"


def created(**data) -> EventDraft:
    return EventDraft(
        type="case.created",
        actor=SYSTEM,
        data={"study": "DEMO01", "subject": "DEMO01-104-0007", "ae_id": "AE-12", **data},
    )


def make_case(store, case_id: str = CASE) -> None:
    store.create_case(case_id, created())
    store.append_event(case_id, EventDraft(type="edc.related_data_pulled", actor=SYSTEM))


def upload(store, case_id: str = CASE, name: str = "a.pdf", content: bytes = b"%PDF-1") -> None:
    rel = f"documents/original/{name}"
    sha = store.put_file(case_id, rel, content)
    store.append_event(
        case_id,
        EventDraft(
            type="documents.uploaded",
            actor="user:site.maria",
            data={"files": [{"path": rel, "sha256": sha}]},
        ),
    )


# --- chain --------------------------------------------------------------------------


def test_events_are_chained_from_genesis(store):
    make_case(store)
    events = store.events(CASE)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["prev_hash"] == GENESIS_HASH
    assert events[1]["prev_hash"] == events[0]["hash"]
    assert all(record_hash(e) == e["hash"] for e in events)
    assert events[0]["at"] == "2026-11-05T10:00:00Z"
    assert events[0]["case"] == CASE


def test_state_follows_the_workflow(store):
    make_case(store)
    assert store.state(CASE).state == State.AWAITING_DOCUMENTS
    upload(store)
    assert store.state(CASE).state == State.DEIDENTIFYING


def test_creating_the_same_case_twice_is_refused(store):
    store.create_case(CASE, created())
    with pytest.raises(CaseExists):
        store.create_case(CASE, created())


# --- refusals -----------------------------------------------------------------------


def test_illegal_transition_is_refused_and_nothing_is_written(store):
    make_case(store)
    before = (store.case_dir(CASE) / "events.jsonl").read_bytes()
    with pytest.raises(TransitionRefused):
        store.append_event(CASE, EventDraft(type="submission.signed", actor="user:site.maria"))
    assert (store.case_dir(CASE) / "events.jsonl").read_bytes() == before


def test_event_referencing_a_missing_file_is_refused(store):
    make_case(store)
    with pytest.raises(ValueError, match="missing or changed"):
        store.append_event(
            CASE,
            EventDraft(
                type="documents.uploaded",
                actor="user:site.maria",
                data={"files": [{"path": "documents/original/x.pdf", "sha256": "sha256:00"}]},
            ),
        )
    assert store.state(CASE).state == State.AWAITING_DOCUMENTS


def test_files_are_never_overwritten(store):
    make_case(store)
    store.put_file(CASE, "documents/original/a.pdf", b"one")
    with pytest.raises(FileExistsError):
        store.put_file(CASE, "documents/original/a.pdf", b"two")
    assert store.get_file(CASE, "documents/original/a.pdf") == b"one"


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "a/../../b"])
def test_paths_outside_the_case_folder_are_refused(store, bad):
    make_case(store)
    with pytest.raises(ValueError, match="unsafe"):
        store.put_file(CASE, bad, b"x")


def test_withdrawn_is_terminal(store):
    make_case(store)
    store.append_event(
        CASE, EventDraft(type="case.withdrawn", actor="user:ss.anna", reason="duplicate")
    )
    with pytest.raises(TransitionRefused):
        upload(store)


# --- verify -------------------------------------------------------------------------


def test_verify_passes_on_an_untouched_case(store, paths):
    make_case(store)
    upload(store)
    reports = verify_all(paths)
    assert [r.ok for r in reports] == [True]


@pytest.mark.parametrize("field", ["actor", "type", "at", "data"])
def test_editing_any_field_of_an_event_is_detected(store, paths, field):
    make_case(store)
    upload(store)
    log = store.case_dir(CASE) / "events.jsonl"
    lines = log.read_text().splitlines()
    ev = json.loads(lines[1])
    ev[field] = {"edited": True} if field == "data" else "edited"
    lines[1] = json.dumps(ev)
    log.write_text("\n".join(lines) + "\n")
    (report,) = verify_all(paths)
    assert not report.ok
    assert any("line 2: content altered" in p for p in report.problems)


def test_deleting_an_event_breaks_the_chain(store, paths):
    make_case(store)
    upload(store)
    log = store.case_dir(CASE) / "events.jsonl"
    lines = log.read_text().splitlines()
    log.write_text("\n".join([lines[0], lines[2]]) + "\n")
    (report,) = verify_all(paths)
    assert not report.ok


def test_changing_a_referenced_file_is_detected(store, paths):
    make_case(store)
    upload(store)
    (store.case_dir(CASE) / "documents/original/a.pdf").write_bytes(b"tampered")
    (report,) = verify_all(paths)
    assert any("referenced file changed" in p for p in report.problems)


def test_deleting_a_referenced_file_is_detected(store, paths):
    make_case(store)
    upload(store)
    (store.case_dir(CASE) / "documents/original/a.pdf").unlink()
    (report,) = verify_all(paths)
    assert any("referenced file missing" in p for p in report.problems)


def test_intake_log_holds_flat_records_and_is_verified(paths, clock):
    log = intake_log(paths, clock)
    log.append({"key": "DEMO01|S1|AE-1", "outcome": "created", "rule_set": "DEMO01-ELIG"})
    log.append({"key": "DEMO01|S1|AE-2", "outcome": "not_eligible", "rule_set": "DEMO01-ELIG"})
    assert log.records()[1]["outcome"] == "not_eligible"
    assert verify_log(log.path) == (True, "ok")
    raw = log.path.read_text().replace('"not_eligible"', '"created"')
    log.path.write_text(raw)
    ok, msg = verify_log(log.path)
    assert not ok and "line 2" in msg
    assert any(not r.ok for r in verify_all(paths))


def test_reserved_fields_cannot_be_set(paths):
    with pytest.raises(ValueError, match="may not set"):
        intake_log(paths).append({"hash": "sha256:forged"})


def test_new_case_ids_are_sequential_per_study(store):
    assert store.new_case_id("DEMO01") == "DEMO01-0001"
    assert store.new_case_id("DEMO01") == "DEMO01-0002"
    store.create_case("DEMO01-0002", created())
    assert store.list_cases() == ["DEMO01-0002"]  # folders without a log are not cases


# --- rebuild ------------------------------------------------------------------------


def test_rebuild_reproduces_case_json_exactly(store, paths):
    for n in (1, 2, 3):
        make_case(store, f"DEMO01-000{n}")
    upload(store, "DEMO01-0002")
    originals = {p: p.read_bytes() for p in paths.cases.glob("*/case.json")}
    for p in originals:
        p.unlink()
    results = rebuild_all(paths)
    assert all(r.changed for r in results)
    assert {p: p.read_bytes() for p in paths.cases.glob("*/case.json")} == originals
    assert not any(r.changed for r in rebuild_all(paths, write=False))


def test_stale_cache_is_ignored(store):
    make_case(store)
    cache = store.case_dir(CASE) / "case.json"
    stale = cache.read_bytes()
    upload(store)
    cache.write_bytes(stale)
    assert store.state(CASE).state == State.DEIDENTIFYING


# --- crash safety -------------------------------------------------------------------


def test_orphan_file_without_event_leaves_case_consistent(store, paths):
    """Crash between 'write file' and 'append event': the file exists, no event does."""
    make_case(store)
    store.put_file(CASE, "documents/original/orphan.pdf", b"%PDF")
    assert store.state(CASE).state == State.AWAITING_DOCUMENTS
    assert all(r.ok for r in verify_all(paths))
    upload(store, name="retry.pdf")  # the step simply runs again
    assert store.state(CASE).state == State.DEIDENTIFYING


def test_torn_last_write_is_recovered_on_next_append(store, paths):
    """Crash in the middle of writing an event line: the next append must still
    produce a valid chain, and the torn fragment must not become an event."""
    make_case(store)
    log_path = store.case_dir(CASE) / "events.jsonl"
    with log_path.open("ab") as fh:
        fh.write(b'{"seq":3,"at":"2026-11-05T10:0')  # crash mid-line
    assert len(store.events(CASE)) == 2  # fragment is not parsed as an event

    upload(store)

    events = store.events(CASE)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[2]["type"] == "documents.uploaded"
    assert check_chain(ChainedLog(log_path), expect={"case": CASE}).ok


def test_torn_fragment_is_quarantined_not_lost(store):
    make_case(store)
    log_path = store.case_dir(CASE) / "events.jsonl"
    with log_path.open("ab") as fh:
        fh.write(b'{"seq":3,"at":"partial')
    upload(store)
    assert (log_path.parent / "events.jsonl.torn").read_bytes() == b'{"seq":3,"at":"partial\n'
