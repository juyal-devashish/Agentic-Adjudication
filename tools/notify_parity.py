"""Compare the outbox with what Slack confirmed (plan P-54, ADR 0002's reliability bar).

    uv run python -m tools.notify_parity

Run it after a full demo run, once the runner has caught up. The window starts at the
first Notification Slack tried (Slack posts nothing queued before it was switched on).
Clean means, inside the window:

- every Notification has exactly one Slack `sent` record;
- no Slack attempt failed (the bar allows no retries either);
- if e-mail was on, as many Notifications were e-mailed as were posted to Slack;
- no Slack record refers to a Notification missing from the outbox.

It sees what Slack *confirmed* ("ok" to the webhook), not what reached the channel: an
incoming webhook cannot read messages back. Each run appends one line to
`data/metrics/notify_parity.jsonl`, so the five runs of the bar leave a record.
Exit code 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

from adjud.delivery import EMAIL, SLACK, DeliveryLog
from adjud.events import format_time, utc_now
from adjud.paths import DataPaths
from adjud.store import FileCaseStore


def parity(log: DeliveryLog) -> dict[str, Any]:
    notes = {n["id"]: n for n in log.notes()}
    lines = log.lines()
    slack = [d for d in lines if d["channel"] == SLACK]
    tried = [notes[d["id"]]["created_at"] for d in slack if d["id"] in notes]
    report: dict[str, Any] = {"at": format_time(utc_now()), "problems": []}
    if not slack:
        report["problems"].append("Slack has no attempts yet: is NOTIFY_CHANNELS set?")
        report["clean"] = False
        return report
    since = min(tried) if tried else ""
    window = {i for i, n in notes.items() if n["created_at"] >= since}
    sent = Counter(d["id"] for d in slack if d["status"] == "sent")
    failed = [d for d in slack if d["status"] == "failed"]
    email = [d for d in lines if d["channel"] == EMAIL and d["id"] in window]
    emailed = {d["id"] for d in email if d["status"] == "sent"}
    report.update(
        since=since,
        notifications=len(window),
        slack_sent=sum(1 for i in window if sent[i]),
        slack_failed_attempts=len(failed),
        email_on=bool(email),
        email_sent=len(emailed),
    )
    p = report["problems"]
    for i in sorted(window):
        if sent[i] != 1:
            n = notes[i]
            p.append(f"{n.get('case_id') or '-'} {n['template']}: posted {sent[i]} times")
    for d in failed:
        p.append(f"failed attempt for {d['id'][:8]}: {d.get('error', '?')}")
    if email and len(emailed) != report["slack_sent"]:
        p.append(f"{len(emailed)} e-mailed but {report['slack_sent']} posted to Slack")
    for d in slack:
        if d["id"] not in notes:
            p.append(f"Slack record {d['id'][:8]} has no Notification in the outbox")
    report["clean"] = not p
    return report


def main() -> int:
    paths = DataPaths.from_env()
    report = parity(DeliveryLog(FileCaseStore(paths=paths)))
    if "since" in report:
        print(f"Window from {report['since']}: {report['notifications']} notifications")
        print(f"  Slack: {report['slack_sent']} posted, "
              f"{report['slack_failed_attempts']} failed attempts")  # fmt: skip
        email = f"{report['email_sent']} sent" if report["email_on"] else "off"
        print(f"  E-mail: {email}")
    for problem in report["problems"]:
        print(f"  - {problem}")
    print("CLEAN" if report["clean"] else "NOT CLEAN")
    paths.metrics.mkdir(parents=True, exist_ok=True)
    with (paths.metrics / "notify_parity.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(report) + "\n")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
