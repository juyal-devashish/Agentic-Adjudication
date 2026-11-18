"""The Slack Agent in the Messages tab (plan P-58), read-only: who it acts as, one reply
per message, the session status, the PHI gate on every post (Q17), what a site asking
about another site's case gets, the conversation log (Q18), notices as Conversations
(Q12), the greeting, and the answer from code when the Agent's AI is off.

A fake Slack client stands in for slack_sdk and the Agent runs on replay, with answers
written in the test's own folder (labelled simulated): nothing here reaches Slack or the
Claude API."""

from __future__ import annotations

import json
from pathlib import Path

from adjud.ai.config import AiConfig
from adjud.ai.functions import SLACK_AGENT
from adjud.ai.gateway import AgentContext, ChatMessage, Gateway, conversation_hash
from adjud.integrity import verify_all
from adjud.paths import DataPaths
from adjud.slack_app.agent import FAILED, NO_AI, NO_PERSON, SlackAgent
from adjud.slack_app.agent_tools import agent_toolbox
from adjud.slack_app.app import SlackHandlers
from adjud.slack_app.conversations import ConversationLog
from adjud.slack_app.core import Directory, SlackActionsLog, Workspace
from adjud.slack_app.dm import SlackDmChannel, SlackSettings
from adjud.slack_app.phi_gate import HOLD_TEXT, PhiGate
from tests import test_slack_agent_tools as with_cases
from tools.reset_demo import DEMO_AES, reset

ME, DM = "U0PRESENTER", "D0PRESENTER"
data, ws = with_cases.data, with_cases.ws  # fixtures: the demo's three cases, imported


class FakeSlack:
    """slack_sdk's WebClient as the Agent uses it: posts, session status, thread reads."""

    def __init__(self, thread: list[dict] | None = None, status_fails: bool = False):
        self.posts: list[dict] = []
        self.statuses: list[str] = []
        self.thread = thread or []
        self.status_fails = status_fails

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        return {"ok": True, "channel": DM, "ts": f"1700000000.{len(self.posts):06d}"}

    def agents_sessions_setStatus(self, *, channel_id, status, thread_ts=None):  # noqa: N802
        self.statuses.append(status)
        if self.status_fails:
            raise TimeoutError("slack is slow")

    def conversations_replies(self, *, channel, ts, limit):
        return {"messages": self.thread}

    @property
    def texts(self) -> list[str]:
        return [p["text"] for p in self.posts]


def replay_agent(ws: Workspace, tmp_path: Path) -> SlackAgent:
    cfg = AiConfig(mode="off", modes={"slack_agent": "replay"}, functions={"slack_agent": True})
    gw = Gateway(ws.store, cfg, tmp_path / "replays", env={})
    return SlackAgent(ws, ConversationLog(ws.paths), gateway=gw)


def answer(agent: SlackAgent, person_id: str, said: list[str] | str, text: str,
           tool_calls: list[dict] | None = None, case_id: str | None = None) -> None:  # fmt: skip
    """Record the Agent's answer to this exact turn (simulated), keyed as replay finds it:
    by the person, the case, the conversation and that person's tool definitions."""
    person = agent.ws.people.by_id[person_id]
    ctx = AgentContext(person=person["id"], name=person["name"], role=person["role"],
                       site=person.get("site"), case_id=case_id)  # fmt: skip
    said = [said] if isinstance(said, str) else said
    msgs = [ChatMessage(role="user" if i % 2 == 0 else "assistant", text=t)
            for i, t in enumerate(said)]  # fmt: skip
    tools = agent_toolbox(agent.ws, person).definitions()
    gw = agent.gateway
    assert gw is not None
    h = conversation_hash(SLACK_AGENT, gw.cfg.model, ctx, msgs, tools)
    path = gw.replay_dir / "slack_agent" / f"{h}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"simulated": True, "text": text, "tool_calls": tool_calls or []}))


def message(text: str, ts: str = "1700000100.000100", **kw) -> dict:
    return {"type": "message", "channel_type": "im", "channel": DM, "user": ME, "text": text,
            "ts": ts, **kw}  # fmt: skip


def log_of(data: DataPaths) -> list[dict]:
    return ConversationLog(data).log.records()


# -- who the Agent acts as, and one reply per message ------------------------------------------


def test_each_persons_message_gets_exactly_one_reply_in_its_thread(ws, tmp_path):
    """Slack also sends edits (message_changed), deletions, the app's own messages and
    other subtypes: only the person's own message is answered, once, in its thread."""
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    answer(agent, "site101.coord", "What are my tasks?", "You have *2 tasks*.",
           [{"tool": "my_tasks", "input": {}}])  # fmt: skip
    client = FakeSlack()
    ignored = [
        message("What are my tasks?", subtype="message_changed"),
        message("What are my tasks?", subtype="message_deleted"),
        message("What are my tasks?", subtype="thread_broadcast"),
        message("You have 2 tasks.", bot_id="B0APP"),
        message("What are my tasks?", channel_type="channel"),
        {**message("What are my tasks?"), "user": None},
    ]
    for event in [*ignored, message("What are my tasks?")]:
        agent.on_message(event, client)
    (post,) = client.posts
    assert post["thread_ts"] == "1700000100.000100" and post["channel"] == DM
    assert post["text"] == "You have *2 tasks*."
    assert client.statuses == ["processing", "active"]
    assert "Simulated AI" in json.dumps(post["blocks"])
    (entry,) = log_of(ws.paths)
    assert (entry["acting_as"], entry["source"], entry["outcome"]) == ("site101.coord", "ai",
                                                                        "posted")  # fmt: skip
    assert entry["tools"] == [{"tool": "my_tasks", "error": False}] and entry["simulated"]


def test_a_reply_in_a_thread_goes_on_in_that_thread_with_what_was_said(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    first = "1700000100.000100"
    thread = [
        {"user": ME, "text": "What are my tasks?", "ts": first},
        {"bot_id": "B0APP", "text": "You have *2 tasks*.", "ts": "1700000101.000100"},
        {"user": ME, "text": "edited", "subtype": "message_changed", "ts": "1700000101.5"},
        {"user": ME, "text": "And 0003?", "ts": "1700000102.000100"},
    ]
    answer(agent, "site101.coord", ["What are my tasks?", "You have *2 tasks*.", "And 0003?"],
           "DEMO01-0003 waits for documents.")  # fmt: skip
    client = FakeSlack(thread)
    agent.on_message(message("And 0003?", ts="1700000102.000100", thread_ts=first), client)
    assert client.texts == ["DEMO01-0003 waits for documents."]
    assert client.posts[0]["thread_ts"] == first


def test_without_a_person_the_agent_asks_whom_you_act_as(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    client = FakeSlack()
    agent.on_message(message("hello"), client)
    assert client.texts == [NO_PERSON] and client.statuses == ["processing", "active"]
    assert log_of(ws.paths)[-1]["outcome"] == "refused"


def test_with_the_agents_ai_off_code_answers_with_the_persons_tasks(ws):
    """The Agent's mode is off by default (study.yaml): the demo never goes silent."""
    agent = SlackHandlers(ws, SlackActionsLog(ws.paths)).agent
    ws.people.act_as(ME, "site101.coord")
    client = FakeSlack()
    agent.on_message(message("What should I do?"), client)
    (post,) = client.posts
    assert post["text"].startswith(NO_AI)
    assert "DEMO01-0001: Upload the documents" in post["text"]
    shown = json.dumps(post["blocks"])  # each Task once, as its own row with its button
    assert shown.count("Upload the documents") == 2  # DEMO01-0001 and -0003, not twice each
    assert post["blocks"][0]["text"]["text"] == NO_AI
    urls = [e["url"] for b in post["blocks"] for e in
            (b.get("elements") or [b.get("accessory") or {}]) if e.get("url")]  # fmt: skip
    assert "http://localhost:8501/documents?role=site&user=site101.coord&case=DEMO01-0001" in urls
    assert "http://localhost:8501/documents?role=site&user=site101.coord" in urls
    entry = log_of(ws.paths)[-1]
    assert (entry["source"], entry["mode"], entry["outcome"]) == ("code", "off", "posted")
    ws.people.act_as(ME, "dm.dana")  # the data manager's import button works in the DM too
    agent.on_message(message("anything?", ts="1700000200.0"), client)
    assert "import_queued" not in json.dumps(client.posts[-1])  # imported already
    assert "nothing is waiting" in client.posts[-1]["text"]


def test_a_message_with_nothing_in_it_is_answered_with_the_tasks(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    client = FakeSlack()
    agent.on_message(message("   "), client)
    assert client.texts[0].startswith(NO_AI) and client.statuses == ["processing", "active"]
    assert log_of(ws.paths)[-1]["note"] == "an empty message"


def test_a_demo_folder_without_the_kit_takes_the_repositorys_seeded_identities(ws):
    (ws.paths.root / "flow1-testkit").unlink()  # data/demo has no kit of its own
    gate = PhiGate.for_workspace(ws)
    assert "DEMO01-101-0001" in gate.identities
    assert gate.check("Philip Roberts was admitted.").held


def test_without_seeded_identities_the_gate_still_runs_its_detectors(ws, caplog, monkeypatch,
                                                                     tmp_path):  # fmt: skip
    """No kit anywhere: a warning, and the detectors alone. Measured here, not gated:
    how many of the kit's must-redact values they hold."""
    from adjud.slack_app import phi_gate
    from tests.test_slack_agent_tools import seeded_phi

    (ws.paths.root / "flow1-testkit").unlink()
    monkeypatch.setattr(phi_gate, "REPO_DATA", tmp_path / "no-kit")
    gate = PhiGate.for_workspace(ws)
    assert gate.identities == {} and "detectors only" in caplog.text
    phi = seeded_phi()
    held = sum(gate.check(f"The letter on page 1 says {v}, as recorded.").held for _, v in phi)
    print(f"detectors only: {held} of {len(phi)} seeded must-redact values held")
    assert gate.check("Patient: ROBERTS, Philip. DOB: 24/07/1955").held


def test_the_status_goes_back_to_active_when_the_agent_fails(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "ss.anna")

    def boom(*a, **k):
        raise RuntimeError("the gateway broke")

    agent.gateway.converse = boom  # type: ignore[union-attr, method-assign]
    client = FakeSlack(status_fails=True)  # and setting the status fails too
    agent.on_message(message("status of 0001?"), client)
    assert client.statuses == ["processing", "active"]
    assert client.texts == [FAILED]
    entry = log_of(ws.paths)[-1]
    assert (entry["outcome"], entry["note"]) == ("failed", "RuntimeError")


def test_a_recorded_answer_whose_tools_turned_out_differently_is_answered_by_code(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    answer(
        agent,
        "site101.coord",
        "Status of 0002?",
        "DEMO01-0002 is waiting for documents.",
        [{"tool": "case_status", "input": {"case": "DEMO01-0002"}}],
    )  # recorded as allowed
    client = FakeSlack()
    agent.on_message(message("Status of 0002?"), client)
    assert client.texts[0].startswith(NO_AI)  # never the untrue recorded text
    assert log_of(ws.paths)[-1]["tools"] == [{"tool": "case_status", "error": True}]


def test_a_site_asking_about_another_sites_case_through_the_agent_is_refused(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    answer(agent, "site101.coord", "What about DEMO01-0002?",
           "I can't see DEMO01-0002: it is not one of your cases.",
           [{"tool": "case_status", "input": {"case": "DEMO01-0002"}, "error": True}])  # fmt: skip
    client = FakeSlack()
    agent.on_message(message("What about DEMO01-0002?"), client)
    assert client.texts == ["I can't see DEMO01-0002: it is not one of your cases."]
    entry = log_of(ws.paths)[-1]
    assert entry["tools"] == [{"tool": "case_status", "error": True}]
    assert entry["case_id"] is None  # the refused case is not this Conversation's


def test_a_reply_with_identifiers_is_held_and_logged_without_them(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "site101.coord")
    answer(agent, "site101.coord", "Who is the patient in 0001?",
           "The discharge summary is about Philip Roberts, born 24/07/1955.",
           [{"tool": "redaction_results", "input": {"case": "0001"}}])  # fmt: skip
    client = FakeSlack()
    agent.on_message(message("Who is the patient in 0001?"), client)
    (post,) = client.posts
    assert post["text"] == HOLD_TEXT
    sent = json.dumps(post)
    assert "Roberts" not in sent and "1955" not in sent
    assert "case=DEMO01-0001" in sent and "Open in the app" in sent
    entry = log_of(ws.paths)[-1]
    assert entry["outcome"] == "held" and entry["case_id"] == "DEMO01-0001"
    assert entry["held"]["found"] and "reply" not in entry
    logged = (ws.paths.root / "slack" / "conversations.jsonl").read_text()
    assert "Roberts" not in logged and "1955" not in logged
    assert "Who is the patient" not in logged  # what the person typed is never logged


# -- the conversation log (Q18) -----------------------------------------------------------------


def test_the_conversation_log_is_verified_and_cleared_by_a_reset(ws, data, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "ss.anna")
    agent.on_message(message("hello"), FakeSlack())  # no recording: answered by code
    path = data.root / "slack" / "conversations.jsonl"
    assert any(r.log == path and r.ok for r in verify_all(data))
    path.write_text(path.read_text().replace("ss.anna", "dm.dana"))
    assert any(r.log == path and not r.ok for r in verify_all(data))
    reset(data, preload=True, only=DEMO_AES)
    assert not path.exists() and not path.with_name(".conversations.jsonl.lock").exists()


# -- notices as Conversations (Q12) and the greeting ----------------------------------------------


def test_a_reply_to_a_notice_is_about_its_case(ws, tmp_path):
    from adjud.delivery import directory, people
    from adjud.notify import Outbox

    ws.people.act_as(ME, "site101.coord")
    note = Outbox(ws.paths, "DEMO01").queue("DEMO01-0001", "site:101", "documents_requested")
    client = FakeSlack()
    channel = SlackDmChannel(Directory(ws.paths.config, ws.paths.root / "slack"),
                             SlackSettings(bot_token="xoxb-test"), client)  # fmt: skip
    channel.send(note, people("site:101", directory(ws.paths.config, "DEMO01")))
    sent = [json.loads(ln) for ln in
            (ws.paths.root / "slack" / "dm_sent.jsonl").read_text().splitlines()]  # fmt: skip
    assert sent == [{"note": note["id"], "slack_user": ME, "case_id": "DEMO01-0001",
                     "channel": DM, "ts": "1700000000.000001"}]  # fmt: skip
    assert channel.reached(note["id"]) == {ME}

    agent = replay_agent(ws, tmp_path)
    notice_ts = "1700000000.000001"
    answer(agent, "site101.coord", "Which documents?", "A discharge summary and an ECG report.",
           [{"tool": "required_documents", "input": {"case": "DEMO01-0001"}}],
           case_id="DEMO01-0001")  # fmt: skip
    thread = [{"bot_id": "B0APP", "text": client.texts[0], "ts": notice_ts}]
    reply = message("Which documents?", ts="1700000300.0", thread_ts=notice_ts)
    agent.on_message(reply, FakeSlack(thread))
    entry = log_of(ws.paths)[-1]
    assert (entry["case_id"], entry["outcome"]) == ("DEMO01-0001", "posted")
    assert entry["ai_call"]["case_id"] == "DEMO01-0001"  # saved with the case
    called = [e for e in ws.store.events("DEMO01-0001") if e["type"] == "ai.called"]
    assert called[-1]["data"]["function"] == "slack_agent"

    # Acting As changed since the notice: Tom does not inherit Priya's case
    ws.people.act_as(ME, "site102.coord")
    agent.on_message({**reply, "ts": "1700000301.0"}, FakeSlack(thread))
    assert log_of(ws.paths)[-1]["case_id"] is None


def test_the_messages_tab_greets_once_per_person(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    client = FakeSlack()
    opened = {"type": "app_home_opened", "user": ME, "tab": "messages", "channel": DM}
    agent.on_messages_tab(opened, client)
    assert client.texts == [NO_PERSON]
    ws.people.act_as(ME, "dm.dana")
    agent.on_messages_tab(opened, client)
    agent.on_messages_tab(opened, client)  # opened again: no second greeting
    assert len(client.posts) == 2 and "*Rahul Mehta (Data manager)*" in client.texts[1]
    assert "thread_ts" not in client.posts[1]
    assert [e["kind"] for e in log_of(ws.paths)] == ["greeting", "greeting"]


class DownSlack(FakeSlack):
    """Slack refuses every post."""

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        raise RuntimeError("slack is down")


def test_a_post_slack_refused_is_logged_failed_and_a_greeting_is_tried_again(ws, tmp_path):
    agent = replay_agent(ws, tmp_path)
    ws.people.act_as(ME, "dm.dana")
    client = DownSlack()
    agent.on_message(message("What are my tasks?"), client)
    entry = log_of(ws.paths)[-1]
    assert entry["outcome"] == "failed" and "reply" not in entry
    assert "post failed: RuntimeError" in entry["note"]
    opened = {"type": "app_home_opened", "user": ME, "tab": "messages", "channel": DM}
    agent.on_messages_tab(opened, client)
    agent.on_messages_tab(opened, client)  # not greeted yet: tried again
    assert [e["outcome"] for e in log_of(ws.paths)[-2:]] == ["failed", "failed"]
    agent.on_messages_tab(opened, FakeSlack())
    agent.on_messages_tab(opened, FakeSlack())  # greeted now: once
    assert [e["outcome"] for e in log_of(ws.paths)[-3:]] == ["failed", "failed", "posted"]


def test_switching_person_in_the_conversation_points_to_switch_person(ws, tmp_path):
    """Live run: "/adj act-as priya" typed in the Agent's thread reached the Agent as a
    message (commands do not run there), and the AI only refused. Code answers it: whom
    you act as, and how to switch (Home's Switch person, or /adj in a channel); no AI call,
    nothing switched."""
    agent = replay_agent(ws, tmp_path)  # no recorded answers: an AI call would fail
    ws.people.act_as(ME, "dm.dana")
    for text in ("/adj act-as priya", "act as priya", "Act-as anna please", "/adj tasks"):
        client = FakeSlack()
        agent.on_message(message(text), client)
        (said,) = client.texts
        assert "Rahul Mehta" in said and "Switch person" in said and "/adj" in said, text
        assert client.statuses == ["processing", "active"]
    assert ws.people.person_for(ME)["id"] == "dm.dana"
    assert {e["source"] for e in log_of(ws.paths)} == {"code"}
