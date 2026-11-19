"""Run the Slack app over Socket Mode: no public address, no tunnel (P-55, ADR 0003).

    uv run --env-file .env python -m adjud.slack_app

Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN (an app-level token with connections:write)
in the environment, never in data/ or the repository. Run one copy only: Slack sends
each click to one of the app's open connections. The runner sends the DMs; this process
answers `/adj`, App Home and buttons, and at its start makes sure the demo's character
channels exist (`channels.py`, P-58).
"""

from __future__ import annotations

import logging
import os
import sys

from adjud.paths import DataPaths
from adjud.slack_app.app import SlackHandlers, build_app
from adjud.slack_app.channels import ensure_channels
from adjud.slack_app.core import SlackActionsLog, Workspace


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bot, app_token = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_APP_TOKEN")
    if not bot or not app_token:
        print("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN (see docs/demo-guide.md).", file=sys.stderr)
        return 1
    paths = DataPaths.from_env()
    base = (os.environ.get("APP_BASE_URL") or "http://localhost:8501").rstrip("/")
    handlers = SlackHandlers(Workspace(paths, base_url=base), SlackActionsLog(paths))
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app(handlers, bot)
    ensure_channels(app.client, handlers.ws.people)  # demo character channels (P-58)
    SocketModeHandler(app, app_token).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
