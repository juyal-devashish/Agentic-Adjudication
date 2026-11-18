"""The Slack Agent's conversation entry point in the gateway (plan P-58): its own mode,
the tool loop, call records with and without a case, the ledger, replay by input hash.
A scripted fake client stands in for the API: tests never call it."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from adjud.ai.config import AiConfig, Prices
from adjud.ai.functions import SLACK_AGENT
from adjud.ai.gateway import AgentContext, ChatMessage, Gateway, conversation_hash
from adjud.ai.tools import Tool, Toolbox, ToolError
from adjud.integrity import verify_all
from adjud.paths import DataPaths
from adjud.store import CaseBusy, FileCaseStore
from adjud.workflow import TransitionRefused
from tests.test_ai import FakeClient, case, reply, text, tool_use  # noqa: F401 (fixture)

PRIYA = AgentContext(person="site101.coord", name="Priya Nair", role="site", site="101")
ASK = [ChatMessage(role="user", text="What are my tasks?")]


def thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


class Tasks:
    """A toolbox with one read tool and one acting tool; counts what was done."""

    def __init__(self):
        self.imported = 0

    def toolbox(self) -> Toolbox:
        def my_tasks(_: dict) -> dict:
            return {"tasks": [{"case": "DEMO01-0001", "task": "Upload documents"}]}

        def import_files(_: dict) -> dict:
            self.imported += 1
            return {"imported": 1}

        def refuse(_: dict) -> dict:
            raise ToolError("not yours to see")

        schema = {"type": "object", "properties": {}, "required": [],
                  "additionalProperties": False}  # fmt: skip
        return Toolbox([
            Tool("my_tasks", "This person's tasks.", schema, my_tasks),
            Tool("import_files", "Import the waiting AE files.", schema, import_files),
            Tool("other_site_cases", "Refuses.", schema, refuse),
        ])  # fmt: skip


def agent_cfg(**kw) -> AiConfig:
    base = {
        "mode": "replay",  # the Quality Check agents stay on replay
        "modes": {"slack_agent": "live"},
        "functions": {"slack_agent": True},
        "price_per_mtok": Prices(input=0.10, output=0.50),
    }
    return AiConfig(**{**base, **kw})


@pytest.fixture
def store(tmp_path, clock) -> FileCaseStore:
    return FileCaseStore(DataPaths(tmp_path / "data"), clock=clock)


def gw(store, tmp_path, client=None, env=None, **kw) -> Gateway:
    return Gateway(store, agent_cfg(**kw), tmp_path / "replays", client=client, env=env or {})


def test_off_answers_without_ai_and_records_nothing(store, tmp_path):
    for cfg in ({"modes": {"slack_agent": "off"}}, {"modes": {}, "mode": "live"},
                {"functions": {"slack_agent": False}}):  # fmt: skip
        r = gw(store, tmp_path, client=FakeClient(), **cfg).converse(PRIYA, ASK, Tasks().toolbox())
        assert (r.mode, r.status, r.text) == ("off", "unavailable", "")
    assert not store.paths.ai.exists()


def test_live_without_a_key_answers_without_ai(store, tmp_path):
    r = gw(store, tmp_path).converse(PRIYA, ASK, Tasks().toolbox())
    assert r.status == "unavailable" and "no API key" in r.note


def test_live_tool_loop_reply_record_and_ledger_without_a_case(store, tmp_path):
    tasks = Tasks()
    client = FakeClient(
        reply(thinking(), tool_use("my_tasks", {}), stop="tool_use"),
        reply(thinking(), text("You have *1 task*:"), text("• `DEMO01-0001`: upload documents")),
    )
    r = gw(store, tmp_path, client=client).converse(PRIYA, ASK, tasks.toolbox())
    assert r.ok and r.mode == "live" and not r.simulated
    assert r.text == "You have *1 task*:\n\n• `DEMO01-0001`: upload documents"
    assert [c["tool"] for c in r.tool_calls] == ["my_tasks"] and not r.tool_calls[0]["error"]
    assert r.cost_usd == pytest.approx(2 * (1000 * 0.10 + 200 * 0.50) / 1e6)

    first, second = client.requests
    assert first["system"][1]["text"].count("Priya Nair") == 1
    assert "PHI" in first["system"][0]["text"] and "password" in first["system"][0]["text"]
    assert first["output_config"] == {"effort": "medium"}  # text, no JSON schema
    assert first["messages"] == [{"role": "user", "content": "What are my tasks?"}]
    assert {t["name"] for t in first["tools"]} == {"my_tasks", "import_files", "other_site_cases"}
    # the assistant turn goes back whole, thinking block included, then the tool result
    assert [b["type"] for b in second["messages"][1]["content"]] == ["thinking", "tool_use"]
    assert second["messages"][2]["content"][0]["type"] == "tool_result"

    assert r.call["path"].startswith("ai/calls/") and r.call["path"].endswith(".json")
    saved = json.loads((store.paths.root / r.call["path"]).read_text())
    assert saved["function"] == "slack_agent" and saved["prompt"] == "slack_agent_v1"
    assert saved["output"] == {"text": r.text} and saved["requests"] == 2
    assert saved["input"]["context"]["person"] == "site101.coord"
    (ledger,) = Gateway(store, agent_cfg(), tmp_path).ledger.records()
    assert ledger["case_id"] is None and ledger["call"] == r.call
    assert all(rep.ok for rep in verify_all(store.paths))

    (store.paths.root / r.call["path"]).write_text("{}")
    assert any("referenced file changed" in p for rep in verify_all(store.paths)
               for p in rep.problems)  # fmt: skip


def test_a_turn_about_a_case_is_saved_with_the_case(case, tmp_path):  # noqa: F811
    store, cid = case
    client = FakeClient(reply(text("Your case is waiting for documents.")))
    ctx = PRIYA.model_copy(update={"case_id": cid})
    r = gw(store, tmp_path, client=client).converse(ctx, ASK, Tasks().toolbox())
    assert r.ok and r.call["path"].startswith("ai/") and "calls" not in r.call["path"]
    called = [e for e in store.events(cid) if e["type"] == "ai.called"]
    assert called[-1]["data"]["function"] == "slack_agent"
    assert called[-1]["data"]["call"] == r.call
    assert "## This conversation" in client.requests[0]["system"][1]["text"]
    assert cid in client.requests[0]["system"][1]["text"]
    assert all(rep.ok for rep in verify_all(store.paths))


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_a_turn_about_a_case_that_is_gone_makes_no_call_and_no_folder(store, tmp_path, mode):
    """A Slack thread can outlive its case (a demo reset): the turn makes no request, and
    no orphan folder under cases/ shifts the next case id (the P-57 demo uses -0001)."""
    client = FakeClient(reply(text("hi")))
    g = gw(store, tmp_path, client=client, modes={"slack_agent": mode})
    for cid in ("DEMO01-0001", "not-a-case-id"):
        r = g.converse(PRIYA.model_copy(update={"case_id": cid}), ASK, Tasks().toolbox())
        assert r.status == "unavailable" and "no longer exists" in r.note and r.call is None
    assert client.requests == [] and not g.missing
    assert not store.paths.cases.exists() or not any(store.paths.cases.iterdir())
    assert store.new_case_id("DEMO01") == "DEMO01-0001"


def test_a_case_removed_during_the_turn_is_recorded_without_the_case(store, tmp_path):
    """Removed between the check and the record: saved as a turn about no case, so the
    cost still reaches the ledger and no case folder is created."""
    client = FakeClient(reply(text("hi")))
    g = gw(store, tmp_path, client=client)
    call = g._record("DEMO01-0001", {"function": "slack_agent", "prompt": "slack_agent_v1",
                                     "mode": "live", "simulated": False, "model": "m",
                                     "valid": True, "usage": {}, "cost_usd": 0.01})  # fmt: skip
    assert call["path"].startswith("ai/calls/")
    (line,) = g.ledger.records()
    assert line["case_id"] is None and line["cost_usd"] == 0.01
    assert not store.paths.cases.exists() or not any(store.paths.cases.iterdir())


def test_tool_errors_go_back_to_the_agent(store, tmp_path):
    client = FakeClient(
        reply(tool_use("other_site_cases", {}), stop="tool_use"),
        reply(text("That is not yours to see.")),
    )
    r = gw(store, tmp_path, client=client).converse(PRIYA, ASK, Tasks().toolbox())
    assert r.ok and r.tool_calls[0]["error"]
    result = client.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] and "not yours to see" in result["content"]


@pytest.mark.parametrize(
    "exc",
    [CaseBusy("DEMO01-0001"), TransitionRefused("upload.added", "CLOSED"),
     OSError("/data/cases/DEMO01-0001/documents/original/Smith_John.pdf")],
)  # fmt: skip
def test_a_store_error_in_a_tool_is_an_error_for_the_agent(store, tmp_path, exc):
    """Store errors from an acting tool go back to the model as is_error, naming only the
    error type (an OSError's text can carry a file name); the call is still saved and
    costed."""

    def boom(_: dict) -> dict:
        raise exc

    schema = {"type": "object", "properties": {}, "required": [],
              "additionalProperties": False}  # fmt: skip
    box = Toolbox([Tool("import_files", "Import.", schema, boom)])
    client = FakeClient(
        reply(tool_use("import_files", {}), stop="tool_use"),
        reply(text("The import could not be done.")),
    )
    r = gw(store, tmp_path, client=client).converse(PRIYA, ASK, box)
    assert r.ok and r.tool_calls[0]["error"] and len(client.requests) == 2
    result = client.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] and type(exc).__name__ in result["content"]
    assert "Smith" not in result["content"] and "original" not in result["content"]
    assert r.call and len(Gateway(store, agent_cfg(), tmp_path).ledger.records()) == 1


def test_refusal_and_cut_off_answers_are_errors_with_no_text(store, tmp_path):
    for stop, words in (("refusal", "declined"), ("max_tokens", "cut off")):
        client = FakeClient(reply(text("partial"), stop=stop))
        r = gw(store, tmp_path, client=client).converse(PRIYA, ASK, Tasks().toolbox())
        assert (r.status, r.text) == ("error", "") and words in r.note and r.call


def test_too_many_tool_turns_stops(store, tmp_path):
    client = FakeClient(*[reply(tool_use("my_tasks", {}), stop="tool_use")] * 3)
    r = gw(store, tmp_path, client=client, max_tool_turns=2).converse(PRIYA, ASK, Tasks().toolbox())
    assert r.status == "error" and "too many" in r.note and len(client.requests) == 3


def test_budget_cap_stops_the_agent(store, tmp_path):
    client = FakeClient(reply(text("Hello."), tokens=(10_000_000, 0)))
    g = gw(store, tmp_path, client=client, budget_usd=0.5)
    assert g.converse(PRIYA, ASK, Tasks().toolbox()).ok
    r = g.converse(PRIYA, ASK, Tasks().toolbox())
    assert r.status == "unavailable" and "budget" in r.note


def test_input_hash_covers_person_role_case_messages_and_tools():
    tools = Tasks().toolbox().definitions()
    h = conversation_hash(SLACK_AGENT, "m", PRIYA, ASK, tools)
    assert h == conversation_hash(SLACK_AGENT, "m", PRIYA.model_copy(), list(ASK), tools)
    others = [
        conversation_hash(SLACK_AGENT, "m", PRIYA.model_copy(update={"role": "safety_specialist"}),
                          ASK, tools),
        conversation_hash(SLACK_AGENT, "m", PRIYA.model_copy(update={"person": "x"}), ASK, tools),
        conversation_hash(SLACK_AGENT, "m", PRIYA.model_copy(update={"case_id": "DEMO01-0001"}),
                          ASK, tools),
        conversation_hash(SLACK_AGENT, "m", PRIYA, [*ASK, ChatMessage(role="user", text="?")],
                          tools),
        conversation_hash(SLACK_AGENT, "m", PRIYA, ASK, tools[:1]),
        conversation_hash(SLACK_AGENT, "other-model", PRIYA, ASK, tools),
    ]  # fmt: skip
    assert len({h, *others}) == 7


def test_replay_runs_the_recorded_tools_and_returns_the_recorded_text(store, tmp_path):
    tasks = Tasks()
    g = gw(store, tmp_path, modes={"slack_agent": "replay"})
    r = g.converse(PRIYA, ASK, tasks.toolbox())
    assert (r.mode, r.status) == ("replay", "unavailable") and "no recorded answer" in r.note
    ((fn, h, payload),) = g.missing
    assert fn == "slack_agent" and payload["context"]["role"] == "site"
    assert h == conversation_hash(SLACK_AGENT, g.cfg.model, PRIYA, ASK,
                                  tasks.toolbox().definitions())  # fmt: skip

    path = tmp_path / "replays" / "slack_agent" / f"{h}.json"
    path.parent.mkdir(parents=True)
    # recorded with the refusals it met: they are refused again, so the text still holds
    path.write_text(json.dumps({
        "simulated": True,
        "text": "Imported 1 file. The other site's cases are not yours to see.",
        "tool_calls": [{"tool": "import_files", "input": {}, "error": False},
                       {"tool": "other_site_cases", "input": {}, "error": True},
                       {"tool": "not_in_this_toolbox", "input": {}, "error": True}],
    }))  # fmt: skip
    r = g.converse(PRIYA, ASK, tasks.toolbox())
    assert r.ok and r.simulated and r.label == "Simulated AI" and r.cost_usd == 0
    assert r.text.startswith("Imported 1 file.") and tasks.imported == 1  # the action happened
    # a tool the person does not have, or one that refuses, stays refused in replay too
    assert [c["error"] for c in r.tool_calls] == [False, True, True]
    saved = json.loads((store.paths.root / r.call["path"]).read_text())
    assert saved["mode"] == "replay" and saved["simulated"] is True
    assert all(rep.ok for rep in verify_all(store.paths))


def test_replay_never_posts_a_recorded_text_whose_tools_turned_out_differently(store, tmp_path):
    """Recorded as a success, refused this time: the recorded "Imported" would be untrue.
    The turn is an error with no text, later recorded tools do not run, and tool_calls
    says what did run, so the caller answers in its own words."""
    tasks = Tasks()
    g = gw(store, tmp_path, modes={"slack_agent": "replay"})
    h = conversation_hash(SLACK_AGENT, g.cfg.model, PRIYA, ASK, tasks.toolbox().definitions())
    path = tmp_path / "replays" / "slack_agent" / f"{h}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "simulated": True,
        "text": "Done: imported 1 file and listed the other site's cases.",
        "tool_calls": [{"tool": "import_files", "input": {}},  # no flag: recorded as success
                       {"tool": "other_site_cases", "input": {}},
                       {"tool": "import_files", "input": {}}],
    }))  # fmt: skip
    r = g.converse(PRIYA, ASK, tasks.toolbox())
    assert (r.status, r.text) == ("error", "") and "other_site_cases" in r.note
    assert [(c["tool"], c["error"]) for c in r.tool_calls] == [
        ("import_files", False), ("other_site_cases", True)]  # fmt: skip
    assert tasks.imported == 1  # the third recorded call never ran
    saved = json.loads((store.paths.root / r.call["path"]).read_text())
    assert saved["valid"] is False and saved["output"] is None


def test_a_turn_must_end_with_the_persons_message(store, tmp_path):
    g = gw(store, tmp_path, client=FakeClient())
    with pytest.raises(ValueError):
        g.converse(PRIYA, [*ASK, ChatMessage(role="assistant", text="Hi")], Tasks().toolbox())
    with pytest.raises(ValueError):
        g.converse(PRIYA, [], Tasks().toolbox())


def test_run_refuses_the_conversation_function(case):  # noqa: F811
    store, cid = case
    g = Gateway(store, agent_cfg(), store.paths.root)
    with pytest.raises(ValueError):
        g.run(cid, SLACK_AGENT, {}, Toolbox())
