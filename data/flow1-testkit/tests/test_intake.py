"""TC-INT: intake pipeline against the expected results in expected/intake_expected.json."""
import json
import re
import shutil

import pytest

from adjud.store import read_log, verify_log
from conftest import SCEN


def by_key(result):
    return {o["key"]: o for o in result.outcomes}


def case_of(pipe, key):
    cid = pipe.case_for(key)
    assert cid, f"no case for {key}"
    return cid


# ---------------------------------------------------------------- import and eligibility
def test_int01_initial_import_eligibility(pipe, expected):
    exp = expected["scenarios"]["01_initial.csv"]
    r = pipe.import_file(SCEN / "01_initial.csv")
    assert r.status == "ACCEPTED" and r.records == exp["records"]
    assert r.new_cases == exp["new_cases"] and r.count("not_eligible") == exp["not_eligible"]
    got = by_key(r)
    for e in exp["evaluations"]:
        assert got[e["key"]]["eligible"] == e["eligible"], e
        assert sorted(got[e["key"]]["failed_rules"]) == sorted(e["failed_rules"]), e


def test_int02_every_record_logged_with_explanation(pipe):
    pipe.import_file(SCEN / "01_initial.csv")
    log = read_log(pipe.store.evaluations_path)
    assert len(log) == 22
    for e in log:
        assert e["rule_set"] == "DEMO01-ELIG" and e["rule_version"] == 1 and e["explanation"]
    assert sum(e["outcome"] == "not_eligible" for e in log) == 11


def test_int03_reimport_is_idempotent(pipe, expected):
    pipe.import_file(SCEN / "01_initial.csv")
    before = pipe.store.list_cases()
    r = pipe.import_file(SCEN / "02_reimport_identical.csv")
    exp = expected["scenarios"]["02_reimport_identical.csv"]
    assert r.new_cases == 0 and r.count("changed") == 0 and r.count("unchanged") == exp["unchanged"]
    assert pipe.store.list_cases() == before


def test_int04_updates_change_detection(pipe, expected):
    pipe.import_file(SCEN / "01_initial.csv")
    r = pipe.import_file(SCEN / "03_updates.csv")
    exp = expected["scenarios"]["03_updates.csv"]
    got = by_key(r)
    for o in exp["outcomes"]:
        assert got[o["key"]]["outcome"] == o["outcome"], o
        assert got[o["key"]]["eligible"] == o["eligible_now"], o
    for k, flags in exp["flags"].items():
        st = pipe.case_state(case_of(pipe, k))
        assert set(flags) <= set(st["flags"])
        assert st["state"] == "AWAITING_DOCUMENTS", "a downgraded case must be flagged, never withdrawn"
    downgraded = case_of(pipe, exp["outcomes"][1]["key"])
    assert (pipe.store.case_dir(downgraded) / "source/ae_snapshot_v2.json").exists()
    stale_key = [o["key"] for o in exp["outcomes"] if o["outcome"] == "stale"][0]
    stale_case = case_of(pipe, stale_key)
    assert pipe.store.get_json(stale_case, "source/ae_snapshot_v1.json")["ae_term"] == "Myocardial infarction"
    assert any(e["type"] == "source.stale_ignored" for e in pipe.store.events(stale_case))


@pytest.mark.parametrize("fname", ["04_invalid_missing_column.csv", "05_invalid_rows.csv", "10_semicolon_delimited.csv"])
def test_int05_invalid_files_rejected_whole(pipe, expected, fname):
    exp = expected["scenarios"][fname]
    r = pipe.import_file(SCEN / fname)
    assert r.status == "REJECTED" and r.records == 0
    assert pipe.store.list_cases() == [] and read_log(pipe.store.evaluations_path) == []
    got = {(e["row"], e["column"], e["code"]) for e in r.errors}
    for e in exp.get("errors", []):
        assert (e["row"], e["column"], e["code"]) in got, e
    if "errors_include_code" in exp:
        assert exp["errors_include_code"] in {e["code"] for e in r.errors}


def test_int06_invalid_rows_valid_row_has_no_error(pipe):
    r = pipe.import_file(SCEN / "05_invalid_rows.csv")
    assert 2 not in {e["row"] for e in r.errors}
    assert {e["row"] for e in r.errors} == {3, 4, 5, 6, 7, 8}


def test_int07_xlsx_with_excel_and_text_dates(run_sequence, pipe, expected):
    res = run_sequence(upto="06_xlsx_format.xlsx")["06_xlsx_format.xlsx"]
    exp = expected["scenarios"]["06_xlsx_format.xlsx"]
    assert res.status == "ACCEPTED" and res.new_cases == exp["new_cases"]
    got = by_key(res)
    for e in exp["evaluations"]:
        assert got[e["key"]]["eligible"] == e["eligible"] and got[e["key"]]["failed_rules"] == e["failed_rules"]
    snap = pipe.store.get_json(case_of(pipe, exp["evaluations"][0]["key"]), "source/ae_snapshot_v1.json")
    assert snap["onset_date"] == "2026-08-04" and snap["last_modified"] == "2026-08-10T09:00:00Z"


def test_int08_possible_duplicate_flagged(run_sequence, pipe, expected):
    run_sequence(upto="07_duplicate_candidate.csv")
    exp = expected["scenarios"]["07_duplicate_candidate.csv"]
    (k, of_key), = exp["possible_duplicate_of"].items()
    ev = [e for e in pipe.store.events(case_of(pipe, k)) if e["type"] == "case.flagged"]
    assert ev and ev[0]["data"]["flag"] == "possible_duplicate" and ev[0]["data"]["of_key"] == of_key


def test_int09_header_only_file(pipe):
    r = pipe.import_file(SCEN / "08_header_only.csv")
    assert r.status == "ACCEPTED" and r.records == 0


def test_int10_utf8_bom(pipe):
    r = pipe.import_file(SCEN / "09_utf8_bom.csv")
    assert r.status == "ACCEPTED" and r.new_cases == 1
    assert r.outcomes[0]["key"].startswith("DEMO01|")


def test_int11_manual_entry(pipe, kit):
    v = json.loads((SCEN / "manual_entry_valid.json").read_text())
    r = pipe.manual_entry(v["record"], v["reason"], v["actor"])
    assert r.status == "ACCEPTED" and r.new_cases == 1
    created = pipe.store.events(r.outcomes[0]["case_id"])[0]
    assert created["data"]["source"] == "MANUAL" and created["reason"] == v["reason"] and created["actor"] == "ss.anna"
    bad = json.loads((SCEN / "manual_entry_missing_reason.json").read_text())
    r2 = pipe.manual_entry(bad["record"], bad["reason"], bad["actor"])
    assert r2.status == "REJECTED" and r2.errors[0]["code"] == "MISSING_REASON"


# ---------------------------------------------------------------- related data and prior events
def test_int12_related_data_filters(pipe, expected):
    pipe.import_file(SCEN / "01_initial.csv")
    for k, exp in expected["related_data"].items():
        rd = pipe.store.get_json(case_of(pipe, k), "source/related_data.json")
        assert len(rd["medical_history"]) == exp["medical_history"]
        assert sorted(c["drug"] for c in rd["conmeds"]) == sorted(exp["conmeds"])
        assert sorted([l["test"], l["collected_date"]] for l in rd["labs"]) == sorted(exp["labs"])
        assert rd["demographics"] == exp["demographics"]


def test_int13_prior_events(run_sequence, pipe, expected):
    run_sequence(upto="07_duplicate_candidate.csv")
    for k, exp in expected["prior_events"].items():
        got = pipe.store.get_json(case_of(pipe, k), "source/prior_events.json")
        assert [{"ae_id": g["ae_id"], "match": g["match"]} for g in got] == exp, k


# ---------------------------------------------------------------- audit trail, state, notifications
def test_int14_case_log_chain_and_tamper_detection(pipe):
    pipe.import_file(SCEN / "01_initial.csv")
    cid = pipe.store.list_cases()[0]
    path = pipe.store.case_dir(cid) / "events.jsonl"
    assert verify_log(path)[0]
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0]); rec["actor"] = "someone.else"
    path.write_text("\n".join([json.dumps(rec)] + lines[1:]) + "\n")
    ok, msg = verify_log(path)
    assert not ok and "line 1" in msg


def test_int15_evaluation_log_chain_and_index_rebuild(run_sequence, pipe):
    run_sequence()
    assert verify_log(pipe.store.evaluations_path)[0]
    saved = pipe.store.load_index()
    rebuilt = pipe.store.rebuild_index()
    assert {k: v["case_id"] for k, v in rebuilt.items()} == {k: v["case_id"] for k, v in saved.items()}
    assert {k: v["hash"] for k, v in rebuilt.items()} == {k: v["hash"] for k, v in saved.items()}


def test_int16_new_case_state_and_site_notification(pipe):
    r = pipe.import_file(SCEN / "01_initial.csv")
    o = [x for x in r.outcomes if x["outcome"] == "created"][0]
    assert pipe.case_state(o["case_id"])["state"] == "AWAITING_DOCUMENTS"
    notes = [json.loads(p.read_text()) for p in (pipe.store.root / "outbox").glob("*.json")]
    mine = [n for n in notes if n["case_id"] == o["case_id"]]
    assert any(n["to"].startswith("site:") and n["template"] == "documents_requested" for n in mine)


def test_int17_notifications_carry_no_clinical_content(run_sequence, pipe):
    run_sequence()
    snaps = [pipe.store.get_json(c, "source/ae_snapshot_v1.json") for c in pipe.store.list_cases()]
    forbidden = {s[f] for s in snaps for f in ("ae_term", "onset_date", "subject_id") if s[f]}
    for p in (pipe.store.root / "outbox").glob("*.json"):
        text = p.read_text(encoding="utf-8")
        assert not any(v in text for v in forbidden), p.name


def test_int18_values_normalised_and_unicode_preserved(pipe):
    r = by_key(pipe.import_file(SCEN / "01_initial.csv"))
    s14 = pipe.store.get_json(r["DEMO01|DEMO01-101-0013|AE-014"]["case_id"], "source/ae_snapshot_v1.json")
    s15 = pipe.store.get_json(r["DEMO01|DEMO01-102-0014|AE-015"]["case_id"], "source/ae_snapshot_v1.json")
    s16 = pipe.store.get_json(r["DEMO01|DEMO01-104-0015|AE-016"]["case_id"], "source/ae_snapshot_v1.json")
    assert s14["serious"] == "Y" and s15["outcome"] == "FATAL"
    assert s16["ae_term"] == "Infarctus du myocarde aigu \u2013 STEMI"


def test_int19_no_patient_identity_in_store(run_sequence, pipe, identities):
    run_sequence()
    blob = "\n".join(p.read_text(encoding="utf-8") for p in pipe.store.root.rglob("*")
                     if p.is_file() and p.suffix in (".json", ".jsonl"))
    blob = re.sub(r"sha256:[0-9a-f]{64}", "", blob)   # hashes could contain digit runs by chance
    for s in identities.values():
        for f in ("last", "mrn", "phone", "email", "address"):
            assert s[f] not in blob, (s["subject_id"], f)


def test_int20_process_inbox_moves_files_and_reports(pipe, tmp_path):
    inbox, done = tmp_path / "inbox", tmp_path / "processed"
    inbox.mkdir()
    shutil.copy(SCEN / "01_initial.csv", inbox)
    shutil.copy(SCEN / "05_invalid_rows.csv", inbox)
    results = pipe.process_inbox(inbox, done)
    assert [r.status for r in results] == ["ACCEPTED", "REJECTED"]
    assert not any(inbox.iterdir())
    rep = json.loads((done / "05_invalid_rows.csv.report.json").read_text())
    assert rep["status"] == "REJECTED" and len(rep["errors"]) == 6


def test_int21_full_sequence_totals(run_sequence, pipe, expected):
    res = run_sequence()
    created = sum(r.new_cases for r in res.values())
    exp = sum(expected["scenarios"][f].get("new_cases", 0) for f in expected["sequence"])
    assert created == exp == len(pipe.store.list_cases()) == 17
