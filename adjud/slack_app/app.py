"""The Bolt app (P-55, ADR 0003): `/adj`, App Home, buttons and the Acting As form, and
the Agent's events in the Messages tab (P-58, `agent.py`).

Handlers are plain methods of `SlackHandlers` taking Bolt's arguments (ack, command,
body, respond, client), so tests drive them with a fake client and never reach Slack.
Each acknowledges first (Slack waits 3 seconds at most), then does the work. Nothing
here signs or shows Clinical Content: signed and clinical steps open the app.

Every interaction is one entry in the hash-chained Slack actions log: the Slack user,
whom they act as, the action, the cases and the result.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from adjud.slack_app import blocks
from adjud.slack_app.agent import SlackAgent
from adjud.slack_app.conversations import ConversationLog
from adjud.slack_app.core import SlackActionsLog, Workspace

log = logging.getLogger("adjud.slack_app")
EPHEMERAL = "ephemeral"
SUBCOMMANDS = {"help", "act-as", "tasks", "cases", "status", "import", "pull"}


@dataclass
class SlackHandlers:
    ws: Workspace
    actions: SlackActionsLog
    notes: dict[str, str] = field(default_factory=dict)  # Slack user -> last result on Home
    agent: SlackAgent | None = None  # the Agent in the Messages tab (P-58)

    def __post_init__(self) -> None:
        if self.agent is None:
            self.agent = SlackAgent(self.ws, ConversationLog(self.ws.paths))

    @property
    def people(self) -> Any:
        return self.ws.people

    def _who(self, slack_user: str) -> dict[str, Any] | None:
        return self.people.person_for(slack_user)

    def _record(self, slack_user: str, action: str, **kw: Any) -> None:
        person = self._who(slack_user)
        self.actions.record(slack_user, person["id"] if person else None, action, **kw)

    def publish_home(self, client: Any, slack_user: str) -> None:
        view = blocks.home(self.ws, self._who(slack_user), self.notes.get(slack_user))
        client.views_publish(user_id=slack_user, view=view)

    # -- /adj -----------------------------------------------------------------------------------

    def on_command(self, ack: Any, command: dict[str, Any], respond: Any, client: Any) -> None:
        ack()
        slack_user = command["user_id"]
        sub, _, rest = (command.get("text") or "").strip().partition(" ")
        sub, rest = sub.lower(), rest.strip()
        known = sub if sub in SUBCOMMANDS else "unknown"  # typed text never goes in the log
        person = self._who(slack_user)

        def reply(content: list[dict[str, Any]] | str, text: str = "Adjudication") -> None:
            if isinstance(content, str):
                content, text = [blocks._text(blocks.esc(content))], content
            respond(blocks=content, text=text, response_type=EPHEMERAL, replace_original=False)

        if sub in ("", "help"):
            self._record(slack_user, "command:help")
            reply([blocks._text(blocks.HELP)], "Adjudication commands")
            return
        if sub == "act-as":
            self._act_as_command(slack_user, rest, command, reply, client)
            return
        if person is None:
            self._record(slack_user, f"command:{known}", result="refused", reason="no person")
            reply("Choose whom you act as first: `/adj act-as` (demo).")
            return
        if sub == "tasks":
            self._record(slack_user, "command:tasks")
            reply(blocks.tasks_reply(self.ws, person), "Your tasks")
        elif sub == "cases":
            self._record(slack_user, "command:cases")
            reply(blocks.cases_reply(self.ws, person), "Your cases")
        elif sub == "status":
            case_id = self.ws.find_case(person, rest) if rest else None
            if case_id is None:
                self._record(slack_user, "command:status", result="refused",
                             reason="not one of their cases")  # fmt: skip
                ids = ", ".join(cs.case_id for cs in self.ws.cases(person)) or "none yet"
                reply(f"Which case? For example `/adj status 0001`. Your cases: {ids}.")
                return
            self._record(slack_user, "command:status", cases=[case_id])
            reply(blocks.status_reply(self.ws, person, self.ws.status(person, case_id)), case_id)
        elif sub == "import":
            reply(self._import(slack_user, person))
            self.publish_home(client, slack_user)
        elif sub == "pull":
            reply(self._import(slack_user, person, "pull"))
            self.publish_home(client, slack_user)
        else:
            self._record(slack_user, "command:unknown", result="refused", reason="unknown")
            reply([blocks._text("I don't know that one.\n" + blocks.HELP)], "Unknown command")

    def _act_as_command(self, slack_user: str, rest: str, command: dict[str, Any],
                        reply: Any, client: Any) -> None:  # fmt: skip
        if not rest:
            current = self._who(slack_user)
            view = blocks.act_as_modal(self.people, current["id"] if current else None)
            client.views_open(trigger_id=command["trigger_id"], view=view)
            self._record(slack_user, "act_as:opened")
            return
        found = self.people.find(rest)
        if len(found) != 1:
            names = ", ".join(u["name"] for u in found[:8]) or "nobody"
            self._record(slack_user, "act_as", result="refused", reason=f"{len(found)} matches")
            reply(f"'{rest}' matches {names}. Be more precise, or type `/adj act-as` for a list.")
            return
        person = self.people.act_as(slack_user, found[0]["id"])
        self._record(slack_user, "act_as")
        reply(f"You now act as {self.people.describe(person)}. Your App Home shows their tasks.")
        self.publish_home(client, slack_user)

    def _import(self, slack_user: str, person: dict[str, Any], action: str = "import") -> str:
        """The data manager's import of the queued files ("import") or EDC pull ("pull"),
        the same calls as the app's buttons."""
        run = self.ws.pull_from_edc if action == "pull" else self.ws.import_queued
        try:
            summary, created = run(person)
        except PermissionError as exc:
            self._record(slack_user, action, result="refused", reason="not the data manager")
            return str(exc)
        except Exception as exc:  # e.g. the runner took the same file: say so, log it
            log.exception("%s from Slack failed", action)
            self._record(slack_user, action, result="failed", reason=type(exc).__name__)
            return f"The {action} did not finish. Try again, or use AE import in the app."
        self._record(slack_user, action, cases=created)
        self.notes[slack_user] = summary
        return summary + (" The sites are notified now." if created else "")

    # -- App Home ------------------------------------------------------------------------------

    def on_home_opened(self, event: dict[str, Any], client: Any) -> None:
        if event.get("tab") == "messages":  # the conversation with the Agent (P-58)
            assert self.agent is not None
            self.agent.on_messages_tab(event, client)
            return
        if event.get("tab", "home") != "home":
            return
        self._record(event["user"], "home_opened")
        self.publish_home(client, event["user"])

    # -- buttons -------------------------------------------------------------------------------

    def on_switch_person(self, ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        slack_user = body["user"]["id"]
        current = self._who(slack_user)
        view = blocks.act_as_modal(self.people, current["id"] if current else None)
        client.views_open(trigger_id=body["trigger_id"], view=view)
        self._record(slack_user, "act_as:opened")

    def on_import_button(self, ack: Any, body: dict[str, Any], client: Any,
                         respond: Any = None) -> None:  # fmt: skip
        ack()
        slack_user = body["user"]["id"]
        person = self._who(slack_user)
        if person is None:
            self._record(slack_user, "import", result="refused", reason="no person")
            return
        summary = self._import(slack_user, person)
        if respond is not None and body.get("response_url"):
            respond(text=summary, response_type=EPHEMERAL, replace_original=False)
        self.publish_home(client, slack_user)

    def on_open_app(self, ack: Any, body: dict[str, Any]) -> None:
        """A link button: the browser opens the app; here it is only acknowledged and
        logged, with the case it named."""
        ack()
        action = (body.get("actions") or [{}])[0]
        case = parse_qs(urlparse(action.get("url", "")).query).get("case", [])
        self._record(body["user"]["id"], "open_app", cases=case[:1])

    # -- the Acting As form ---------------------------------------------------------------------

    def on_act_as_submitted(self, ack: Any, body: dict[str, Any], view: dict[str, Any],
                            client: Any) -> None:  # fmt: skip
        slack_user = body["user"]["id"]
        chosen = view["state"]["values"]["who"]["person"].get("selected_option") or {}
        person_id = chosen.get("value")
        if person_id not in self.people.by_id:
            self._record(slack_user, "act_as", result="refused", reason="no person chosen")
            ack(response_action="errors", errors={"who": "Choose a person."})
            return
        ack()
        self.people.act_as(slack_user, person_id)
        self._record(slack_user, "act_as")
        self.notes.pop(slack_user, None)
        self.publish_home(client, slack_user)


def build_app(handlers: SlackHandlers, bot_token: str, *, verify: bool = True) -> Any:
    """The Bolt app with every listener registered. `verify=False` skips Bolt's call to
    Slack's auth.test at start-up (tests)."""
    from slack_bolt import App

    app = App(token=bot_token, token_verification_enabled=verify)
    app.command("/adj")(handlers.on_command)
    app.event("app_home_opened")(handlers.on_home_opened)
    app.action("switch_person")(handlers.on_switch_person)
    app.action("import_queued")(handlers.on_import_button)
    app.action(re.compile(r"^open_app"))(handlers.on_open_app)
    app.view("act_as")(handlers.on_act_as_submitted)
    agent = handlers.agent
    assert agent is not None
    app.event("message")(agent.on_message)  # message.im; edits and bots are ignored there
    for name in ("app_context_changed", "agent_session_stopped", "agent_session_title_changed",
                 "file_shared"):  # fmt: skip
        app.event(name)(agent.on_session_event)
    return app
