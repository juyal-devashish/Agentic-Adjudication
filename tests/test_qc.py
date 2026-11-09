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


# --- QC agent in replay mode (plan step 15) ---------------------------------------------------


def test_agent_spots_a_different_event_and_queries_the_site(tmp_path, fixtures, monkeypatch):
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    store, cid = run_scenario(tmp_path, fixtures, "different_event")
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["decision"] == "query"
    query = next(e for e in store.events(cid) if e["type"] == "query.opened")
    assert query["actor"] == "agent:qc" and query["data"]["codes"] == ["wrong_event"]
    assert "hip fracture" in query["data"]["text"]
    (entry,) = result["ai"]
    assert entry["label"] == "Simulated AI" and entry["gate"]["passed"]
    assert any(e["type"] == "ai.gate" and e["data"]["passed"] for e in store.events(cid))


def test_a_quote_the_page_does_not_contain_sends_the_case_to_a_person(
    tmp_path, fixtures, monkeypatch
):
    import shutil

    replays = tmp_path / "replays"
    shutil.copytree(CONFIG / "DEMO01" / "ai_replays", replays)
    (path,) = (replays / "event_match").glob("*.json")
    record = json.loads(path.read_text())
    record["output"]["evidence"][0]["quote"] = "Admitted with pneumonia"  # not on the page
    path.write_text(json.dumps(record))
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    monkeypatch.setattr(
        "adjud.flow2.Gateway.for_study",
        classmethod(lambda cls, store, config_dir, study="DEMO01": _gateway(cls, store, replays)),
    )
    store, cid = run_scenario(tmp_path, fixtures, "different_event")
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["decision"] == "review"
    assert any("quote not found on discharge_summary.pdf page 1" in r for r in result["reasons"])
    assert store.state(cid).state == State.QC_REVIEW


def _gateway(cls, store, replays):
    from adjud.ai.config import AiConfig

    cfg = AiConfig(
        mode="replay", functions={"document_type": True, "event_match": True, "narrative": True}
    )
    return cls(store, cfg, replays)


def test_agent_types_the_kit_letters_code_could_not(tmp_path, monkeypatch):
    """Kit AE-005 (fatal, a GP summary letter) passes; AE-006 (a clinic letter and an
    angiography report, no discharge summary or ECG) gets a query for both."""
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(CONFIG)
    (data / "flow1-testkit").symlink_to(ROOT / "data" / "flow1-testkit")
    paths = DataPaths(data)
    reset(paths, preload=True)
    runner = Runner(paths)
    runner.run_once()
    store, docs = runner.pipeline.store, ROOT / "data" / "flow1-testkit" / "documents"
    cases = {}
    for folder, site in (("DEMO01-102-0004_AE-005", "102"), ("DEMO01-101-0005_AE-006", "101")):
        subject, ae = folder.split("_")
        cid = runner.pipeline.case_for(f"DEMO01|{subject}|{ae}")
        files = sorted((docs / folder).iterdir())
        upload(store, cid, [(f.name, f.read_bytes()) for f in files], f"site{site}.coord")
        cases[ae] = cid
    runner.run_case_steps()
    for cid in cases.values():
        submit(store, cid, store.state(cid).subject.replace("DEMO01-", "site")[:7] + ".coord",
               "demo", paths.config)  # fmt: skip
    for _ in range(2):
        runner.run_case_steps()
    passed = next(e for e in store.events(cases["AE-005"]) if e["type"] == "qc.passed")
    assert passed["actor"] == "agent:qc" and passed["data"]["by"] == "checks and AI"
    query = next(e for e in store.events(cases["AE-006"]) if e["type"] == "query.opened")
    assert query["actor"] == "agent:qc" and query["data"]["codes"] == ["missing_document"]
    assert "Discharge or death summary, ECG report" in query["data"]["text"]


# --- narrative agent in replay mode (plan step 17) --------------------------------------------


def _complete_narrative(replays: Path) -> Path:
    return next(
        p for p in (replays / "narrative").glob("*.json")
        if [d["name"] for d in json.loads(p.read_text())["input"]["dossier"]["page_map"]]
        == ["discharge_summary.pdf", "ecg_report.pdf"]
    )  # fmt: skip


def test_narrative_agent_writes_a_cited_narrative_nobody_has_to_sign(
    tmp_path, fixtures, monkeypatch
):
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    store, cid = run_scenario(tmp_path, fixtures, "complete")
    from adjud.runner import Runner

    Runner(store.paths).run_case_steps()  # dossier + narrative
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION
    approved = next(e for e in store.events(cid) if e["type"] == "narrative.approved")
    assert approved["actor"] == "agent:narrative" and approved["data"]["simulated"]
    text = store.get_file(cid, approved["data"]["narrative"]["path"]).decode()
    assert "Myocardial infarction" in text and "[dossier p. 2]" in text
    people = {e["actor"] for e in store.events(cid)} - {"site101.coord"}
    assert all(a.startswith(("system:", "agent:")) for a in people)


def test_forbidden_wording_sends_the_ai_draft_to_the_safety_specialist(
    tmp_path, fixtures, monkeypatch
):
    import shutil

    from adjud.flow2 import Flow2
    from adjud.runner import Runner

    replays = tmp_path / "replays"
    shutil.copytree(CONFIG / "DEMO01" / "ai_replays", replays)
    path = _complete_narrative(replays)
    record = json.loads(path.read_text())
    record["output"]["statements"][0]["text"] += " It was probably caused by the study drug."
    path.write_text(json.dumps(record))
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    monkeypatch.setattr(
        "adjud.flow2.Gateway.for_study",
        classmethod(lambda cls, store, config_dir, study="DEMO01": _gateway(cls, store, replays)),
    )
    store, cid = run_scenario(tmp_path, fixtures, "complete")
    Runner(store.paths).run_case_steps()
    assert store.state(cid).state == State.NARRATIVE_REVIEW
    reasons = next(e for e in store.events(cid) if e["type"] == "narrative.review_needed")
    assert "forbidden wording: caused by, probably" in reasons["data"]["reasons"][0]
    draft = next(e for e in store.events(cid) if e["type"] == "narrative.drafted")["data"]
    assert draft["source"] == "ai"
    text = store.get_file(cid, draft["draft"]["path"]).decode()
    fixed = text.replace(" It was probably caused by the study drug.", "")
    Flow2(store, CONFIG).sign_narrative(cid, "ss.anna", "demo", fixed)
    signed = next(e for e in store.events(cid) if e["type"] == "narrative.signed")["data"]
    assert signed["source"] == "ai" and signed["edited"]
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION
