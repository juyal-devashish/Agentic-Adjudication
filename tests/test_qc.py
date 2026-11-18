"""Deterministic QC checks (plan step 14) on the Flow 2 fixtures, end to end through the
runner: each scenario gets the proposal written in its expected.json, and every case
then waits for the Safety Specialist (P-37)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

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


def draft_of(store, cid: str) -> tuple[dict, str]:
    """The round's narrative draft (P-39: drafted before the review) and its text."""
    result = store.get_json(cid, "qc/checks_v1.json")
    return result["narrative"], store.get_file(cid, result["narrative"]["draft"]["path"]).decode()


def no_decision_without_a_person(store, cid: str) -> None:
    """P-37: no QC result, query or narrative approval without a signature."""
    for e in store.events(cid):
        if e["type"] in (
            "qc.signed_pass",
            "query.opened",
            "narrative.signed",
            "quality_check.signed",
        ):
            assert e.get("signature"), e["type"]
        assert e["type"] not in ("qc.passed", "narrative.approved"), e["type"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_fixture_gets_its_expected_proposal(tmp_path, fixtures, scenario):
    store, cid = run_scenario(tmp_path, fixtures, scenario)
    expected = make_flow2_fixtures.EXPECTED[scenario]
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["proposal"] == expected["proposal"], result["checks"]
    assert result["query_codes"] == expected["codes"]
    assert store.state(cid).state == State.QC_REVIEW  # always the Safety Specialist's call
    no_decision_without_a_person(store, cid)
    events = (store.case_dir(cid) / "events.jsonl").read_text() + json.dumps(result)
    assert "Roberts" not in events and "24/07/1955" not in events  # verdicts, never values


def test_a_check_failed_by_code_cannot_be_signed_as_passed(tmp_path, fixtures):
    """A clear failure still always ends in a query, now one the Safety Specialist signs."""
    from adjud.flow2 import Flow2, Flow2Refused

    store, cid = run_scenario(tmp_path, fixtures, "missing_document")
    flow2 = Flow2(store, CONFIG)
    with pytest.raises(Flow2Refused, match="send a query"):
        flow2.sign_qc_pass(cid, "ss.anna", "demo", "A narrative.", "I disagree")
    flow2.open_query(cid, ["missing_document"], "Please upload the ECG report.",
                     user_id="ss.anna", password="demo")  # fmt: skip
    assert store.state(cid).state == State.QUERY_OPEN
    no_decision_without_a_person(store, cid)


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


def test_ai_suggestion_finds_a_mismatch_with_the_edc_and_suggests_a_query(
    tmp_path, fixtures, monkeypatch
):
    """P-39: the documents describe an earlier episode. The AI Suggestion's onset finding
    is a verified mismatch, so code suggests a Site Query; the specialist may overrule it,
    with a reason, because the finding is the AI's, not code's."""
    from adjud.flow2 import Flow2, Flow2Refused

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    store, cid = run_scenario(tmp_path, fixtures, "different_event")
    expected = make_flow2_fixtures.EXPECTED["different_event"]["with_ai"]
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["proposal"] == expected["proposal"] == "query"
    assert result["query_codes"] == expected["codes"]
    fields = {f["field"]: f for f in result["suggestion"]["fields"]}
    onset = fields["onset_date"]
    assert onset["status"] == "mismatch" and onset["edc_value"] == "2026-03-14"
    assert onset["document_value"] == "02/11/2025" and onset["evidence"]  # quoted, verified
    assert any("Onset date: EDC 2026-03-14, documents 02/11/2025" in r for r in result["reasons"])
    assert result["suggestion"]["summary"] and result["suggestion"]["label"] == "AI"
    assert store.state(cid).state == State.QC_REVIEW
    assert not any(e["type"] == "query.opened" for e in store.events(cid))  # a person sends it
    assert result["failed_by_code"] == []  # the AI's finding: the specialist may overrule it
    _, text = draft_of(store, cid)
    flow2 = Flow2(store, CONFIG)
    with pytest.raises(Flow2Refused, match="give a reason"):
        flow2.sign_qc_pass(cid, "ss.anna", "demo", text)
    flow2.sign_qc_pass(cid, "ss.anna", "demo", text, "Site confirmed the dates by phone")
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION


def test_a_quote_the_page_does_not_contain_sends_the_case_to_a_person(
    tmp_path, fixtures, monkeypatch
):
    import shutil

    replays = tmp_path / "replays"
    shutil.copytree(CONFIG / "DEMO01" / "ai_replays", replays)
    path = next(
        p for p in (replays / "qc_suggestion").glob("*.json")
        if "fracture" in json.loads(p.read_text())["output"]["summary"].lower()
    )  # fmt: skip
    record = json.loads(path.read_text())
    onset = next(f for f in record["output"]["fields"] if f["field"] == "onset_date")
    onset["evidence"][0]["quote"] = "Admitted with pneumonia"  # not on the page
    path.write_text(json.dumps(record))
    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    monkeypatch.setattr(
        "adjud.flow2.Gateway.for_study",
        classmethod(lambda cls, store, config_dir, study="DEMO01": _gateway(cls, store, replays)),
    )
    store, cid = run_scenario(tmp_path, fixtures, "different_event")
    result = store.get_json(cid, "qc/checks_v1.json")
    onset = next(f for f in result["suggestion"]["fields"] if f["field"] == "onset_date")
    assert onset["status"] == "not_sure"  # an unverified quote is never a finding
    assert any("quote not found on" in p for p in onset["problems"])
    assert result["proposal"] != "pass"
    assert store.state(cid).state == State.QC_REVIEW


def _gateway(cls, store, replays):
    from adjud.ai.config import AiConfig

    model = yaml.safe_load((CONFIG / "DEMO01" / "study.yaml").read_text())["ai"]["model"]
    cfg = AiConfig(
        mode="replay",
        model=model,  # the study's pinned model: replays are keyed by it
        functions={"document_type": True, "qc_suggestion": True, "narrative": True},
    )
    return cls(store, cfg, replays)


def test_agent_types_the_kit_letters_code_could_not(tmp_path, monkeypatch):
    """Kit AE-005 (fatal, a poor-quality GP summary letter): the recorded live agent types
    it as a discharge summary but is unsure (0.6), so the gate sends it to the Safety
    Specialist with no proposal. AE-006 (a clinic letter and an angiography report, no
    discharge summary or ECG) gets a query proposed for both."""
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
    assert store.state(cases["AE-005"]).state == State.QC_REVIEW
    result = store.get_json(cases["AE-005"], "qc/checks_v1.json")
    entry = next(a for a in result["ai"] if a["task"] == "Document type")
    assert entry["label"] == "AI" and not entry["gate"]["passed"]
    assert any("below 0.80" in r for r in entry["gate"]["reasons"])
    assert result["proposal"] == "unclear"
    ae006 = store.get_json(cases["AE-006"], "qc/checks_v1.json")
    assert ae006["proposal"] == "query" and ae006["query_codes"] == ["missing_document"]
    assert any("Discharge or death summary, ECG report" in r for r in ae006["reasons"])
    documents = ae006["suggestion"]["documents"]  # the documents finding: code's, with advice
    assert documents["status"] == "problem"
    assert documents["required"] == ["Discharge or death summary", "ECG report"]
    assert store.state(cases["AE-006"]).state == State.QC_REVIEW


# --- narrative agent in replay mode (plan step 17) --------------------------------------------


def _complete_narrative(replays: Path) -> Path:
    return next(
        p for p in (replays / "narrative").glob("*.json")
        if [d["name"] for d in json.loads(p.read_text())["input"]["dossier"]["page_map"]]
        == ["discharge_summary.pdf", "ecg_report.pdf"]
    )  # fmt: skip


def test_clean_case_gets_a_suggested_pass_with_the_narrative_to_sign(
    tmp_path, fixtures, monkeypatch
):
    """P-39 on a clean case: the AI Suggestion, the dossier and the cited AI narrative are
    all ready at the one review; one signature passes QC and approves the narrative."""
    from adjud.flow2 import Flow2
    from adjud.runner import Runner

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    store, cid = run_scenario(tmp_path, fixtures, "complete")
    result = store.get_json(cid, "qc/checks_v1.json")
    assert result["proposal"] == "pass" and result["suggestion"]["status"] == "ok"
    assert store.state(cid).state == State.QC_REVIEW  # a person signs every case
    narrative, text = draft_of(store, cid)
    assert narrative["source"] == "ai" and narrative["gate_passed"] and not narrative["simulated"]
    assert "myocardial infarction" in text.lower() and "[dossier p. 2]" in text
    Flow2(store, CONFIG).sign_qc_pass(cid, "ss.anna", "demo", text)
    Runner(store.paths).run_case_steps()  # Flow 3 handoff
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION
    signed = next(e for e in store.events(cid) if e["type"] == "quality_check.signed")
    assert signed["data"]["source"] == "ai" and not signed["data"]["edited"]
    assert signed["data"]["dossier"]["version"] == 1
    no_decision_without_a_person(store, cid)


def test_forbidden_wording_is_flagged_in_the_draft_the_specialist_corrects(
    tmp_path, fixtures, monkeypatch
):
    import shutil

    from adjud.flow2 import Flow2

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
    assert store.state(cid).state == State.QC_REVIEW
    narrative, text = draft_of(store, cid)
    assert narrative["source"] == "ai" and not narrative["gate_passed"]
    assert "forbidden wording: caused by, probably" in narrative["reasons"][0]
    fixed = text.replace(" It was probably caused by the study drug.", "")
    Flow2(store, CONFIG).sign_qc_pass(cid, "ss.anna", "demo", fixed)
    signed = next(e for e in store.events(cid) if e["type"] == "quality_check.signed")["data"]
    assert signed["source"] == "ai" and signed["edited"]
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION


def test_a_wrong_password_at_the_pass_writes_nothing_and_the_next_try_signs(tmp_path, fixtures):
    """Signed first: a wrong password leaves no narrative file behind, so the retry with
    the right password is not blocked by a name an earlier attempt already took."""
    import re

    from adjud.flow2 import Flow2
    from adjud.signatures import SignatureRefused

    store, cid = run_scenario(tmp_path, fixtures, "complete")
    assert store.state(cid).state == State.QC_REVIEW
    _, draft = draft_of(store, cid)
    text = re.sub(r"\[Add[^\]]*\]", "Chest pain treated with a stent.", draft)
    flow2 = Flow2(store, CONFIG)
    with pytest.raises(SignatureRefused):
        flow2.sign_qc_pass(cid, "ss.anna", "wrong", text, "Documents present")
    assert not list(store.case_dir(cid).glob("narrative/narrative_v*.md"))
    flow2.sign_qc_pass(cid, "ss.anna", "demo", text, "Documents present")
    assert store.state(cid).state == State.READY_FOR_ADJUDICATION
    no_decision_without_a_person(store, cid)
