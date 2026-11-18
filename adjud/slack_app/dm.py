"""The Slack Channel (P-55, ADR 0003): each Notification as a DM from the app, with an
*Open in the app* button. Replaces the incoming webhook of P-54.

A notice goes to the Slack user of each person behind its recipient: their own
`slack_user_id` in users.yaml (real use), else the demo presenter (the Slack account that
chose someone to act as). People who share one Slack account get one DM naming them all.
Same content as the e-mail, never Clinical Content (ADR 0004). A failure is a recorded
Delivery Attempt (adjud.delivery) and never holds up e-mail or the workflow.

Configuration, from the environment only: NOTIFY_CHANNELS must include `slack`;
SLACK_BOT_TOKEN (a secret, never logged); APP_BASE_URL for the links.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from adjud.delivery import SLACK, ChannelError
from adjud.slack_app.blocks import notice
from adjud.slack_app.core import ROLE_WORDS, Directory

ACTOR = "system:slack"
TIMEOUT_SECONDS = 10


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
    name: str = SLACK

    def send(self, note: dict[str, Any], people: list[dict[str, Any]]) -> None:
        targets: dict[str, list[dict[str, Any]]] = {}
        for p in people:
            slack_user = self.directory.slack_user_for(p["id"])
            if slack_user:
                targets.setdefault(slack_user, []).append(p)
        if not targets:
            raise ChannelError("no Slack user yet: choose someone with /adj act-as")
        url = f"{self.settings.base_url}{note['link']}" if note.get("link") else None
        for slack_user, group in targets.items():
            text, blocks = notice(note, for_whom(note["to"], group, self.directory), url)
            self._post(channel=slack_user, text=text, blocks=blocks)

    def _post(self, **kwargs: Any) -> None:
        from slack_sdk.errors import SlackApiError

        try:
            self.client.chat_postMessage(unfurl_links=False, unfurl_media=False, **kwargs)
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
