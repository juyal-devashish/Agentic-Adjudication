# Slack test plan (P-55, ADR 0003, ADR 0004)

How we show that the Slack app works as the place where work starts, with signing and Clinical Content kept in the app. Synthetic data only. E-mail (Mailpit) and every app screen stay on throughout: Slack is judged against them, never instead of them. Set-up steps are in the run sheet's *Slack app* section.

| Stage | What it proves | Needs Slack? | Who | Time |
|---|---|---|---|---|
| 1. Automated tests | The rules hold in code: no Clinical Content in any payload, Acting As, import, DMs, failures recorded, nothing blocks | No | Anyone, every commit | 1 min |
| 2. Smoke test | The app connects, Home, `/adj` and DMs look right, links open the right page as the right person | Yes | Presenter | 20 min |
| 3. Failure drills | Real failures are recorded and retried, and never hold up e-mail, cases or the app | Yes | Presenter | 30 min |
| 4. Privacy checks | No Clinical Content, addresses or tokens anywhere they shouldn't be | Yes | Presenter | 10 min |
| 5. Assistant-retirement runs (after Friday) | ADR 0003's bar: 3 full demo runs driven from Slack, parity clean | Yes | Presenter | 3 × ~30 min |

Repeat stages 2–4 after any change to `adjud/slack_app/`, `adjud/delivery.py` or the runner.

## 1. Automated tests

`uv run pytest tests/test_slack_app.py tests/test_slack.py tests/test_delivery.py tests/test_mailer.py`. A fake Slack client stands in for Slack; an autouse fixture clears `NOTIFY_CHANNELS`, `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`, so no test can reach Slack even if `.env` is set.

| Covered | Test |
|---|---|
| **No payload carries Clinical Content**: every Home, reply and DM, for every person, with a case through the Quality Check into adjudication, checked against each case's AE term, dates, subject and AE IDs, seriousness, outcome, severity, relatedness and codes, document names, AI Suggestion text, query text and narrative lines | `test_no_payload_carries_clinical_content` |
| Nothing works before choosing whom to act as | `test_nothing_works_before_choosing_whom_to_act_as` |
| `/adj act-as <name>` and the form; the actions log names the Slack user and the person | `test_act_as_by_name_publishes_that_persons_home`, `test_the_act_as_form_sets_the_person_or_says_what_is_missing` |
| Import from `/adj` and from the Home button; counts and case IDs only; the case event is as from the app | `test_the_data_manager_imports_from_slack_and_the_log_names_the_cases`, `test_the_import_button_on_app_home_does_the_same` |
| Only the data manager imports | `test_only_the_data_manager_may_import` |
| A site sees only its cases; a task opens the right page as the right person | `test_a_site_sees_its_own_cases_and_tasks_open_the_app` |
| Link clicks, Home opening and refused forms logged; typed command text never logged | `test_link_clicks_and_home_opening_are_handled`, `test_every_interaction_is_logged_without_the_typed_text` |
| Hidden files are not an import task; a failed import is answered and logged | `test_hidden_files_in_the_queue_are_not_an_import_task`, `test_a_failed_import_is_answered_and_logged` |
| The Slack actions log is verified; a reset clears it but keeps Acting As | `test_the_slack_actions_log_is_verified_and_survives_a_reset_without_acting_as` |
| DM content and *Open in the app*; one DM per Slack account; a retry never DMs someone twice; DMs wait for Acting As; the last account to choose is the presenter | `tests/test_slack.py` |
| Refused, rate-limited, timed-out and odd failures: recorded, retried at most 3 times, e-mail unaffected | `tests/test_slack.py` |
| The bot token is never written down | `test_the_token_and_odd_errors_are_never_written_down` |
| Parity report, runner switches, a crash never reaches e-mail | `tests/test_slack.py` |

## 2. Smoke test

From a fresh `reset_demo --preload`, with Mailpit, the runner, the UI and `python -m adjud.slack_app` running:

- [ ] The Slack app logs that it connected (Socket Mode); no public URL or tunnel is used.
- [ ] Home before choosing anyone says to choose; `/adj tasks` says the same, visible only to you.
- [ ] `/adj act-as rahul` → Home: *Acting as Rahul Mehta (Data manager)*, *1 AE file waiting to be imported*, **Import queued files**.
- [ ] **Import queued files** → "Imported 1 file: 4 records; 3 new cases (DEMO01-0001, …); 1 not a new case". No AE or subject IDs.
- [ ] Within a tick: a DM per site notice in the app's Messages tab, each with *For: name (Site user, hospital)* and **Open in the app**; the same e-mails in Mailpit.
- [ ] `/adj act-as priya` → Home lists only site 101's cases; **Open in the app** opens *Documents* as Priya Nair on that case.
- [ ] After the site signs and QC runs, `/adj act-as anna` → *Review the Quality Check and sign*; the button opens *Quality check*. Nothing about the documents, the AI Suggestion or the narrative appears in Slack.
- [ ] `/adj status 0001`, `/adj cases`, `/adj help` answer privately; `/adj status 0002` as Priya says *Which case?*.
- [ ] Case record → *Notifications*: *Slack* column *Sent*; `data/slack/actions.jsonl` has one entry per interaction; `uv run python -m tools.verify_log` is all OK.
- [ ] Whether `/adj` works inside the app's own DM (research left this open): note the answer here.

## 3. Failure drills

From a fresh reset; after each, check the Case record, `data/outbox/delivery.jsonl`, the Slack actions log and Mailpit.

| Drill | How | Expected |
|---|---|---|
| Slack app stopped | Stop `python -m adjud.slack_app`, click a Home button, type `/adj tasks` | Slack shows that the app didn't respond; DMs still arrive (the runner sends them); e-mail and the app carry on |
| Wrong bot token | Change `SLACK_BOT_TOKEN`, restart the runner, import from the app | Each notice: *Slack message failed: Slack invalid_auth*, three tries over three ticks; e-mail *Sent*; cases move on; parity **NOT CLEAN** |
| Slack unreachable | Wi-Fi off for two ticks, then on | First tick: one ~10 s wait, the rest marked failed without trying; after Wi-Fi returns, the next tick sends them |
| Given up | Wi-Fi off for 3+ ticks | After 3 tries a notice is no longer tried; it stays in the outbox; parity lists it |
| Nobody acting yet | Reset, delete `data/slack/acting_as.json`, import from the app | DMs wait (no try is used up) and go out on the first tick after `/adj act-as`; e-mail as usual |
| Slack switched off | Empty `NOTIFY_CHANNELS`, restart the runner | No DMs, e-mail exactly as before |
| Not the data manager | `/adj act-as anna`, `/adj import` | Refused with a reason; logged as refused; no case created |

**Pass:** every row behaves as expected, and in no drill is an e-mail late, missing or doubled, or a case stuck.

## 4. Privacy checks

After stage 2 with at least one case through the Quality Check:

- [ ] Read every DM, Home view and reply: no AE term, event date, subject or AE ID, query text, AI text, narrative, answer, document name, or e-mail address.
- [ ] `grep -rI "xoxb-\|xapp-" data/ ; echo $?` prints `1`.
- [ ] The runner's and the Slack app's logs contain no token, including after the *wrong bot token* drill.
- [ ] `git status` shows `.env` untracked; `git log -p | grep -E "xoxb-|xapp-"` finds nothing.
- [ ] No file was uploaded to Slack (the app has no `files:write` scope).

## 5. Assistant-retirement runs (after Friday)

ADR 0003: the Assistant page may be retired only when each of its abilities has a Slack equivalent covered by tests, 3 consecutive full demo runs (all four flows) are driven from Slack without opening the Assistant, and `uv run python -m tools.notify_parity` is CLEAN after each; then a new decision removes it. Record each run:

| Run | Date | Notices | DMs sent | Failed attempts | E-mailed | Parity |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |

Each run's report is also kept in `data/metrics/notify_parity.jsonl` (a demo reset keeps it).
