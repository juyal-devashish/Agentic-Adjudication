# Slack runs alongside email until it meets the reliability bar

**Status:** superseded by [ADR 0003](0003-slack-is-where-work-starts-the-app-is-where-it-is-signed.md). The Delivery Attempt records and the parity report are kept.

Slack is added as an extra notification Channel, opt-in through `NOTIFY_CHANNELS`, next to the outbox and e-mail, not instead of them: with the setting unset, behaviour is exactly as before. E-mail is proven in every demo so far and Slack is not, and a missed notice means a task nobody picks up. A Slack message carries the same content as an e-mail (study, case ID, recipient, task and link; never clinical content), and a failed Slack send is a recorded Delivery Attempt that never blocks the workflow or the e-mail. Slack may be proposed as a default only after 5 consecutive full demo runs (all four flows) with 0 failed Slack attempts, each with a clean `tools.notify_parity` report; meeting that bar opens a new decision, it does not switch anything by itself. Pharma users live on Teams, so Slack is a stepping stone: Channels share one interface, so Teams can be added later without touching the workflow. Slack is the project's one cloud-service exception, allowed only with synthetic data, a separate demo workspace, and the webhook URL kept in the environment.

## Consequences

- The parity check sees only what Slack confirmed, not what reached the channel: an incoming webhook cannot read messages back. Checking the channel itself needs a bot token with read access.
- One demo channel holds every recipient's notices. Blinding holds only because the presenter is its sole member; real use needs per-recipient routing (channels or direct messages) first.
