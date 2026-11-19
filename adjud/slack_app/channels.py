"""One public Slack channel per demo character (P-58, demo only).

study.yaml's `slack_character_channels` names a channel per person (by role, e.g.
`site-user`). When the Slack app starts it finds each channel, creates the missing ones
and joins them, then writes `data/slack/channels.json` (name -> channel id) for the
runner and the Agent. A channel the app could not find, create or join is left out, and
that person's notices go as DMs as before. Nothing here raises: a missing scope or a
Slack error is logged by its error word only.
"""

from __future__ import annotations

import logging
from typing import Any

from adjud.slack_app.core import Directory

log = logging.getLogger("adjud.slack_app")


def _error(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    word = resp.get("error") if resp is not None and hasattr(resp, "get") else None
    return str(word) if word else type(exc).__name__


def _public_channels(client: Any) -> dict[str, dict[str, Any]]:
    """The workspace's public channels that are not archived, by name."""
    out: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"types": "public_channel", "exclude_archived": True,
                                  "limit": 200}  # fmt: skip
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_list(**kwargs)
        for ch in resp.get("channels") or []:
            out[ch["name"]] = ch
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return out


def ensure_channels(client: Any, directory: Directory) -> dict[str, str]:
    """Find, create and join each character channel; write and return name -> id for
    those the app is a member of. No mapping: nothing is called and nothing written."""
    wanted = sorted(set(directory.character_channels.values()))
    if not wanted:
        return {}
    try:
        existing = _public_channels(client)
    except Exception as exc:
        log.warning("listing Slack channels failed (%s): notices go as DMs", _error(exc))
        existing = {}
    ids: dict[str, str] = {}
    for name in wanted:
        try:
            ch = existing.get(name)
            if ch is None:
                ch = client.conversations_create(name=name, is_private=False)["channel"]
                log.info("created the Slack channel #%s", name)
            client.conversations_join(channel=ch["id"])
            ids[name] = ch["id"]
        except Exception as exc:
            log.warning("the Slack channel #%s is not available (%s): its person's notices "
                        "go as DMs", name, _error(exc))  # fmt: skip
    try:
        directory.save_channel_ids(ids)
    except OSError as exc:
        log.warning("slack/channels.json could not be written (%s): notices go as DMs",
                    type(exc).__name__)  # fmt: skip
    log.info("character channels ready: %s", ", ".join(f"#{n}" for n in ids) or "none")
    return ids
