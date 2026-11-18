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


def test_why_names_the_case_when_an_edc_update_downgrades_it(demo):
    """F1-04 / P-14: a record that stops qualifying stays an open, flagged case, so /why
    must name the case, not say it is not one. A record that never became a case still does."""
    from tools.reset_demo import KIT

    at = run(role="data_manager")
    say(at, "/import")
    lines = (KIT / "inbox_scenarios" / "03_updates.csv").read_text().splitlines()
    rows = [lines[0]] + [r for r in lines[1:] if ",AE-006," in r]
    (demo.inbox / "03_ae006_downgrade.csv").write_text("\n".join(rows) + "\n")
    say(at, "/import")
    say(at, "/why AE-006")
    text = chat(at).rsplit("/why AE-006", 1)[-1]
    assert "not a case" not in text
    assert "DEMO01-0003" in text and "flagged" in text and "Serious: No" in text
    assert any("Open DEMO01-0003" in b.label for b in at.button)
    say(at, "/why AE-008")
    assert "so it is not a case" in chat(at).rsplit("/why AE-008", 1)[-1]


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


def test_an_adjudicator_is_told_only_what_gave_them_the_case(demo, monkeypatch):
    """B21: a case that becomes visible after the chat opened is reported from the event
    that made it visible (assignment, Adjudicator 3), not replayed from its Flow 1-2
    history; and only the assigned adjudicators are told a review is theirs."""
    import yaml

    from adjud.documents import upload
    from adjud.flow3 import current_round
    from adjud.workflow import State
    from tests.test_flow3 import SS, answers, pdf, to_qc_review

    monkeypatch.syspath_prepend(str(ROOT / "ui"))  # the UI imports its modules by name
    from assistant import Assistant

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    upload(store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                        ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE101["id"])  # fmt: skip
    runner.flow2.sign_qc_pass(cid, SS, "demo", to_qc_review(runner, store, cid), "Documents")
    users = {u["id"]: u for u in yaml.safe_load((demo.config / "users.yaml").read_text())["users"]}
    chat = {}
    for uid in ("adj.okafor", "adj.lindqvist", "adj.reyes"):
        helper = Assistant(users[uid])
        chat[uid] = (helper, {cs.case_id: cs.seq for cs in helper.cases()})
        assert chat[uid][1] == {}, uid  # nothing assigned yet
    runner.run_case_steps()  # Flow 3 handoff
    runner.run_case_steps()  # the reviews are assigned
    assert store.state(cid).state == State.ADJ_REVIEW
    rnd = current_round(store, cid)
    a1, a2 = rnd.holders["1"], rnd.holders["2"]
    for uid, (helper, seen) in chat.items():
        got = [r.text for r in helper.updates(seen)]
        mine = uid in (a1, a2)
        assert got == ([f"{cid}: an independent review is assigned to you."] if mine else []), uid
    runner.flow3.sign_review(cid, a1, "demo", answers(mi_type="type_1"))
    runner.flow3.sign_review(cid, a2, "demo", answers(mi_type="type_2"))
    runner.run_case_steps()
    assert store.state(cid).state == State.ADJ3_REVIEW
    a3 = current_round(store, cid).holders["3"]
    helper, seen = chat[a3]
    assert [r.text for r in helper.updates(seen)] == [
        f"{cid}: you are Adjudicator 3 for the disputed fields."
    ]


def test_flow3_roles_get_no_ai_suggestion_and_qc_wording_follows_the_state(demo, monkeypatch):
    """B28: the AI Suggestion and the suggested QC outcome are the Safety Specialist's; the
    adjudicators and the CEC Coordinator, whose pages show only the dossier and the signed
    narrative (P-49), are not shown them in the chat. After the signature, /qc no longer
    says the draft is ready to edit or that the reader decides and signs."""
    import yaml

    from tests.test_flow3 import SS, through_qc

    monkeypatch.syspath_prepend(str(ROOT / "ui"))  # the UI imports its modules by name
    from assistant import Assistant

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    from adjud.documents import upload
    from tests.test_flow3 import pdf

    upload(store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                        ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE101["id"])  # fmt: skip
    through_qc(runner, store, cid)
    users = {u["id"]: u for u in yaml.safe_load((demo.config / "users.yaml").read_text())["users"]}
    from adjud.flow3 import current_round

    rnd = current_round(store, cid)
    for uid in (rnd.holders["1"], rnd.holders["2"], "cec.sofia"):
        helper = Assistant(users[uid])
        texts = [helper.handle(f"/qc {cid[-4:]}").text]
        texts += [r.text for r in helper.updates({})]
        for text in texts:
            assert "suggest" not in text.lower(), (uid, text)
            assert "ready to edit" not in text and "You decide" not in text, (uid, text)
    text = Assistant(users[SS]).handle(f"/qc {cid[-4:]}").text
    assert "ready to edit" not in text and "You decide" not in text
    assert "Quality check passed and narrative signed by" in text


def test_qc_after_a_query_says_the_query_was_sent(demo, monkeypatch):
    """B29: once the Safety Specialist sends a query, /qc reports it instead of saying the
    narrative draft is ready to edit and that the reader decides and signs."""
    import yaml

    from tests.test_flow3 import SS, pdf, to_qc_review

    monkeypatch.syspath_prepend(str(ROOT / "ui"))  # the UI imports its modules by name
    from assistant import Assistant

    monkeypatch.setenv("ADJUD_AI_MODE", "replay")
    runner = Runner(demo)
    runner.run_once()
    store = runner.pipeline.store
    cid = runner.pipeline.case_for("DEMO01|DEMO01-101-0001|AE-001")
    from adjud.documents import upload

    upload(store, cid, [("summary.pdf", pdf("DISCHARGE SUMMARY\nChest pain.")),
                        ("ecg.pdf", pdf("ECG REPORT\nST elevation."))], SITE101["id"])  # fmt: skip
    to_qc_review(runner, store, cid)
    runner.flow2.open_query(cid, ["missing_document"], "Please add the troponin results.",
                            user_id=SS, password="demo")  # fmt: skip
    users = {u["id"]: u for u in yaml.safe_load((demo.config / "users.yaml").read_text())["users"]}
    for uid in (SS, "dm.dana"):
        reply = Assistant(users[uid]).handle(f"/qc {cid[-4:]}")
        assert "ready to edit" not in reply.text and "You decide" not in reply.text, uid
        assert f"A query was sent to the site by {users[SS]['name']}" in reply.text, uid
        assert not reply.actions, uid
