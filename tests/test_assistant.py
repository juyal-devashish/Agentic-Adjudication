"""The Assistant (P-40): a guided chat answered by code from the case records. It lists
each person's tasks for their role and site, explains cases and decisions, runs the data
manager's import, reports what happens as it happens, and links to the page for every
manual step. It never signs or decides anything (ADR 0001)."""

from __future__ import annotations

import pytest

from adjud.paths import DataPaths
from adjud.runner import Runner
from tests.test_ui import ROOT, run
from tools.reset_demo import DEMO_AES, reset


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """The short demo: three cases and one record that is not eligible."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "config").symlink_to(ROOT / "data" / "config")
    monkeypatch.setenv("ADJUD_DATA_DIR", str(data))
    paths = DataPaths(data)
    reset(paths, preload=True, only=DEMO_AES)
    return paths


SITE101 = {"id": "site101.coord", "name": "Priya Nair", "role": "site", "site": "101",
           "email": "priya.nair@larkfield-general.example"}  # fmt: skip


def chat(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def say(at, text: str):
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception
    return at


def test_every_role_lands_on_the_assistant(demo):
    for role in ("data_manager", "site", "safety_specialist"):
        at = run(role=role)
        assert at.title[0].value == "Assistant", role
        assert "Hello" in chat(at)


def test_data_manager_imports_from_the_chat_and_is_told_why_not(demo):
    at = run(role="data_manager")
    assert "waiting to be imported" in chat(at)  # the preloaded EDC export
    say(at, "/import")
    text = chat(at)
    assert "3 new cases" in text and "1 not eligible" in text and "/why AE-008" in text
    say(at, "why is AE-008 not a case?")
    text = chat(at)
    assert "does not meet the study's rules" in text and "Serious: No" in text
    from adjud.store import read_log

    log = list(read_log(demo.root / "intake" / "evaluations.jsonl"))
    assert any(e.get("actor") == "dm.dana" for e in log)  # imported as the data manager


def test_a_plain_message_about_importing_does_not_import(demo):
    """Only /import (or its starter button) imports; "don't import yet" must not."""
    at = run(role="data_manager")
    for text in ("don't import yet, just tell me what is waiting", "how does import work?"):
        say(at, text)
        assert "Type **/import**" in chat(at)
    assert [f.name for f in demo.inbox.iterdir()] == ["01_initial.csv"]
    assert not list(demo.root.glob("cases/*/events.jsonl"))
    assert not list(demo.root.glob("intake/evaluations.jsonl"))


def test_site_sees_only_its_cases_and_gets_a_link_to_upload(demo):
    Runner(demo).run_once()
    at = run(role="site", user_site=SITE101)
    text = chat(at)
    assert "DEMO01-0001" in text and "DEMO01-0003" in text and "DEMO01-0002" not in text
    assert "discharge or death summary and ECG report" in text
    say(at, "/import")
    assert "data manager's job" in chat(at)
    button = next(b for b in at.button if b.label == "Upload documents for DEMO01-0001")
    button.click().run()
    assert not at.exception, at.exception
    assert at.session_state["case_id"] == "DEMO01-0001"


def test_safety_specialist_gets_the_quality_check_summary(demo, monkeypatch):
    from adjud.documents import upload
    from adjud.review import submit
    from tools.reset_demo import KIT

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    (demo.root / "flow1-testkit").symlink_to(KIT)
    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    folder = demo.root / "demo_uploads" / "documents" / "DEMO01-101-0001_AE-001"
    upload(store, "DEMO01-0001", [(f.name, f.read_bytes()) for f in sorted(folder.iterdir())],
           "site101.coord")  # fmt: skip
    runner.run_case_steps()
    submit(store, "DEMO01-0001", "site101.coord", "demo", demo.config)
    at = run(role="safety_specialist")  # opened before the QC round: it is reported live
    for _ in range(3):
        runner.run_case_steps()
    at.run()
    text = chat(at)
    assert "the quality check is ready" in text and "Suggested outcome: **pass**" in text
    say(at, "/qc 0001")
    text = chat(at)
    assert "AI Suggestion** (AI)" in text and "You decide and sign" in text
    assert "outcome: not sure" in text.lower()
    assert any(b.label == "Review and sign DEMO01-0001" for b in at.button)
    assert not any(b.label.startswith("Sign") for b in at.button)  # it never signs


@pytest.mark.parametrize("text", ["hello there", "/help"])
def test_unknown_questions_get_the_list_of_actions(demo, text):
    at = run(role="safety_specialist")
    say(at, text)
    assert "/status 0001" in chat(at)
