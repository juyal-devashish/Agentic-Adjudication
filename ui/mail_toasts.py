"""A pop-up at the bottom of the screen for every e-mail the runner sends.

The runner, not the UI, sends e-mail (adjud/mailer.py), up to one runner tick after the
action that queued it. So the pop-ups follow the delivery log the mailer writes once the
mail server has accepted a message, checked every few seconds by a fragment that re-runs
only itself. Each viewer sees the e-mails about cases they may see (site segregation),
whoever they were sent to. On the first check the log so far counts as seen, so opening
the app does not replay old e-mails.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st
from common import context, paths, pipeline, users

SEEN = "mail_toasts_seen"  # lines of the delivery log already shown in this session
# Flows 3–4 e-mails name adjudicators: a site or another adjudicator sees only its own.
BLINDED = {"adjudication_assigned", "adj3_assigned", "consensus_to_sign", "committee_needed",
           "more_data_requested", "outcome_for_pv", "adjudication_complete",
           "readjudication_started"}  # fmt: skip


def _notification(note_id: str) -> dict[str, Any] | None:
    """The queued notification an entry of the delivery log refers to."""
    for f in paths().outbox.glob(f"*-{note_id[:8]}-*.json"):
        note = json.loads(f.read_text())
        if note.get("id") == note_id:
            return note
    return None


def _who(user_ids: list[str]) -> str:
    """Names only, to keep the pop-up short: Mailpit and the case record show addresses."""
    people = {u["id"]: u["name"] for u in users()}
    return ", ".join(people.get(u, u) for u in user_ids)


@st.fragment(run_every=3)
def show() -> None:
    log = paths().outbox / "delivery.jsonl"
    lines = [ln for ln in log.read_text().splitlines() if ln.strip()] if log.exists() else []
    if SEEN not in st.session_state:
        st.session_state[SEEN] = len(lines)
        return
    if len(lines) < st.session_state[SEEN]:  # the demo was reset: start again
        st.session_state[SEEN] = 0
    new, st.session_state[SEEN] = lines[st.session_state[SEEN] :], len(lines)
    ctx = context()
    store = pipeline().store
    cases = set(store.list_cases()) if new else set()
    for line in new:
        entry = json.loads(line)
        if entry.get("channel", "email") != "email":  # e-mail pop-ups only (P-54)
            continue
        note = _notification(entry["id"])
        if note is None or not note.get("case_id"):
            continue
        if note["case_id"] not in cases:  # removed by a demo reset since the e-mail
            continue
        if not ctx.sees(store.state(note["case_id"]).subject, note["case_id"]):
            continue
        if (
            ctx.role in ("site", "adjudicator")
            and note["template"] in BLINDED
            and note["to"] not in (f"user:{ctx.actor}", f"site:{ctx.user.get('site')}")
        ):
            continue  # who adjudicates is not shown to the site or other adjudicators
        case, task = note["case_id"], note["subject"]
        if entry["status"] == "sent":
            st.toast(
                f"{case}: e-mail sent to {_who(entry['to'])}. {task}",
                icon=":material/mail:",
                duration="long",
            )
        else:
            st.toast(
                f"{case}: e-mail to {_who(entry['to'])} failed. {task}",
                icon=":material/error:",
                duration="long",
            )
