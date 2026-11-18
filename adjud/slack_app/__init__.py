"""The Slack app (plan P-55, ADR 0003, ADR 0004): where work starts.

Slack shows each person their Tasks (App Home), a DM for each notice and an `/adj`
command for tasks, cases, status and import. Every signed step and every step that needs
Clinical Content is finished in the app through an *Open in the app* button; nothing here
signs, and nothing sent to Slack carries Clinical Content.

    core.py    Tasks, cases, status, Acting As and the Slack actions log; no Slack library
    blocks.py  what Slack shows (Block Kit), built only from core.py's clinical-free facts
    dm.py      the Slack Channel: one DM per notice, with *Open in the app*
    app.py     the Bolt app: /adj, App Home, buttons and the Acting As form
    agent.py   the Agent in the Messages tab (P-58): one Conversation per thread, as one person
    agent_tools.py  the Agent's tools, one toolbox per role, scoped to that person's cases
    phi_gate.py     the check for identifiers on everything the Agent posts (Q17)
    conversations.py  the Agent's hash-chained conversation log (Q18)
    __main__   `uv run python -m adjud.slack_app` (Socket Mode, no public address)

`slack_bolt` (and its `slack_sdk`) are used only in this package (AGENTS.md).
"""
