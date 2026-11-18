"""What Slack shows (Block Kit), built only from core.py's clinical-free facts (ADR 0004).

Every block here carries case IDs, workflow words, site and people's names, counts,
buttons and links into the app; nothing else is passed in. Limits respected: a Home view
holds at most 100 blocks, so long lists are cut and the rest counted.
"""

from __future__ import annotations

from typing import Any

from adjud.slack_app.core import ROLE_ORDER, ROLE_WORDS, Directory, Status, Task, Workspace

SYNTHETIC = "Synthetic data only. No patient or clinical information is shown in Slack."
MAX_ROWS = 30  # per list on App Home, well inside the 100-block view limit


def esc(text: str) -> str:
    """Slack's own escaping for message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def open_button(url: str, label: str = "Open in the app", action_id: str = "open_app") -> dict:
    """A link button. Slack still sends a click to the app, which only acknowledges it."""
    return {"type": "button", "text": {"type": "plain_text", "text": label}, "url": url,
            "action_id": action_id}  # fmt: skip


def task_block(ws: Workspace, person: dict[str, Any], task: Task, i: int) -> dict[str, Any]:
    head = f"*{task.case_id}* · " if task.case_id else ""
    block = _text(f"{head}{esc(task.words)}")
    if task.in_slack == "import_queued":
        label = {"type": "plain_text", "text": "Import queued files"}
        block["accessory"] = {"type": "button", "style": "primary",
                              "action_id": "import_queued", "text": label}  # fmt: skip
    else:
        url = ws.link(person, task.page, task.case_id)
        block["accessory"] = open_button(url, action_id=f"open_app_task_{i}")
    return block


def home(ws: Workspace, person: dict[str, Any] | None, note: str | None = None) -> dict:
    """The App Home for whoever this Slack user is acting as."""
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Adjudication"}},
    ]
    switch = {"type": "button", "action_id": "switch_person",
              "text": {"type": "plain_text", "text": "Switch person"}}  # fmt: skip
    if person is None:
        blocks.append(_text("Choose whom you act as for the demo. Roles are simulated, as "
                            "in the app's sidebar."))  # fmt: skip
        blocks[-1]["accessory"] = {**switch, "text": {"type": "plain_text", "text": "Choose"}}
        blocks.append(_context(SYNTHETIC))
        return {"type": "home", "blocks": blocks}
    blocks.append(_text(f"Acting as *{esc(ws.people.describe(person))}*. Simulated for the "
                        "demo, not enforced."))  # fmt: skip
    blocks[-1]["accessory"] = switch
    if note:
        blocks.append(_context(esc(note)))
    blocks.append({"type": "divider"})

    tasks = ws.tasks(person)
    blocks.append(_text(f"*Your tasks* ({len(tasks)})" if tasks else
                        "*Your tasks*\nNothing is waiting for you right now."))  # fmt: skip
    for i, task in enumerate(tasks[:MAX_ROWS]):
        blocks.append(task_block(ws, person, task, i))
    if len(tasks) > MAX_ROWS:
        blocks.append(_context(f"And {len(tasks) - MAX_ROWS} more: see the app."))

    cases = ws.cases(person)
    blocks.append({"type": "divider"})
    if cases:
        lines = [f"*{s.case_id}* · {esc(s.state)} since {s.since}"
                 for s in (ws.status(person, cs.case_id) for cs in cases[:MAX_ROWS])]  # fmt: skip
        more = f"\nAnd {len(cases) - MAX_ROWS} more." if len(cases) > MAX_ROWS else ""
        blocks.append(_text(f"*Your cases* ({len(cases)})\n" + "\n".join(lines) + more))
    else:
        blocks.append(_text("*Your cases*\nNone yet."))
    blocks.append({"type": "actions", "elements": [
        open_button(ws.link(person, "record"), "Open the app", "open_app_home")]})  # fmt: skip
    blocks.append(_context(SYNTHETIC))
    return {"type": "home", "blocks": blocks}


def tasks_reply(ws: Workspace, person: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = ws.tasks(person)
    if not tasks:
        return [_text("Nothing is waiting for you right now.")]
    blocks = [_text(f"You have {len(tasks)} task{'s' if len(tasks) > 1 else ''}:")]
    blocks += [task_block(ws, person, t, i) for i, t in enumerate(tasks[:MAX_ROWS])]
    return blocks


def cases_reply(ws: Workspace, person: dict[str, Any]) -> list[dict[str, Any]]:
    cases = ws.cases(person)
    if not cases:
        return [_text("You have no cases yet.")]
    lines = [f"*{s.case_id}* · {esc(s.state)} since {s.since}"
             for s in (ws.status(person, cs.case_id) for cs in cases[:MAX_ROWS])]  # fmt: skip
    return [_text(f"Your cases ({len(cases)}):\n" + "\n".join(lines))]


def status_reply(ws: Workspace, person: dict[str, Any], s: Status) -> list[dict[str, Any]]:
    block = _text(f"*{s.case_id}* at {esc(s.site)}: {esc(s.state)} since {s.since}.\n"
                  f"{esc(s.next_step)}")  # fmt: skip
    block["accessory"] = open_button(ws.link(person, "record", s.case_id), "Open the case record")
    return [block]


HELP = (
    "*`/adj` commands* (only you see the replies)\n"
    "• `/adj tasks`: what is waiting for you\n"
    "• `/adj cases`: your cases and where each is\n"
    "• `/adj status 0001`: one case, and your next step\n"
    "• `/adj import`: import the queued AE files (data manager)\n"
    "• `/adj act-as anna`: choose whom you act as (demo); `/adj act-as` opens a list\n"
    "Signing and anything clinical happen in the app, through *Open in the app*."
)


def act_as_modal(people: Directory, current: str | None) -> dict[str, Any]:
    """A form to choose the demo person, grouped by role."""
    groups = []
    for role in ROLE_ORDER:
        members = [u for u in people.users if u["role"] == role]
        if members:
            groups.append(
                {
                    "label": {"type": "plain_text", "text": ROLE_WORDS[role]},
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": people.describe(u)[:75]},
                            "value": u["id"],
                        }
                        for u in members
                    ],  # fmt: skip
                }
            )
    select: dict[str, Any] = {"type": "static_select", "action_id": "person",
                              "option_groups": groups}  # fmt: skip
    chosen = people.by_id.get(current or "")
    if chosen:
        select["initial_option"] = {
            "text": {"type": "plain_text", "text": people.describe(chosen)[:75]},
            "value": chosen["id"],
        }
    return {
        "type": "modal",
        "callback_id": "act_as",
        "title": {"type": "plain_text", "text": "Act as"},
        "submit": {"type": "plain_text", "text": "Act as"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "who",
                "label": {"type": "plain_text", "text": "Person"},
                "element": select,
            },  # fmt: skip
            _context(
                "Demo only: roles are simulated, as in the app's sidebar. Every Slack "
                "action records your Slack user and whom you acted as."
            ),  # fmt: skip
        ],
    }


def notice(note: dict[str, Any], who: str, url: str | None) -> tuple[str, list[dict]]:
    """A notice as a DM: the same content as the e-mail (study, case, task, for whom) and
    an *Open in the app* button. Returns (fallback text, blocks)."""
    case = note.get("case_id") or "-"
    task = note["subject"][:1].lower() + note["subject"][1:]
    text = f"[{note['study']}] Case {case}: {task}"
    blocks: list[dict[str, Any]] = [_text(f"*{esc(text)}*\nFor: {esc(who)}")]
    if url:
        blocks.append({"type": "actions", "elements": [open_button(url, action_id="open_app_dm")]})
    blocks.append(_context(SYNTHETIC))
    return text, blocks
