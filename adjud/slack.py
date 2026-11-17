"""Slack, on trial next to e-mail (plan P-54, ADR 0002).

Every Notification is posted to one demo channel through a Slack incoming webhook,
saying who it is for. Same content as the e-mail: study, case ID, recipient, task and
link, never clinical content; link previews are off, so Slack's servers never open the
link. Only the presenter should be a member of that channel: it shows every recipient's
notices, including who adjudicates which case.

Configuration, from the environment only (never written to data/ or the repository):

    NOTIFY_CHANNELS    must include `slack` (adjud.delivery)
    SLACK_WEBHOOK_URL  the channel's incoming webhook; a secret, never logged
    APP_BASE_URL       where links point (default http://localhost:8501), as for e-mail

Only Notifications created after the runner started with Slack on are posted. A failure
is a recorded Delivery Attempt and never blocks the workflow or the e-mail.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from adjud.delivery import SLACK, ChannelError

log = logging.getLogger("adjud.slack")
ACTOR = "system:slack"
TIMEOUT_SECONDS = 10
ROLE_WORDS = {
    "safety_specialist": "Safety Specialist",
    "data_manager": "Data Manager",
    "cec_coordinator": "CEC Coordinator",
    "adjudicator": "Adjudicator",
}

# (url, JSON body, timeout) -> (HTTP status, response text). Raises OSError (incl. timeout)
# when Slack cannot be reached. Tests pass a fake; nothing in tests reaches Slack.
Post = Callable[[str, bytes, float], tuple[int, str]]


def urllib_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    # https only: SlackSettings.from_env refuses any other scheme.
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode("utf-8", "replace")
    except urllib.error.URLError as exc:  # urllib wraps a connect timeout
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError from exc
        raise


@dataclass(frozen=True)
class SlackSettings:
    webhook_url: str = field(repr=False)  # a secret: kept out of repr, logs and errors
    base_url: str = "http://localhost:8501"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SlackSettings | None:
        """None when SLACK_WEBHOOK_URL is not set, or is not an https URL."""
        env = os.environ if env is None else env
        url = env.get("SLACK_WEBHOOK_URL", "").strip()
        if not url.startswith("https://"):
            if url:
                log.warning("SLACK_WEBHOOK_URL must start with https://: Slack off")
            return None
        base = (env.get("APP_BASE_URL") or "http://localhost:8501").rstrip("/")
        return cls(webhook_url=url, base_url=base)


def _esc(text: str) -> str:
    """Slack's own escaping for message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def for_whom(to: str, people: list[dict[str, Any]]) -> str:
    """'Priya Nair (Site 101)', 'Dr. Anna Weber (Safety Specialist)', no addresses."""
    names = ", ".join(u.get("name", u["id"]) for u in people)
    kind, _, value = to.partition(":")
    if kind == "site":
        return f"{names} (Site {value})"
    if kind == "mailbox":
        return f"{names} (mailbox)"
    roles = {ROLE_WORDS.get(u.get("role", ""), u.get("role", "")) for u in people}
    return f"{names} ({', '.join(sorted(r for r in roles if r))})" if any(roles) else names


def message(note: dict[str, Any], people: list[dict[str, Any]], base_url: str) -> dict[str, Any]:
    """The Slack message for one Notification: who it is for, the task, the case, a link."""
    case = note.get("case_id") or "-"
    task = note["subject"][:1].lower() + note["subject"][1:]
    lines = [
        _esc(f"[{note['study']}] Case {case}: {task}"),
        _esc(f"For: {for_whom(note['to'], people)}"),
    ]
    if note.get("link"):
        lines.append(f"<{base_url}{note['link']}|Open the case>")
    lines.append("_Synthetic data only. No patient or clinical information._")
    return {"text": "\n".join(lines), "unfurl_links": False, "unfurl_media": False}


def _reason(status: int, text: str) -> str:
    """'HTTP 404 no_service': Slack's error word only, nothing else from the reply."""
    first = (text.split() or [""])[0].lower()
    word = first if re.fullmatch(r"[a-z_]{1,40}", first) else ""
    return f"HTTP {status} {word}".strip()


@dataclass
class SlackChannel:
    settings: SlackSettings
    post: Post = urllib_post
    name: str = SLACK

    def send(self, note: dict[str, Any], people: list[dict[str, Any]]) -> None:
        body = json.dumps(message(note, people, self.settings.base_url)).encode()
        try:
            status, text = self.post(self.settings.webhook_url, body, TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise ChannelError("timeout", unreachable=True) from exc
        except OSError as exc:
            raise ChannelError(f"cannot connect: {type(exc).__name__}", unreachable=True) from exc
        if status == 200 and text.strip() == "ok":
            return
        # Rate limited or Slack's side failing: stop for this tick; the next tick tries again.
        raise ChannelError(_reason(status, text), unreachable=status == 429 or status >= 500)
