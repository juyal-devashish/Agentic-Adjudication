"""The Slack Agent's tools and its PHI gate (plan P-58): what each role's tools may see,
enforced in code (Q5, Q11), and the check on every text the Agent posts (Q17), with the
kit's seeded PHI. Nothing here reaches Slack or the Claude API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjud.ai.tools import ToolError
from adjud.documents import upload
from adjud.paths import DataPaths
from adjud.runner import Runner
from adjud.slack_app.agent_tools import agent_toolbox
from adjud.slack_app.core import Workspace
from adjud.slack_app.phi_gate import PhiGate
from tests.test_flow3 import pdf, to_qc_review
from tools.reset_demo import DEMO_AES, reset

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"


@pytest.fixture
def data(tmp_path, monkeypatch) -> DataPaths:
    root = tmp_path / "data"
    root.mkdir()
    (root / "config").symlink_to(ROOT / "data" / "config")
    (root / "flow1-testkit").symlink_to(KIT)  # the seeded identities the gate searches
    monkeypatch.setenv("ADJUD_DATA_DIR", str(root))
    paths = DataPaths(root)
    reset(paths, preload=True, only=DEMO_AES)
    return paths


@pytest.fixture
def ws(data) -> Workspace:
    """Three cases: DEMO01-0001 and -0003 at site 101 (Priya), -0002 at site 102 (Tom)."""
    w = Workspace(data)
    w.import_queued(w.people.by_id["dm.dana"])
    return w


def tool(ws: Workspace, person_id: str, name: str, **args) -> dict:
    return json.loads(agent_toolbox(ws, ws.people.by_id[person_id]).call(name, args))


def tool_names(ws: Workspace, person_id: str) -> set[str]:
    return {t["name"] for t in agent_toolbox(ws, ws.people.by_id[person_id]).definitions()}


# -- what each role may see (Q5, Q11), enforced in the tools -----------------------------------


def test_each_role_has_only_its_own_tools(ws):
    everyone = {"my_tasks", "my_cases", "case_status"}
    assert tool_names(ws, "dm.dana") == everyone | {"waiting_files", "records_not_a_case",
                                                    "case_summary", "import_waiting_files",
                                                    "pull_from_edc"}  # fmt: skip
    assert tool_names(ws, "site101.coord") == everyone | {
        "case_summary", "required_documents", "redaction_results", "query_text",
        "upload_attached_files"}  # fmt: skip
    assert tool_names(ws, "ss.anna") == everyone | {
        "case_summary", "qc_checks", "ai_suggestion", "dossier_summary", "narrative_draft",
        "query_text", "edit_narrative_draft", "draft_site_query"}  # fmt: skip
    for flows_3_4 in ("cec.sofia", "adj.okafor", "adj.lindqvist"):
        assert tool_names(ws, flows_3_4) == everyone


def test_a_site_sees_only_its_own_cases_and_another_sites_case_is_refused(ws):
    cases = [c["case"] for c in tool(ws, "site101.coord", "my_cases")["cases"]]
    assert cases == ["DEMO01-0001", "DEMO01-0003"]
    for name in ("case_status", "case_summary", "required_documents", "redaction_results",
                 "query_text"):  # fmt: skip
        with pytest.raises(ToolError) as theirs:
            tool(ws, "site101.coord", name, case="DEMO01-0002")  # Tom's site
        with pytest.raises(ToolError) as nobodys:
            tool(ws, "site101.coord", name, case="DEMO01-0099")
        # the same answer: a site cannot tell another site's case from no case
        assert str(theirs.value).replace("0002", "N") == str(nobodys.value).replace("0099", "N")
    assert tool(ws, "site102.coord", "case_status", case="0002")["case"] == "DEMO01-0002"
    summary = tool(ws, "site101.coord", "case_summary", case="0001")
    assert summary["ae_term"] == "Myocardial infarction" and summary["onset_date"] == "2026-03-14"
    assert summary["site"] == "Larkfield General Hospital"


def test_adjudicators_see_only_assigned_cases(ws):
    assert tool(ws, "adj.okafor", "my_cases")["cases"] == []
    with pytest.raises(ToolError):
        tool(ws, "adj.okafor", "case_status", case="0001")


def test_the_data_manager_sees_the_queue_and_why_a_record_is_not_a_case(data):
    w = Workspace(data)
    assert tool(w, "dm.dana", "waiting_files") == {"count": 1, "files": ["01_initial.csv"]}
    w.import_queued(w.people.by_id["dm.dana"])
    assert tool(w, "dm.dana", "waiting_files")["count"] == 0
    (rec,) = tool(w, "dm.dana", "records_not_a_case", ae_id="")["records"]
    assert rec["ae_id"] == "AE-008"
    assert rec["rules_not_met"][0]["rule"] == "R1"
    assert "serious is 'N'" in rec["rules_not_met"][0]["why"]
    assert tool(w, "dm.dana", "records_not_a_case", ae_id="ae-001")["records"] == []
    assert len(tool(w, "dm.dana", "my_cases")["cases"]) == 3  # every case


def test_the_site_sees_redaction_counts_and_the_safety_specialist_the_quality_check(ws):
    runner = Runner(ws.paths)
    store = ws.store
    upload(store, "DEMO01-0001", [("summary.pdf", pdf("DISCHARGE SUMMARY\nPatient: ROBERTS, "
                                                      "Philip\nChest pain."))],
           "site101.coord")  # fmt: skip
    required = tool(ws, "site101.coord", "required_documents", case="0001")
    assert required["required"] == ["Discharge or death summary", "ECG report"]
    assert required["uploaded"] == [{"file": "summary.pdf", "type": None}]  # typed when read
    to_qc_review(runner, store, "DEMO01-0001")
    required = tool(ws, "site101.coord", "required_documents", case="0001")
    assert required["missing"] == ["ECG report"]
    res = tool(ws, "site101.coord", "redaction_results", case="0001")
    (f,) = res["files"]
    assert (f["file"], f["status"], f["identifiers_left"]) == ("summary.pdf", "verified", 0)
    assert f["removed_by_kind"].get("patient names", 0) >= 1
    assert "Ro" not in json.dumps(res) and "…" not in json.dumps(res)  # counts, never text
    checks = tool(ws, "ss.anna", "qc_checks", case="0001")
    assert checks["waiting_for_review"] and checks["suggested_outcome"] in ("pass", "query",
                                                                            "none")  # fmt: skip
    assert {c["check"] for c in checks["checks"]}
    assert tool(ws, "ss.anna", "ai_suggestion", case="0001")["suggestion"] is None  # AI off
    assert tool(ws, "ss.anna", "dossier_summary", case="0001")["documents"][0]["file"] == (
        "summary.pdf"
    )
    draft = tool(ws, "ss.anna", "narrative_draft", case="0001")
    assert draft["source"] == "study template" and not draft["approved"]
    assert "DEMO01-0001" in draft["text"]
    assert tool(ws, "ss.anna", "query_text", case="0001")["query"] is None
    with pytest.raises(ToolError):
        tool(ws, "ss.anna", "qc_checks", case="0003")  # no Quality Check yet


# -- the PHI gate (Q17) --------------------------------------------------------------------------


def seeded_phi(subject: str | None = None) -> list[tuple[str, str]]:
    """The kit's must-redact identifiers (ground truth), of one subject or of all."""
    out = []
    for f in sorted((KIT / "ground_truth").glob("D*.json")):
        gt = json.loads(f.read_text())
        if subject in (None, gt["subject_id"]):
            out += [(i["category"], i["value"]) for i in gt["items"]
                    if i["requirement"] == "must_redact"]  # fmt: skip
    return out


def test_the_gate_holds_every_seeded_identifier_of_the_kit(ws):
    gate = PhiGate.for_workspace(ws)
    phi = seeded_phi()
    assert len(phi) > 80
    for category, value in phi:  # about a case of the turn or not
        for cases in (["DEMO01-0001"], []):
            verdict = gate.check(f"The letter on page 1 says {value}, as recorded.", cases)
            assert verdict.held, (category, cases)
            assert value not in json.dumps(verdict.summary)  # categories, never the text
    for value in ("Philip Roberts", "Wendy Roberts", "philip.roberts1@example.org",
                  "24/07/1955", "9 Victoria Ridges, South Joel W95 2AN"):  # fmt: skip
        assert gate.check(f"*{value}*", ["DEMO01-0001"]).held
    assert gate.check("Uploaded `Roberts_Philip_letter.pdf`.", []).held  # in a file name too


def test_the_gate_passes_ordinary_answers_and_the_apps_people(ws):
    gate = PhiGate.for_workspace(ws)
    ordinary = [
        "You have *2 tasks*:\n• `DEMO01-0001` · Upload the documents\n• `DEMO01-0003` · "
        "Review the redacted pages and sign",
        "I act as *Priya Nair (Site user, Larkfield General Hospital)*.",
        "Dr. Anna Weber reviews the Quality Check; Dr. Weber signs in the app. Rahul Mehta "
        "imports the records.",
        "Dr. Daniel Okafor and Dr. Elin Lindqvist are assigned; Sofia Marques records the "
        "committee at Northgate University Hospital.",
        "Katrin Vogel at Klinikum Auenstadt; Tom Hollis at Brennan Valley Medical Centre.",
        "*DEMO01-0001* (subject DEMO01-101-0001, AE-001): Myocardial infarction, onset "
        "2026-03-14, ended 2026-03-21. Serious: hospitalisation. Outcome: recovered.",
        "AE-008 (subject DEMO01-101-0006) is not a case: R1 Serious adverse event is not met "
        "(serious is 'N').",
        "Redaction results for `DEMO01-0001`:\n• discharge_summary.pdf: verified, 2 pages, 17 "
        "identifiers removed (patient names 4, dates of birth 2, addresses 1), none left.",
        "*AI Suggestion* (Simulated AI): Outcome: Discharged home on ongoing medication (not "
        "sure). The patient was on the cardiology ward and troponin rose overnight.",
        "The narrative draft: Subject DEMO01-101-0001 at site 101 had Myocardial infarction "
        "with onset on 2026-03-14. Severity: SEVERE. Outcome: RECOVERED.",
    ]
    for text in ordinary:
        verdict = gate.check(text, ["DEMO01-0001"])
        assert not verdict.held, (text, verdict.found)
    # a patient named like a user is still caught: only the user's own name is allowed
    assert gate.check("The next of kin is Anna Bell.", []).held
    assert gate.check("Dr Rupert Okafor signed the letter.", []).held  # not Dr. Daniel Okafor
