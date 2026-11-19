"""One Slack channel per demo character (P-58 follow-up): study.yaml's
`slack_character_channels` maps a person to a channel named by role. Notices for them go
there instead of a DM; the Agent there acts as that person whoever writes; buttons and
links open the app as them; unmapped people and channels behave as before.

Fake Slack clients and the Agent on replay: nothing reaches Slack or the Claude API."""

from __future__ import annotations

import json

from slack_sdk.errors import SlackApiError

from adjud.delivery import directory, people
from adjud.notify import Outbox
from adjud.slack_app.agent import IN_CHANNEL
from adjud.slack_app.app import SlackHandlers
from adjud.slack_app.channels import ensure_channels
from adjud.slack_app.core import Directory, SlackActionsLog, Workspace
from adjud.slack_app.dm import SlackDmChannel, SlackSettings
from tests import test_slack_agent_tools as with_cases
from tests.test_slack_agent import ME, FakeSlack, answer, log_of, message, replay_agent
from tools.reset_demo import DEMO_AES, reset

data, ws = with_cases.data, with_cases.ws  # fixtures: the demo's three cases, imported
IDS = {"data-manager": "C0DM", "site-user": "C0SITE", "safety-specialist": "C0SS",
       "adjudicator-1": "C0ADJ1", "adjudicator-2": "C0ADJ2"}  # fmt: skip


class ChannelSlack(FakeSlack):
    """Answers a post with the channel it went to, as Slack does."""

    def chat_postMessage(self, **kwargs):  # noqa: N802 (slack_sdk's name)
        self.posts.append(kwargs)
        return {"ok": True, "channel": kwargs["channel"], "ts": f"1700000000.{len(self.posts):06d}"}


def with_channels(ws: Workspace) -> Directory:
    ws.people.save_channel_ids(IDS)
    return ws.people


def in_channel(text: str, channel: str, ts: str = "1700000100.000100", **kw) -> dict:
    return message(text, ts=ts, channel=channel, channel_type="channel", **kw)


# -- the configuration ---------------------------------------------------------------------------


def test_the_demo_names_one_channel_per_character_by_role(ws):
    assert ws.people.character_channels == {
        "dm.dana": "data-manager", "site101.coord": "site-user",
        "ss.anna": "safety-specialist", "adj.okafor": "adjudicator-1",
        "adj.lindqvist": "adjudicator-2"}  # fmt: skip
    assert ws.people.channel_of("site101.coord") is None  # the app has not made them yet
    with_channels(ws)
    assert ws.people.channel_of("site101.coord") == "C0SITE"
    assert ws.people.channel_of("site102.coord") is None
    assert ws.people.person_in_channel("C0SS")["id"] == "ss.anna"
    assert ws.people.person_in_channel("C0OTHER") is None


def test_the_channel_ids_survive_a_reset_as_acting_as_does(ws, data):
    with_channels(ws)
    ws.people.act_as(ME, "ss.anna")
    reset(data, only=DEMO_AES)
    fresh = Directory(data.config, data.root / "slack")
    assert fresh.channel_ids() == IDS and fresh.person_for(ME)["id"] == "ss.anna"


# -- making sure the channels exist when the Slack app starts ------------------------------------


class Channels:
    """conversations.list (two pages), .create and .join; `fail` names failing calls."""

    def __init__(self, existing: dict[str, str], fail: set[str] | None = None):
        self.existing, self.fail = dict(existing), fail or set()
        self.created: list[str] = []
        self.joined: list[str] = []

    def _check(self, call: str, name: str = "") -> None:
        if call in self.fail or f"{call}:{name}" in self.fail:
            raise SlackApiError("no", {"ok": False, "error": "missing_scope"})

    def conversations_list(self, **kw):
        self._check("list")
        chans = [{"name": n, "id": i} for n, i in sorted(self.existing.items())]
        if kw.get("cursor") != "page2":
            return {"channels": chans[:2], "response_metadata": {"next_cursor": "page2"}}
        return {"channels": chans[2:], "response_metadata": {"next_cursor": ""}}

    def conversations_create(self, *, name, is_private):
        self._check("create", name)
        assert is_private is False
        self.created.append(name)
        self.existing[name] = f"C0NEW{len(self.created)}"
        return {"channel": {"id": self.existing[name], "name": name}}

    def conversations_join(self, *, channel):
        self._check("join", channel)
        self.joined.append(channel)
        return {"ok": True}


def test_existing_channels_are_joined_and_missing_ones_created(ws):
    slack = Channels({"general": "C0GEN", "site-user": "C0SITE", "data-manager": "C0DM",
                      "random": "C0RND"})  # fmt: skip
    ids = ensure_channels(slack, ws.people)
    assert slack.created == ["adjudicator-1", "adjudicator-2", "safety-specialist"]
    assert ids == {"adjudicator-1": "C0NEW1", "adjudicator-2": "C0NEW2", "data-manager": "C0DM",
                   "safety-specialist": "C0NEW3", "site-user": "C0SITE"}  # fmt: skip
    assert sorted(slack.joined) == sorted(ids.values())
    assert ws.people.channel_ids() == ids


def test_a_channel_slack_refuses_is_left_out_and_its_person_gets_dms(ws, caplog):
    slack = Channels({"site-user": "C0SITE"}, fail={"create:safety-specialist", "join:C0SITE"})
    ids = ensure_channels(slack, ws.people)
    assert set(ids) == {"adjudicator-1", "adjudicator-2", "data-manager"}
    assert "missing_scope" in caplog.text
    ws.people.act_as(ME, "site101.coord")
    note = Outbox(ws.paths, "DEMO01").queue("DEMO01-0001", "site:101", "documents_requested")
    client = ChannelSlack()
    SlackDmChannel(ws.people, SlackSettings(bot_token="xoxb-test"), client).send(
        note, people("site:101", directory(ws.paths.config, "DEMO01")))  # fmt: skip
    assert [p["channel"] for p in client.posts] == [ME]


def test_without_the_list_scope_every_channel_is_still_tried(ws):
    ids = ensure_channels(Channels({}, fail={"list"}), ws.people)
    assert len(ids) == 5


def test_no_mapping_calls_nothing(ws, tmp_path):
    ws.people.character_channels = {}
    assert ensure_channels(object(), ws.people) == {}
    assert not ws.people.channels_path.exists()


# -- notices -------------------------------------------------------------------------------------


def test_notices_for_mapped_people_go_to_their_channel_and_the_rest_as_dms(ws):
    with_channels(ws)
    ws.people.act_as(ME, "cec.sofia")  # the presenter: unmapped people's DMs reach them
    out = Outbox(ws.paths, "DEMO01")
    site = out.queue("DEMO01-0001", "site:101", "documents_requested")
    adjs = out.queue("DEMO01-0001", "role:adjudicator", "committee_needed")
    cec = out.queue("DEMO01-0001", "role:cec_coordinator", "committee_needed")
    client = ChannelSlack()
    channel = SlackDmChannel(ws.people, SlackSettings(bot_token="xoxb-test"), client,
                             next_step=ws.next_step)  # fmt: skip
    users = directory(ws.paths.config, "DEMO01")
    for note in (site, adjs, cec):
        channel.send(note, people(note["to"], users))
    to = [(p["channel"], p["blocks"][0]["text"]["text"].split("For: ")[1].split("\n")[0])
          for p in client.posts]  # fmt: skip
    assert to == [
        ("C0SITE", "Priya Nair (Site user, Larkfield General Hospital)"),
        ("C0ADJ1", "Dr. Daniel Okafor (Adjudicator, committee Chair)"),
        ("C0ADJ2", "Dr. Elin Lindqvist (Adjudicator)"),
        (ME, "Dr. Marco Reyes (Adjudicator)"),  # unmapped: a DM as before
        (ME, "Sofia Marques (CEC Coordinator)"),
    ]
    assert "thread_ts" not in client.posts[0]  # top level: the start of a thread
    site_post = json.dumps(client.posts[0]["blocks"])
    assert "Next: Upload the documents" in site_post
    url = client.posts[0]["blocks"][1]["elements"][0]["url"]
    assert url == ("http://localhost:8501/documents?role=site&case=DEMO01-0001"
                   "&user=site101.coord")  # fmt: skip
    sent = [json.loads(ln) for ln in
            (ws.paths.root / "slack" / "dm_sent.jsonl").read_text().splitlines()]  # fmt: skip
    assert sent[0] == {"note": site["id"], "slack_user": "C0SITE", "case_id": "DEMO01-0001",
                       "channel": "C0SITE", "ts": "1700000000.000001"}  # fmt: skip
    channel.send(adjs, people(adjs["to"], users))  # a retry reaches nobody twice
    assert len(client.posts) == 5


def test_a_mapped_person_gets_their_channel_with_nobody_acting_as(ws):
    with_channels(ws)
    note = Outbox(ws.paths, "DEMO01").queue("DEMO01-0001", "role:safety_specialist",
                                            "qc_review_needed", page="qc")  # fmt: skip
    client = ChannelSlack()
    SlackDmChannel(ws.people, SlackSettings(bot_token="xoxb-test"), client).send(
        note, people(note["to"], directory(ws.paths.config, "DEMO01")))  # fmt: skip
    assert [p["channel"] for p in client.posts] == ["C0SS"]
    url = client.posts[0]["blocks"][1]["elements"][0]["url"]
    assert "role=safety_specialist" in url and "user=ss.anna" in url


# -- the Agent in a character channel ------------------------------------------------------------


def test_a_message_in_the_site_channel_is_answered_as_priya_once_in_its_thread(ws, tmp_path):
    with_channels(ws)
    ws.people.act_as(ME, "ss.anna")  # Acting As says someone else: the channel wins
    agent = replay_agent(ws, tmp_path)
    answer(agent, "site101.coord", "What are my tasks?", "You have *2 tasks*.",
           [{"tool": "my_tasks", "input": {}}])  # fmt: skip
    client = ChannelSlack()
    event = in_channel("What are my tasks?", "C0SITE")
    for e in (event, {**event, "subtype": "message_changed"},
              {**event, "bot_id": "B0APP", "user": None}):  # fmt: skip
        agent.on_message(e, client)
    (post,) = client.posts
    assert (post["channel"], post["thread_ts"], post["text"]) == (
        "C0SITE", "1700000100.000100", "You have *2 tasks*.")  # fmt: skip
    assert client.statuses == []  # no agent session in a channel
    url = post["blocks"][-2]["elements"][0]["url"]
    assert "role=site" in url and "user=site101.coord" in url
    (entry,) = log_of(ws.paths)
    assert (entry["acting_as"], entry["channel"], entry["slack_user"]) == (
        "site101.coord", "C0SITE", ME)  # fmt: skip
    assert ws.people.person_for(ME)["id"] == "ss.anna"  # nothing switched


def test_a_reply_in_a_channel_notices_thread_is_about_its_case(ws, tmp_path):
    with_channels(ws)
    note = Outbox(ws.paths, "DEMO01").queue("DEMO01-0001", "site:101", "documents_requested")
    client = ChannelSlack()
    SlackDmChannel(ws.people, SlackSettings(bot_token="xoxb-test"), client).send(
        note, people("site:101", directory(ws.paths.config, "DEMO01")))  # fmt: skip
    notice_ts = "1700000000.000001"
    agent = replay_agent(ws, tmp_path)
    answer(agent, "site101.coord", "Which documents?", "A discharge summary and an ECG report.",
           [{"tool": "required_documents", "input": {"case": "DEMO01-0001"}}],
           case_id="DEMO01-0001")  # fmt: skip
    thread = [{"bot_id": "B0APP", "text": client.texts[0], "ts": notice_ts}]
    agent.on_message(in_channel("Which documents?", "C0SITE", ts="1700000300.0",
                                thread_ts=notice_ts), ChannelSlack(thread))  # fmt: skip
    entry = log_of(ws.paths)[-1]
    assert (entry["case_id"], entry["outcome"], entry["acting_as"]) == (
        "DEMO01-0001", "posted", "site101.coord")  # fmt: skip


def test_priyas_channel_cannot_see_site_102s_cases(ws, tmp_path):
    with_channels(ws)
    ws.people.act_as(ME, "dm.dana")  # the data manager sees every case; the channel does not
    agent = replay_agent(ws, tmp_path)
    answer(agent, "site101.coord", "What about DEMO01-0002?",
           "I can't see DEMO01-0002: it is not one of your cases.",
           [{"tool": "case_status", "input": {"case": "DEMO01-0002"}, "error": True}])  # fmt: skip
    client = ChannelSlack()
    agent.on_message(in_channel("What about DEMO01-0002?", "C0SITE"), client)
    assert client.texts == ["I can't see DEMO01-0002: it is not one of your cases."]
    entry = log_of(ws.paths)[-1]
    assert entry["tools"] == [{"tool": "case_status", "error": True}]
    assert entry["case_id"] is None


def test_a_message_in_any_other_channel_is_ignored(ws, tmp_path):
    with_channels(ws)
    ws.people.act_as(ME, "site101.coord")
    client = ChannelSlack()
    replay_agent(ws, tmp_path).on_message(in_channel("hello", "C0GENERAL"), client)
    assert client.posts == [] and log_of(ws.paths) == []


def test_a_switch_typed_in_a_channel_says_whom_the_channel_is(ws, tmp_path):
    with_channels(ws)
    agent = replay_agent(ws, tmp_path)  # no recorded answers: an AI call would fail
    client = ChannelSlack()
    agent.on_message(in_channel("act as anna", "C0SITE"), client)
    assert client.texts == [IN_CHANNEL.format(name="Priya Nair")]
    assert "In this channel you are *Priya Nair*" in client.texts[0]
    assert log_of(ws.paths)[-1]["source"] == "code"


def test_with_the_ai_off_the_channel_gets_the_channel_persons_tasks(ws):
    with_channels(ws)
    agent = SlackHandlers(ws, SlackActionsLog(ws.paths)).agent
    client = ChannelSlack()
    agent.on_message(in_channel("What should I do?", "C0SITE"), client)
    assert "DEMO01-0001: Upload the documents" in client.texts[0]


# -- buttons clicked in a character channel ------------------------------------------------------


def test_import_clicked_in_the_data_manager_channel_imports_as_rahul(data):
    w = Workspace(data)
    with_channels(w)
    handlers = SlackHandlers(w, SlackActionsLog(data))
    client = FilesHome()
    body = {"user": {"id": ME}, "channel": {"id": "C0DM"}}  # nobody chose whom ME acts as
    handlers.on_import_button(lambda: None, body, client)
    assert sorted(w.store.list_cases()) == ["DEMO01-0001", "DEMO01-0002", "DEMO01-0003"]
    entry = SlackActionsLog(data).log.records()[-1]
    assert (entry["slack_user"], entry["acting_as"], entry["action"]) == (ME, "dm.dana", "import")
    handlers.on_open_app(
        lambda: None,
        {
            "user": {"id": ME},
            "container": {"channel_id": "C0SS"},
            "actions": [{"url": "http://x/qc?case=DEMO01-0001"}],
        },
    )
    entry = SlackActionsLog(data).log.records()[-1]
    assert (entry["acting_as"], entry["cases"]) == ("ss.anna", ["DEMO01-0001"])


class FilesHome:
    def __init__(self):
        self.views = []

    def views_publish(self, user_id, view):
        self.views.append(view)
