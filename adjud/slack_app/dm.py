"""The Slack Channel (P-55, ADR 0003): each Notification as a DM from the app, with an
*Open in the app* button. Replaces the incoming webhook of P-54.

A notice goes to the Slack user of each person behind its recipient: their own
`slack_user_id` in users.yaml (real use), else the demo presenter (the Slack account that
chose someone to act as). People who share one Slack account get one DM naming them all.
In the demo, a person with a character channel (study.yaml `slack_character_channels`,
P-58) gets the notice there instead, as a top-level message opening the app as them.
Same content as the e-mail, except for the two DMs below; never PHI (ADR 0005). A
failure is a recorded Delivery Attempt (adjud.delivery) and never holds up e-mail or the
workflow.

Each DM's channel and `ts` are recorded in `data/slack/dm_sent.jsonl` with its case, so
the DM starts a Conversation: a reply in its thread reaches the Agent with that case as
context (P-58, Q12).

Two DMs say more than their e-mails (ADR 0005, `NoticeDetail`), when the PHI gate passes
the text: the site's `query_opened` carries the signed Site Query's reasons and text
(Q13), and the site answers by attaching files in its thread; the Safety Specialist's
`qc_review_needed` carries the narrative draft (P-59). The note in the outbox, and so the
e-mail, is unchanged.

Configuration, from the environment only: NOTIFY_CHANNELS must include `slack`;
SLACK_BOT_TOKEN (a secret, never logged); APP_BASE_URL for the links.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from adjud.delivery import SLACK, ChannelError
from adjud.flow2 import QUERY_CODES, qc_drafts
from adjud.slack_app.blocks import notice
from adjud.slack_app.core import ROLE_WORDS, Directory, Workspace

log = logging.getLogger("adjud.slack_app")

ACTOR = "system:slack"
TIMEOUT_SECONDS = 10


def notice_case(slack_dir: Path, channel: str, thread_ts: str) -> str | None:
    """The case of the DM notice that starts this thread, if it is one (P-58, Q12): a
    reply in a notice's thread is a Conversation about that case."""
    path = slack_dir / "dm_sent.jsonl"
    if not path.exists() or not thread_ts:
        return None
    for line in path.read_text().splitlines():
        sent = json.loads(line) if line.strip() else {}
        if sent.get("ts") == thread_ts and sent.get("channel") == channel:
            return sent.get("case_id")
    return None


class PostClient(Protocol):
    """The one slack_sdk WebClient method used here; tests pass a fake."""

    def chat_postMessage(self, **kwargs: Any) -> Any: ...  # noqa: N802 (slack_sdk's name)


@dataclass(frozen=True)
class SlackSettings:
    bot_token: str = field(repr=False)  # a secret: kept out of repr, logs and errors
    base_url: str = "http://localhost:8501"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SlackSettings | None:
        """None when SLACK_BOT_TOKEN is not set."""
        env = os.environ if env is None else env
        token = env.get("SLACK_BOT_TOKEN", "").strip()
        if not token:
            return None
        base = (env.get("APP_BASE_URL") or "http://localhost:8501").rstrip("/")
        return cls(bot_token=token, base_url=base)

    def client(self) -> PostClient:
        from slack_sdk import WebClient

        return WebClient(token=self.bot_token, timeout=TIMEOUT_SECONDS)


def for_whom(to: str, people: list[dict[str, Any]], directory: Directory) -> str:
    """'Priya Nair (Site user, Larkfield General Hospital)'; several people share a role."""
    if to.startswith("mailbox:"):
        return ", ".join(p.get("name", p["id"]) for p in people) + " (mailbox)"
    if len(people) == 1:
        return directory.describe(people[0])
    roles = sorted({ROLE_WORDS.get(p.get("role", ""), p.get("role", "")) for p in people})
    return ", ".join(p["name"] for p in people) + f" ({', '.join(roles)})"


@dataclass
class SlackDmChannel:
    directory: Directory
    settings: SlackSettings
    client: PostClient
    # (person, case ID) -> their next step in Task words, e.g. Workspace.next_step (P-57)
    next_step: Callable[[dict[str, Any], str], str] | None = None
    # note -> what the DM says beyond the e-mail, already PHI-gated (Q13), e.g. NoticeDetail
    detail: Callable[[dict[str, Any]], str | None] | None = None
    name: str = SLACK

    def send(self, note: dict[str, Any], people: list[dict[str, Any]]) -> None:
        # Where each person's notice goes: their character channel (demo, P-58), else
        # their Slack user's DM. In a channel the link opens the app as that person.
        targets: dict[str, list[dict[str, Any]]] = {}
        in_channel: set[str] = set()
        for p in people:
            channel = self.directory.channel_of(p["id"])
            target = channel or self.directory.slack_user_for(p["id"])
            if target:
                targets.setdefault(target, []).append(p)
            if channel:
                in_channel.add(channel)
        if not targets:
            raise ChannelError("no Slack user yet: choose someone with /adj act-as", later=True)
        url = f"{self.settings.base_url}{note['link']}" if note.get("link") else None
        reached = self.reached(note["id"])
        detail = self.detail(note) if self.detail is not None else None
        for target, group in targets.items():
            if target in reached:  # sent on an earlier try that failed for someone else
                continue
            link = as_person(url, group[0]) if target in in_channel and url else url
            text, blocks = notice(note, for_whom(note["to"], group, self.directory), link,
                                  self._next(note, group[0]), detail)  # fmt: skip
            response = self._post(channel=target, text=text, blocks=blocks)
            self._mark(note, target, response, channel=target if target in in_channel else None)

    def _next(self, note: dict[str, Any], person: dict[str, Any]) -> str | None:
        """Worked out when the DM is sent, so it says where the case is now. None for a
        mailbox, a notice with no case, or a channel built without a next_step."""
        if self.next_step is None or not note.get("case_id") or "role" not in person:
            return None
        return self.next_step(person, note["case_id"])

    @property
    def sent_path(self) -> Path:
        return self.directory.slack_dir / "dm_sent.jsonl"

    def reached(self, note_id: str) -> set[str]:
        """Slack users this notice already reached, so a retry never DMs them again."""
        if not self.sent_path.exists():
            return set()
        lines = (json.loads(ln) for ln in self.sent_path.read_text().splitlines() if ln.strip())
        return {d["slack_user"] for d in lines if d["note"] == note_id}

    def _mark(self, note: dict[str, Any], slack_user: str, response: Any,
              channel: str | None = None) -> None:  # fmt: skip
        """Who it reached, and where: the DM channel (`D…`, what a reply's message event
        carries) or the character channel, and the message's `ts`, so a reply in its thread
        finds the case (Q12). `slack_user` is the target posted to: a Slack user, or a
        character channel's id (P-58)."""
        sent = {"note": note["id"], "slack_user": slack_user, "case_id": note.get("case_id")}
        for key in ("channel", "ts"):
            value = response.get(key) if hasattr(response, "get") else None
            if isinstance(value, str):
                sent[key] = value
        if channel:  # a character channel: posted there whatever the response says
            sent["channel"] = channel
        self.sent_path.parent.mkdir(parents=True, exist_ok=True)
        with self.sent_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sent) + "\n")

    def _post(self, **kwargs: Any) -> Any:
        from slack_sdk.errors import SlackApiError

        try:
            return self.client.chat_postMessage(unfurl_links=False, unfurl_media=False, **kwargs)
        except SlackApiError as exc:
            resp = exc.response
            status = getattr(resp, "status_code", 0) or 0
            word = str(resp.get("error", "")) if resp is not None else ""
            error = word if re.fullmatch(r"[a-z_]{1,40}", word) else "error"
            raise ChannelError(
                f"Slack {error}", unreachable=status == 429 or status >= 500
            ) from None
        except TimeoutError as exc:
            raise ChannelError("timeout", unreachable=True) from exc
        except OSError as exc:
            raise ChannelError(f"cannot connect: {type(exc).__name__}", unreachable=True) from exc
        except Exception as exc:  # anything else: recorded by type only, never its message
            raise ChannelError(f"error: {type(exc).__name__}") from None


def as_person(url: str, person: dict[str, Any]) -> str:
    """The link with this person as its `role` and `user`, so the app opens as them."""
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.update({"role": person["role"], "user": person["id"]})
    return urlunparse(parts._replace(query=urlencode(query)))


def latest_query(ws: Workspace, case_id: str) -> dict[str, Any] | None:
    """The case's latest signed `query.opened` event."""
    return next((e for e in reversed(ws.store.events(case_id))
                 if e["type"] == "query.opened"), None)  # fmt: skip


def narrative_now(ws: Workspace, case_id: str) -> tuple[str, dict[str, Any]] | None:
    """The narrative as it stands in the case's QC review, as the Quality check page
    starts it (`qc_drafts`, P-59), else the latest AI or template draft, with that
    `narrative.drafted` event's data."""
    events = ws.store.events(case_id)
    drafted = next((e for e in reversed(events) if e["type"] == "narrative.drafted"), None)
    if drafted is None:
        return None
    drafts = qc_drafts(ws.store, case_id)
    text = drafts.narrative or ws.store.get_file(case_id, drafted["data"]["draft"]["path"]).decode()
    return text.strip(), {**drafted["data"], "edited": drafts.narrative_edited,
                          "earlier": drafts.narrative_via == "earlier_ai"}  # fmt: skip


@dataclass
class NoticeDetail:
    """What a DM says beyond its e-mail (P-58, ADR 0005), read from the case when the DM is
    sent and posted only if the PHI gate passes it. The note in the outbox, and so the
    e-mail, is unchanged.

    - the site's `query_opened`: the signed Site Query's reasons and text (Q13);
    - the Safety Specialist's `qc_review_needed`: the narrative draft, so it can be read
      and changed in Slack without asking for it (Nirav's review, 2026-11-19, P-59).

    Never raises: without a gate or a text the DM goes as the e-mail does. A narrative the
    gate holds is replaced by a line saying so."""

    ws: Workspace
    gate: Any = None  # a PhiGate; built on first use

    def __call__(self, note: dict[str, Any]) -> str | None:
        case_id = note.get("case_id")
        make = {"query_opened": self._query, "qc_review_needed": self._narrative}.get(
            note.get("template", "")
        )
        if make is None or not case_id:
            return None
        try:
            text = make(case_id)
            if text is None:
                return None
            if self.gate is None:
                from adjud.slack_app.phi_gate import PhiGate

                self.gate = PhiGate.for_workspace(self.ws)
            verdict = self.gate.check(text, {case_id})
        except Exception as exc:  # no gate, no text: the DM goes without it
            log.warning("the DM text for %s could not be checked: %s", note["template"],
                        type(exc).__name__)  # fmt: skip
            return None
        if verdict.held:
            log.info("the %s text of %s held from its DM: %s", note["template"], case_id,
                     verdict.found)  # fmt: skip
            return NARRATIVE_HELD if note["template"] == "qc_review_needed" else None
        return text

    def _query(self, case_id: str) -> str | None:
        q = latest_query(self.ws, case_id)
        if q is None:
            return None
        reasons = ", ".join(QUERY_CODES.get(c, c) for c in q["data"].get("codes") or [])
        text = f"Query: {reasons}. {q['data'].get('text', '')}".strip()
        return text + "\nAnswer by attaching the files in this thread, or upload them in the app."

    def _narrative(self, case_id: str) -> str | None:
        now = narrative_now(self.ws, case_id)
        if now is None:
            return None
        text, d = now
        if d["edited"]:
            source = "your unsigned edit"
        elif d.get("earlier"):
            source = "the AI draft from before the query; check it against the new documents"
        else:
            source = {"ai": "drafted by AI", "template": "from the study template"}.get(
                d.get("source"), "draft"
            )
            source += ", simulated" if d.get("simulated") else ""
        return (f"Narrative draft ({source}, not signed):\n{text}\n\nReply in this thread to "
                "change it. Sign it, or send a query, in the app.")  # fmt: skip


NARRATIVE_HELD = "The narrative draft may contain identifiers, so it is shown only in the app."
