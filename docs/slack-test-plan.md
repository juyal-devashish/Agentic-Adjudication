# Slack test plan (P-54, ADR 0002)

How we show that Slack works next to e-mail, and when it has met the reliability bar. Synthetic data only. E-mail (Mailpit) stays on throughout: Slack is judged against it, never instead of it.

| Stage | What it proves | Needs Slack? | Who | Time |
|---|---|---|---|---|
| 1. Automated tests | The rules hold in code: content, retries, failures recorded, nothing blocks | No | Anyone, every commit | 1 min |
| 2. Set-up and smoke test | Real posts arrive, look right, links open the right page | Yes | Presenter | 20 min |
| 3. Failure drills | Real failures are recorded, retried, and never hold up e-mail or cases | Yes | Presenter | 30 min |
| 4. Privacy checks | No clinical content, addresses or webhook URL anywhere they shouldn't be | Yes | Presenter | 10 min |
| 5. Reliability runs | The bar: 5 clean full runs in a row | Yes | Presenter | 5 × ~30 min |

Stages 2–4 run once; repeat them only after a change to `adjud/slack.py`, `adjud/delivery.py` or the runner. Stage 5 starts only when 2–4 pass.

## 1. Automated tests (already in the suite)

`uv run pytest tests/test_slack.py tests/test_delivery.py tests/test_mailer.py`. They use a fake Slack; an autouse fixture clears `NOTIFY_CHANNELS` and `SLACK_WEBHOOK_URL`, so they can't post even if `.env` is set.

| Covered | Test |
|---|---|
| Message: study, case, task, "For: name (role)", link, previews off, no addresses, no clinical words; a mailbox gets no link | `test_the_message_names_the_case_task_and_person_and_nothing_clinical` |
| Only notices queued after switching on are posted, each once | `test_only_notifications_queued_after_switching_on_are_posted_once` |
| Refused post: recorded with its reason, at most 3 tries, e-mail still sent, notice kept | `test_a_refused_post_is_recorded_retried_three_times_and_email_still_goes` |
| Timeout: the rest of the tick isn't tried; the next tick sends | `test_when_slack_does_not_answer_the_rest_of_the_tick_is_not_tried` |
| Unexpected error: recorded by type only | `test_an_unexpected_error_is_a_recorded_attempt_named_by_type_only` |
| The webhook URL is never written to the log or events | `test_the_webhook_url_is_never_written_down` |
| `NOTIFY_CHANNELS` unset = as before; listed but not configured = off | `test_the_runner_switches_channels_from_notify_channels` |
| A crash in Slack never reaches e-mail or the runner | `test_a_slack_crash_never_reaches_the_email_or_the_runner` |
| A restart keeps the switch-on time | `test_a_restart_keeps_the_time_slack_was_switched_on` |
| Case record shows Slack status and failure reason | `test_the_case_record_shows_slack_attempts` |
| Parity report: clean only when every notice was posted once | `test_parity_is_clean_only_when_every_notification_was_posted_once` |
| Slack attempts never count as e-mail ones; old log lines count as e-mail | `tests/test_delivery.py` |

**Pass:** all green. Not covered here, and the reason for stages 2–5: the real Slack service, the real network, and what a person sees.

## 2. Set-up and smoke test

1. Create a **separate free Slack workspace** for the demo (never the company's) and a channel `#adjudication-demo`. You are its only member.
2. At api.slack.com/apps: *Create New App → From scratch* in that workspace → *Incoming Webhooks* on → *Add New Webhook* → pick the channel → copy the URL.
3. In `.env` (never committed): `NOTIFY_CHANNELS=outbox,smtp,slack`, `SLACK_WEBHOOK_URL=<URL>`, plus the Mailpit settings (`SMTP_HOST=localhost`, `SMTP_PORT=1025`).
4. `docker compose --profile mail up -d mailpit`, then `uv run python -m tools.reset_demo --preload`, then start the runner and the UI with `--env-file .env`.
5. As the data manager, import the queued file. Within a tick or two, the three sites' *documents requested* notices appear in Slack **and** in Mailpit.

**Pass:**
- [ ] One Slack message per notice, matching Mailpit one to one (same case, same recipient).
- [ ] Each message reads `[DEMO01] Case DEMO01-000n: documents requested…`, `For: <name> (Site 10x)`, an *Open the case* link, the synthetic-data line. No link preview card.
- [ ] Clicking the link (on the presenter's machine) opens the site's *Documents* page for that case.
- [ ] Runner log says `Slack on…` once and `Slack: n sent, 0 failed`.
- [ ] Case record → *Notifications* has a *Slack* column showing *Sent*.

## 3. Failure drills

Do each from a fresh `reset_demo`; check the Case record, `data/outbox/delivery.jsonl` and Mailpit after each.

| Drill | How | Expected |
|---|---|---|
| Wrong webhook | Change the last part of the URL in `.env`, restart the runner, import | Each notice: *Slack message failed: HTTP 404 no_service* (or 403), three tries over three ticks, then left; e-mail *Sent*; cases move on; parity **NOT CLEAN** |
| Slack unreachable | Turn Wi-Fi off for two ticks, then on | First tick: one ~10 s wait, the rest marked failed without trying (*timeout* / *cannot connect*); after Wi-Fi returns, the next tick posts them; each notice ends *Sent* |
| Given up | Keep Wi-Fi off for 3+ ticks | After 3 failed tries a notice is no longer tried; it stays in the outbox; parity lists it as *posted 0 times* |
| Restart mid-run | Stop the runner after a QC signature, queue more work, start it again | Nothing queued in between is skipped; parity stays clean |
| Switched off | Empty `NOTIFY_CHANNELS`, restart, run one case | No Slack posts, e-mail exactly as before, no *Slack* column for new cases' notices |
| Listed, not configured | `NOTIFY_CHANNELS=outbox,smtp,slack` but empty `SLACK_WEBHOOK_URL` | Runner logs *slack … not set: off* once; e-mail as before |
| Webhook revoked | Remove the webhook in the Slack app settings | Same as *wrong webhook*, with Slack's reason |

**Pass:** every row behaves as expected, and in no drill is an e-mail late, missing or doubled, or a case stuck.

## 4. Privacy checks

After stage 2 with at least one full case through Flow 4:

- [ ] Read every Slack message: no AE term, diagnosis, date of event, outcome, document text, or e-mail address. `outcome_for_pv` says only that an outcome needs pharmacovigilance attention.
- [ ] `grep -rI "hooks.slack.com" data/ ; echo $?` prints `1` (the URL is nowhere under `data/`).
- [ ] The runner's log contains no webhook URL, including after the *wrong webhook* drill.
- [ ] `git status` shows `.env` untracked; `git log -p | grep hooks.slack.com` finds nothing.
- [ ] Blinding caveat noted: the channel shows who adjudicates which case. Only the presenter is a member.

## 5. Reliability runs (the bar)

One run:

1. `uv run python -m tools.reset_demo --preload`; restart the runner (so Slack's start time is fresh).
2. Follow `docs/demo-guide.md` through all four flows: an agreed case, a disputed case through Adjudicator 3 and the committee, one more-data request, one re-adjudication.
3. Wait one tick after the last action, then `uv run python -m tools.notify_parity`.
4. Write the result in the table below and in the plan's step 28.

| Run | Date | Notices | Slack posted | Failed attempts | E-mailed | Parity |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

**The bar (ADR 0002):** 5 runs **in a row**, each **CLEAN** with **0 failed attempts**. Any failure resets the count to zero; write down its cause. Each run's report is also kept in `data/metrics/notify_parity.jsonl` (a demo reset keeps it).

Also check per run which notice types appeared: `jq -r .template data/outbox/*.json | sort | uniq -c`. The run sheet doesn't trigger every one of the 21 types (e.g. *possible duplicate*, *source changed*, *upload rejected*); those are covered by stage 1 and need no extra run.

## Afterwards

- Passing the bar doesn't switch anything: it allows a new decision (an ADR replacing 0002) on making Slack a default. E-mail stays.
- Before any real use: per-recipient routing (a channel per site or role, or direct messages) so blinding holds, and a read-back check of the channel, which needs a bot token rather than a webhook.
- Teams later: the same stages, with a Teams Channel in place of Slack.
