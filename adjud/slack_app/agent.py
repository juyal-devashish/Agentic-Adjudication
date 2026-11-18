"""The Slack Agent's handlers (plan P-58): the Messages tab with Slack's agent experience.

A person writes to the app (`message.im`); the Agent answers in that message's thread,
as that person (Q1: their own `slack_user_id`, else whom they act as in the demo), with
that person's tools only (`agent_tools`, Q5). While it works the session status is
"processing"; it is set back to "active" after the reply, also when anything failed,
or Slack would show it working for up to an hour.

Only a person's own messages are answered: a bot's (the app's own replies and notices)
and every subtype other than a plain message or a file share (edits, deletions, thread
broadcasts) are ignored, so each message gets exactly one reply.

Every text posted goes through the PHI gate first (Q17, `phi_gate`), the code-written
ones included; a held reply is replaced by "That answer may contain identifiers; open it
in the app." Every reply carries *Open in the app* for the page and case it concerns:
signed steps are done there (Q4). When the Agent's AI does not answer (its mode is off,
no key, the budget, no recording, an error) the reply is written by code: the person's
Tasks, with their buttons, so the demo never goes silent. Every turn and greeting is a
record in the conversation log (Q18).

A reply in the thread of a DM notice is about that notice's case (Q12), if the person
can still see it (Acting As may have changed since).

The Agent also does a person's unsigned steps (Q4, `agent_tools`): the data manager's
import and pull, a site's upload of the files attached to its message (`files.py`), the
Safety Specialist's narrative and Site Query drafts. Files arrive with the message
(subtype `file_share`); the `file_shared` event that Slack sends as well is only
acknowledged, so a file is never added twice. Without the Agent's AI, attached files are
not added and the reply says so, with the Documents page one click away.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from adjud.ai.gateway import AgentContext, AgentReply, ChatMessage, Gateway
from adjud.slack_app import blocks
from adjud.slack_app.agent_tools import Turn, agent_toolbox
from adjud.slack_app.conversations import ConversationLog
from adjud.slack_app.core import SlackActionsLog, Workspace
from adjud.slack_app.dm import notice_case
from adjud.slack_app.files import Opener, SlackFiles, https_get
from adjud.slack_app.phi_gate import HOLD_TEXT, PhiGate, Verdict

log = logging.getLogger("adjud.slack_app")
ANSWERED_SUBTYPES = (None, "file_share")  # everything else (edits, deletions...) is ignored
LANDING = {  # each role's own work page in the app (P-56)
    "data_manager": "import",
    "site": "documents",
    "safety_specialist": "qc",
    "cec_coordinator": "assignments",
    "adjudicator": "adjudicate",
}
NO_PERSON = "Choose whom you act as first: `/adj act-as` (demo)."
NO_AI = "I can't answer questions right now. Here is what is waiting for you:"
NOTHING = "I can't answer questions right now, and nothing is waiting for you."
FAILED = "Something went wrong with that answer. Your tasks are in the app."
NO_FILES = ("I can't take files right now, so nothing was added. Upload them on the "
            "Documents page in the app.")  # fmt: skip
MAX_HISTORY = 20  # earlier messages of the thread sent with each turn
SECTION_CHARS = 2900  # Slack's limit for one section's text is 3000
CASE_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d{4}\b")
SUBJECT_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d{3}-\d{4}\b")


def answers(event: dict[str, Any]) -> bool:
    """A person's own message in a DM with the app: the only kind the Agent answers."""
    return (
        event.get("type") == "message"
        and event.get("channel_type") == "im"
        and event.get("subtype") in ANSWERED_SUBTYPES
        and not event.get("bot_id")
        and not event.get("bot_profile")
        and bool(event.get("user"))
        and bool(event.get("ts"))
    )


@dataclass
class SlackAgent:
    ws: Workspace
    conversations: ConversationLog
    gateway: Gateway | None = None  # from study.yaml and the environment on first use
    gate: PhiGate | None = None
    greeted: dict[str, str] = field(default_factory=dict)  # Slack user -> person greeted as
    actions: SlackActionsLog | None = None  # where its imports and uploads are recorded
    opener: Opener = field(default=https_get)  # downloads attached files; tests pass a fake

    def __post_init__(self) -> None:
        if self.actions is None:
            self.actions = SlackActionsLog(self.ws.paths, clock=self.ws.clock)

    def _gateway(self) -> Gateway:
        if self.gateway is None:
            self.gateway = Gateway.for_study(self.ws.store, self.ws.paths.config, self.ws.study)
        return self.gateway

    def _gate(self) -> PhiGate:
        if self.gate is None:
            self.gate = PhiGate.for_workspace(self.ws)
        return self.gate

    # -- a message to the Agent ------------------------------------------------------------

    def on_message(self, event: dict[str, Any], client: Any) -> None:
        if not answers(event):
            return
        channel, thread = event["channel"], event.get("thread_ts") or event["ts"]
        self._status(client, channel, thread, "processing")
        try:
            self._turn(event, client, channel, thread)
        except Exception as exc:  # the person still gets an answer, and it is recorded
            log.exception("the Agent's turn failed")
            self._failed(event, client, channel, thread, type(exc).__name__)
        finally:
            self._status(client, channel, thread, "active")

    def _turn(self, event: dict[str, Any], client: Any, channel: str, thread: str) -> None:
        slack_user = event["user"]
        person = self.ws.people.person_for(slack_user)
        record = {"slack_user": slack_user, "channel": channel, "thread": thread,
                  "kind": "turn"}  # fmt: skip
        if person is None:
            self._say(client, channel, thread, NO_PERSON, None, None)
            self.conversations.record(**record, acting_as=None, outcome="refused",
                                      note="no person")  # fmt: skip
            return
        case_id = self._thread_case(person, channel, thread)
        context = AgentContext(person=person["id"], name=person["name"], role=person["role"],
                               site=person.get("site"), case_id=case_id)  # fmt: skip
        messages = self._history(client, event, channel, thread)
        turn = self._turn_of(event, client)
        if messages and messages[-1].role == "user":
            toolbox = agent_toolbox(self.ws, person, turn)
            reply = self._gateway().converse(context, messages, toolbox)
        else:  # nothing to answer (no words, no file): the Tasks, from code
            reply = AgentReply("off", "unavailable", note="an empty message")
        looked_at = self._cases_in_tools(person, reply)
        about = case_id or (looked_at[0] if len(looked_at) == 1 else None)
        record |= {"acting_as": person["id"], "case_id": about, "mode": reply.mode,
                   "simulated": reply.simulated, "tools": reply.tool_calls,
                   "ai_call": _call_ref(context.case_id, reply.call),
                   "note": reply.note or None}  # fmt: skip
        if turn.attachments:
            record["attachments"] = len(turn.attachments)
        if turn.done:  # what the Agent did as this person (the case events say it too)
            record["done"] = turn.done
        if reply.ok:
            cases = {*filter(None, [case_id]), *looked_at, *self._cases_named(reply.text)}
            verdict = self._say(client, channel, thread, reply.text, person, about,
                                cases=cases, label=reply.label)  # fmt: skip
            self._record_said(record, verdict, reply.text, source="ai")
            return
        if turn.attachments:  # without the AI no file is added: say so, and where to
            verdict = self._say(client, channel, thread, NO_FILES, person, about)
            self._record_said(record, verdict, NO_FILES, source="code")
            return
        text, intro, task_blocks = self._fallback(person)
        verdict = self._say(client, channel, thread, text, person, about, shown=intro,
                            extra=task_blocks)  # fmt: skip
        self._record_said(record, verdict, text, source="code")

    def _turn_of(self, event: dict[str, Any], client: Any) -> Turn:
        """What this message gives the acting tools: the Slack user and its files."""
        study = yaml.safe_load((self.ws.paths.config / self.ws.study / "study.yaml").read_text())
        limits = (study or {}).get("upload_limits") or {}
        files = SlackFiles(client, int(limits.get("max_file_mb", 50)),
                           int(limits.get("max_pdf_pages", 200)), self.opener)  # fmt: skip
        return Turn(slack_user=event["user"], actions=self.actions,
                    attachments=list(event.get("files") or []), files=files)  # fmt: skip

    def _failed(self, event: dict[str, Any], client: Any, channel: str, thread: str,
                error: str) -> None:  # fmt: skip
        try:
            person = self.ws.people.person_for(event["user"])
            self._say(client, channel, thread, FAILED, person, None)
            self.conversations.record(
                slack_user=event["user"], acting_as=person["id"] if person else None,
                channel=channel, thread=thread, kind="turn", outcome="failed", note=error,
            )  # fmt: skip
        except Exception:
            log.exception("answering a failed turn failed too")

    def _record_said(self, record: dict[str, Any], verdict: Verdict, text: str,
                     source: str) -> None:  # fmt: skip
        if verdict.held:
            self.conversations.record(**record, source=source, outcome="held",
                                      held=verdict.summary)  # fmt: skip
        else:
            self.conversations.record(**record, source=source, outcome="posted", reply=text)

    def _fallback(self, person: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        """The answer without AI: the person's Tasks, with their buttons. Returns the
        whole text (gated, and Slack's notification text), the line shown above the task
        blocks, and the blocks."""
        tasks = self.ws.tasks(person)
        if not tasks:
            return NOTHING, NOTHING, []
        lines = [f"• {t.case_id + ': ' if t.case_id else ''}{t.words}" for t in tasks]
        text = NO_AI + "\n" + "\n".join(lines[: blocks.MAX_ROWS])
        return text, NO_AI, blocks.tasks_reply(self.ws, person)[1:]

    # -- the greeting on the Messages tab --------------------------------------------------

    def on_messages_tab(self, event: dict[str, Any], client: Any) -> None:
        """A short greeting naming whom the Agent acts as, once per person: Slack sends
        this every time the tab is opened."""
        slack_user = event["user"]
        person = self.ws.people.person_for(slack_user)
        key = person["id"] if person else ""
        if self.greeted.get(slack_user) == key:
            return
        self.greeted[slack_user] = key
        channel = event.get("channel") or slack_user
        if person is None:
            text = NO_PERSON
        else:
            text = (f"Hi, I'm the adjudication agent. I act as "
                    f"*{self.ws.people.describe(person)}*. Ask me about your tasks and "
                    "cases. Signatures stay in the app.")  # fmt: skip
        verdict = self._say(client, channel, None, text, person, None)
        record = {"slack_user": slack_user, "acting_as": key or None, "channel": channel,
                  "thread": "", "kind": "greeting"}  # fmt: skip
        self._record_said(record, verdict, text, source="code")

    def on_session_event(self, event: dict[str, Any]) -> None:
        """app_context_changed, agent_session_stopped, agent_session_title_changed and
        file_shared: acknowledged so Slack does not retry; nothing to do yet."""
        log.debug("Slack agent event %s", event.get("type"))

    # -- posting ------------------------------------------------------------------------------

    def _say(
        self,
        client: Any,
        channel: str,
        thread: str | None,
        text: str,
        person: dict[str, Any] | None,
        case_id: str | None,
        *,
        cases: set[str] | None = None,
        label: str | None = None,
        shown: str | None = None,
        extra: list[dict[str, Any]] | None = None,
    ) -> Verdict:
        """Post `text` if the PHI gate passes it, else the hold notice; with *Open in the
        app* for this person. `shown` and `extra`: the section text and the blocks after
        it, when blocks carry part of `text` (the Tasks). Returns the gate's verdict.
        Never raises."""
        try:
            verdict = self._gate().check(text, cases or set(filter(None, [case_id])))
        except Exception as exc:  # no gate, no post: the text is held
            log.exception("the PHI gate is not available")
            verdict = Verdict(True, {}, type(exc).__name__)
        if verdict.held:
            out = [blocks._text(HOLD_TEXT)]
        else:
            out = [blocks._text(chunk) for chunk in _chunks(shown or text)]
            out += extra or []
        if person is not None:
            url = self.ws.link(person, self._page(person, case_id), case_id)
            out.append({"type": "actions", "elements": [blocks.open_button(
                url, action_id="open_app_agent")]})  # fmt: skip
        out.append(blocks._context(f"{label} · Synthetic data only." if label and not
                                   verdict.held else "Synthetic data only."))  # fmt: skip
        self._post(client, channel, thread, HOLD_TEXT if verdict.held else text, out)
        return verdict

    def _page(self, person: dict[str, Any], case_id: str | None) -> str:
        if case_id:
            task = next((t for t in self.ws.tasks(person) if t.case_id == case_id), None)
            if task:
                return task.page
            # a site's case opens on its documents, the Safety Specialist's on its QC page
            own = person["role"] in ("site", "safety_specialist")
            return LANDING[person["role"]] if own else "record"
        return LANDING.get(person["role"], "record")

    @staticmethod
    def _post(client: Any, channel: str, thread: str | None, text: str,
              out: list[dict[str, Any]]) -> None:  # fmt: skip
        kwargs: dict[str, Any] = {"channel": channel, "text": text, "blocks": out,
                                  "unfurl_links": False, "unfurl_media": False}  # fmt: skip
        if thread:
            kwargs["thread_ts"] = thread
        try:
            client.chat_postMessage(**kwargs)
        except Exception as exc:  # recorded by type only; the turn is still logged
            log.warning("posting to Slack failed: %s", type(exc).__name__)

    @staticmethod
    def _status(client: Any, channel: str, thread: str, status: str) -> None:
        try:
            client.agents_sessions_setStatus(channel_id=channel, thread_ts=thread, status=status)
        except Exception as exc:
            log.warning("setting the agent status to %s failed: %s", status, type(exc).__name__)

    # -- context -----------------------------------------------------------------------------

    def _thread_case(self, person: dict[str, Any], channel: str, thread: str) -> str | None:
        """The case of the notice this thread replies to, if this person can see it."""
        case_id = notice_case(self.ws.paths.root / "slack", channel, thread)
        return self.ws.find_case(person, case_id) if case_id else None

    def _history(self, client: Any, event: dict[str, Any], channel: str,
                 thread: str) -> list[ChatMessage]:  # fmt: skip
        """The thread so far (the person's messages and the app's), then this message.
        Consecutive messages of one side are joined; it starts with the person."""
        earlier: list[tuple[str, str]] = []
        if thread != event["ts"]:
            try:
                resp = client.conversations_replies(channel=channel, ts=thread, limit=100)
                for m in resp.get("messages") or []:
                    if m.get("ts") == event["ts"]:
                        continue
                    if m.get("bot_id"):
                        earlier.append(("assistant", _text_of(m)))
                    elif m.get("user") == event["user"] and m.get("subtype") in ANSWERED_SUBTYPES:
                        earlier.append(("user", _text_of(m)))
            except Exception as exc:  # answer this message alone
                log.warning("reading the thread failed: %s", type(exc).__name__)
        turns = [*earlier[-MAX_HISTORY:], ("user", _text_of(event))]
        merged: list[tuple[str, str]] = []
        for role, text in turns:
            if not text:
                continue
            if merged and merged[-1][0] == role:
                merged[-1] = (role, merged[-1][1] + "\n\n" + text)
            else:
                merged.append((role, text))
        while merged and merged[0][0] != "user":
            merged.pop(0)
        return [ChatMessage(role=r, text=t) for r, t in merged]  # type: ignore[arg-type]

    def _cases_in_tools(self, person: dict[str, Any], reply: AgentReply) -> list[str]:
        """The cases this turn's tools looked at, among this person's cases."""
        out: list[str] = []
        for call in reply.tool_calls:
            named = str((call.get("input") or {}).get("case") or "")
            case_id = self.ws.find_case(person, named) if named else None
            if case_id and case_id not in out:
                out.append(case_id)
        return out

    def _cases_named(self, text: str) -> set[str]:
        """Cases a reply names by case ID or subject ID: their seeded values are checked."""
        states = self.ws.store.list_case_states()
        ids = set(CASE_RE.findall(text))
        subjects = set(SUBJECT_RE.findall(text))
        return {cs.case_id for cs in states if cs.case_id in ids or cs.subject in subjects}


def _call_ref(case_id: str | None, call: dict[str, str] | None) -> dict[str, Any] | None:
    """Where the turn's AI call record is: in the case folder, or under data/ai/calls/
    for a turn about no case (or whose case was gone when it was saved)."""
    if call is None:
        return None
    return {"case_id": None if call["path"].startswith("ai/calls/") else case_id, **call}


def _text_of(message: dict[str, Any]) -> str:
    """A message's text as the person wrote it (Slack escapes &, < and >), and how many
    files are attached to it (never their names, which may carry identifiers)."""
    text = html.unescape(str(message.get("text") or "")).strip()
    files = message.get("files") or []
    if files:
        note = f"(attached {len(files)} file{'s' if len(files) > 1 else ''})"
        text = f"{text}\n\n{note}" if text else note
    return text


def _chunks(text: str) -> list[str]:
    """Text in pieces Slack's sections can hold, split between paragraphs if it can."""
    out, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > SECTION_CHARS:
            out.append(para[:SECTION_CHARS])
            para = para[SECTION_CHARS:]
        if current and len(current) + 2 + len(para) > SECTION_CHARS:
            out.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        out.append(current)
    return out or [""]
