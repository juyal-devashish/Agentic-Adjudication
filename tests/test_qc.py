"""Deterministic QC checks (plan step 14) on the Flow 2 fixtures, end to end through the
runner: each scenario gets the decision written in its expected.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjud.deid.identity import DocIdentity, conflicts, dob_keys
from adjud.documents import upload
from adjud.paths import DataPaths
from adjud.qc import Checklist, _ids
from adjud.review import submit
from adjud.runner import Runner
from adjud.workflow import State
from tools import make_flow2_fixtures
from tools.reset_demo import reset

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "config"
SCENARIOS = list(make_flow2_fixtures.EXPECTED)


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("flow2")
    make_flow2_fixtures.make(out)
    return out


def run_scenario(tmp_path: Path, fixtures: Path, scenario: str):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(CONFIG)
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for(json.loads((fixtures / "expected.json").read_text())["case"])
    files = sorted((fixtures / scenario).iterdir())
    upload(store, cid, [(f.name, f.read_bytes()) for f in files], "site101.coord")
    runner.run_case_steps()  # de-identification
    submit(store, cid, "site101.coord", "demo", paths.config)
    for _ in range(3):  # handoff, QC start, QC round
        runner.run_case_steps()
    return store, cid


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_fixture_gets_its_expected_decision(tmp_path, fixtures, scenario):
    store, cid = run_scenario(tmp_path, fixtures, scenario)
    expected = make_flow2_fixtures.EXPECTED[scenario]
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["decision"] == expected["decision"], result["checks"]
    state = store.state(cid).state
    if expected["decision"] == "pass":
        assert state in (State.NARRATIVE, State.NARRATIVE_REVIEW)  # the narrative follows
        assert (
            next(e for e in store.events(cid) if e["type"] == "qc.passed")["actor"] == "system:qc"
        )
    elif expected["decision"] == "query":
        assert state == State.QUERY_OPEN
        query = next(e for e in store.events(cid) if e["type"] == "query.opened")
        assert query["data"]["codes"] == expected["codes"]
    else:
        assert state == State.QC_REVIEW
    events = (store.case_dir(cid) / "events.jsonl").read_text() + json.dumps(result)
    assert "Roberts" not in events and "24/07/1955" not in events  # verdicts, never values


def test_types_come_from_the_first_title_keyword():
    cl = Checklist.for_study(CONFIG, "DEMO01")
    assert (
        cl.keyword_type("Cardiology\nDISCHARGE SUMMARY\nThe ECG showed ...") == "discharge_summary"
    )
    assert cl.keyword_type("12-LEAD ECG REPORT\nRecorded 14/03/2026") == "ecg_report"
    assert cl.keyword_type("Klinik für Neurologie\nENTLASSUNGSBRIEF") == "discharge_summary"
    assert cl.keyword_type("Some letter without a title") is None
    # letterheads and body text do not decide the type
    assert cl.keyword_type("Cardiac Catheter Laboratory\nCORONARY ANGIOGRAPHY REPORT") == (
        "imaging_report"
    )
    assert cl.keyword_type("Seen in clinic. The ECG showed sinus rhythm.") is None
    assert cl.keyword_type("S26-4412 | TROPONIN_T | 1240", spreadsheet=True) == "lab_results"


def test_required_documents_depend_on_the_event():
    cl = Checklist.for_study(CONFIG, "DEMO01")
    assert cl.required_for({"soc_code": "90010001", "outcome": "RECOVERED"}) == [
        "discharge_summary", "ecg_report",
    ]  # fmt: skip
    assert cl.required_for({"soc_code": "90010001", "outcome": "FATAL"}) == ["discharge_summary"]
    assert cl.required_for({"soc_code": "90010002"}) == ["discharge_summary"]


def test_subject_ids_survive_ocr_misreads():
    text = "Trial ID: DEMOO1-101-0005 ... Studien-ID DEMOO01-104-0006 ... DEMO02-101-0001"
    assert _ids(text, "DEMO01") == {"DEMO01-101-0005", "DEMO01-104-0006"}


def test_identity_compares_dates_and_names_leniently():
    assert dob_keys("24/07/1955") & dob_keys("1955-07-24")
    assert dob_keys("05.07.55") & dob_keys("07/05/1955")  # day and month could be swapped
    a = DocIdentity([dob_keys("24/07/1955")], {"Philip", "Roberts"})
    b = DocIdentity([dob_keys("24.07.1955")], {"Roberts"})
    c = DocIdentity([dob_keys("02/11/1961")], {"Margaret", "Hill"})
    assert conflicts({"a": a, "b": b}) == {
        "a": {"dob": [], "name": []},
        "b": {"dob": [], "name": []},
    }
    assert conflicts({"a": a, "c": c})["a"] == {"dob": ["c"], "name": ["c"]}
    assert conflicts({"a": a, "d": DocIdentity()})["a"] == {"dob": [], "name": []}  # no data
