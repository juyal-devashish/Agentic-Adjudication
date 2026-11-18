# Slack test plan (P-55, P-58, ADR 0003, ADR 0005)

How we show that the Slack app works as the place where work is done, in conversation with the Agent, with signatures kept in the app and no PHI in Slack. Synthetic data only. E-mail (Mailpit) and every app screen stay on throughout: Slack is judged against them, never instead of them. Set-up steps are in [slack-demo-runbook.md](slack-demo-runbook.md) and the run sheet's *Slack app and the Agent* section.

| Stage | What it proves | Needs Slack? | Who | Time |
|---|---|---|---|---|
| 1. Automated tests | The rules hold in code: no PHI in any payload, the PHI gate, role-scoped tools, Acting As, import, DMs, the conversation log, failures recorded, nothing blocks | No | Anyone, every commit | 2 min |
| 2. Smoke test | The app connects, the Agent answers once per message as the right person, `/adj` and DMs look right, links open the right page as the right person | Yes | Presenter | 20 min |
| 3. Failure drills | Real failures are recorded and retried, and never hold up e-mail, cases or the app | Yes | Presenter | 30 min |
| 4. Privacy checks | No PHI, originals other than the site's own attachments, addresses or tokens anywhere they shouldn't be | Yes | Presenter | 10 min |
| 5. Agent demo runs (before Friday) | The Flows 1–2 demo runs from Slack with the Agent live, and again with it off | Yes | Presenter | 2 × ~15 min |

Repeat stages 2–4 after any change to `adjud/slack_app/`, `adjud/ai/gateway.py`, `adjud/delivery.py` or the runner.

## 1. Automated tests

`uv run pytest tests/test_slack_app.py tests/test_slack.py tests/test_slack_agent.py tests/test_slack_agent_tools.py tests/test_ai_conversation.py tests/test_slack_demo.py tests/test_delivery.py tests/test_mailer.py`. A fake Slack client stands in for Slack and the Agent runs in replay mode with outputs written in the test's own folder; an autouse fixture clears `NOTIFY_CHANNELS`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and `ADJUD_AGENT_MODE`, so no test can reach Slack or the Claude API even if `.env` is set.

| Covered | Test |
|---|---|
| **No Slack payload carries PHI**: every Home, reply and DM, for every person, checked against the kit's seeded identifiers | `tests/test_slack_app.py` (ADR 0004's *no Clinical Content* test is replaced under P-58) |
| The PHI gate holds every seeded identifier of the kit, passes ordinary answers and the app's own people and sites | `test_the_gate_holds_every_seeded_identifier_of_the_kit`, `test_the_gate_passes_ordinary_answers_and_the_apps_people` |
| A held reply is replaced and logged without the text found; the gate still runs without seeded identities | `test_a_reply_with_identifiers_is_held_and_logged_without_them`, `test_without_seeded_identities_the_gate_still_runs_its_detectors` |
| Each role has only its own tools; a site sees only its cases; adjudicators only their assigned ones | `test_each_role_has_only_its_own_tools`, `test_a_site_sees_only_its_own_cases_and_another_sites_case_is_refused`, `test_adjudicators_see_only_assigned_cases`, `test_a_site_asking_about_another_sites_case_through_the_agent_is_refused` |
| Each person's message gets exactly one reply in its thread; a thread goes on with what was said; a reply to a notice is about its case | `test_each_persons_message_gets_exactly_one_reply_in_its_thread`, `test_a_reply_in_a_thread_goes_on_in_that_thread_with_what_was_said`, `test_a_reply_to_a_notice_is_about_its_case` |
| With the Agent's AI off, or an empty message, code answers with the person's tasks; the status goes back to *active* even on failure | `test_with_the_agents_ai_off_code_answers_with_the_persons_tasks`, `test_a_message_with_nothing_in_it_is_answered_with_the_tasks`, `test_the_status_goes_back_to_active_when_the_agent_fails` |
| The conversation log is verified and cleared by a reset | `test_the_conversation_log_is_verified_and_cleared_by_a_reset` |
| Gateway conversations: off, live without a key, the tool loop, records with and without a case, replay, budget cap | `tests/test_ai_conversation.py` |
| Nothing works before choosing whom to act as | `test_nothing_works_before_choosing_whom_to_act_as` |
| `/adj act-as <name>` and the form; the actions log names the Slack user and the person | `test_act_as_by_name_publishes_that_persons_home`, `test_the_act_as_form_sets_the_person_or_says_what_is_missing` |
| Import from `/adj` and from the Home button; the case event is as from the app | `test_the_data_manager_imports_from_slack_and_the_log_names_the_cases`, `test_the_import_button_on_app_home_does_the_same` |
| Only the data manager imports | `test_only_the_data_manager_may_import` |
| A task opens the right page as the right person | `test_a_site_sees_its_own_cases_and_tasks_open_the_app` |
| Link clicks, Home opening and refused forms logged; typed text never logged | `test_link_clicks_and_home_opening_are_handled`, `test_every_interaction_is_logged_without_the_typed_text` |
| Hidden files are not an import task; a failed import is answered and logged | `test_hidden_files_in_the_queue_are_not_an_import_task`, `test_a_failed_import_is_answered_and_logged` |
| The Slack actions log is verified; a reset clears it but keeps Acting As | `test_the_slack_actions_log_is_verified_and_survives_a_reset_without_acting_as` |
| DM content and *Open in the app*; one DM per Slack account; a retry never DMs someone twice; DMs wait for Acting As | `tests/test_slack.py` |
| Refused, rate-limited, timed-out and odd failures: recorded, retried at most 3 times, e-mail unaffected | `tests/test_slack.py` |
| The bot token is never written down | `test_the_token_and_odd_errors_are_never_written_down` |
| The P-57 demo from the import to the assignment | `tests/test_slack_demo.py` |

## 2. Smoke test

From a fresh `reset_demo --preload`, with Mailpit, the runner, the UI and `python -m adjud.slack_app` running, `ADJUD_AI_MODE=replay`, `ADJUD_AGENT_MODE=live` and a key in `.env`:

- [ ] The Slack app logs that it connected (Socket Mode); no public URL or tunnel is used.
- [ ] Opening the **Messages** tab before choosing anyone: the Agent asks whom you act as.
- [ ] `/adj act-as rahul`, then the Messages tab: the Agent greets you once as Rahul Mehta (Data manager). The *My tasks* suggested prompt answers with *1 AE file waiting to be imported*.
- [ ] Type **import the AE file**: one reply, three new cases (or **Import queued files** on Home). The status shows *working* while it runs and is back to normal after the reply.
- [ ] Edit a message you sent, or delete one: the Agent does not reply again.
- [ ] Within a tick: a DM per site notice, each with *For: name (Site user, hospital)* and **Open in the app**; the same e-mails in Mailpit.
- [ ] `/adj act-as priya`: reply in the *documents requested* thread with the three files attached; they reach DEMO01-0001's *Documents*. The app records whether Slack let it delete them (a live test answered `cant_delete_file`).
- [ ] As Priya, ask about DEMO01-0002 (site 102): the Agent says it cannot find that case.
- [ ] After the site signs and QC runs, `/adj act-as anna`: **what does the QC say for 0001?** answers with the checks and the AI Suggestion; **change the last sentence of the narrative to "…"** updates the draft; **Open in the app** shows it pre-filled on *Quality check*. Nothing is signed in Slack.
- [ ] `/adj status 0001`, `/adj cases`, `/adj help` answer privately.
- [ ] Case record → *Notifications*: *Slack* column *Sent*; `data/slack/actions.jsonl` and `data/slack/conversations.jsonl` have one entry per interaction and per turn; `uv run python -m tools.verify_log` is all OK.

## 3. Failure drills

From a fresh reset; after each, check the Case record, `data/outbox/delivery.jsonl`, the Slack logs and Mailpit.

| Drill | How | Expected |
|---|---|---|
| Slack app stopped | Stop `python -m adjud.slack_app`, write to the Agent, type `/adj tasks` | No reply; Slack shows that the app didn't respond to `/adj`; DMs still arrive (the runner sends them); e-mail and the app carry on |
| Agent without AI | `ADJUD_AGENT_MODE=off`, or `live` with no key; restart the Slack app | Every message is answered with the person's tasks and buttons, written by code |
| Wrong API key | A wrong `ANTHROPIC_API_KEY`, `ADJUD_AGENT_MODE=live` | The turn fails, is recorded, and the Agent answers with the tasks; its status does not stay *working* |
| Wrong bot token | Change `SLACK_BOT_TOKEN`, restart the runner, import from the app | Each notice: *Slack message failed: Slack invalid_auth*, three tries over three ticks; e-mail *Sent*; cases move on; parity **NOT CLEAN** |
| Slack unreachable | Wi-Fi off for two ticks, then on | First tick: one ~10 s wait, the rest marked failed without trying; after Wi-Fi returns, the next tick sends them |
| Given up | Wi-Fi off for 3+ ticks | After 3 tries a notice is no longer tried; it stays in the outbox; parity lists it |
| Nobody acting yet | Reset, delete `data/slack/acting_as.json`, import from the app | DMs wait (no try is used up) and go out on the first tick after `/adj act-as`; e-mail as usual |
| Slack switched off | Empty `NOTIFY_CHANNELS`, restart the runner | No DMs, e-mail exactly as before |
| Not the data manager | `/adj act-as anna`, then ask the Agent to import, and `/adj import` | Refused with a reason; no case created |

**Pass:** every row behaves as expected, and in no drill is an e-mail late, missing or doubled, or a case stuck.

## 4. Privacy checks

After stage 2 with at least one case through the Quality Check:

- [ ] Read every DM, Home view and Agent reply: no patient name, date of birth, record or phone number, address or other identifier the study's `deid_policy.yaml` removes. The AE term, event dates, subject ID, checks, AI Suggestion, narrative and query text may appear (ADR 0005).
- [ ] Ask the Agent for something that would quote an identifier (e.g. *what is the patient's name in 0001?*): the reply is held with *Open in the app*, and the conversation log records the hold by category only.
- [ ] No file from `documents/original/` and no unverified file was posted to Slack by the app. The only originals in Slack are the ones the site attached itself.
- [ ] E-mails in Mailpit still carry only the case ID, the task and the link.
- [ ] `grep -rI "xoxb-\|xapp-\|sk-ant-" data/ ; echo $?` prints `1`.
- [ ] The runner's and the Slack app's logs contain no token or API key, including after the *wrong bot token* and *wrong API key* drills.
- [ ] `git status` shows `.env` untracked; `git log -p | grep -E "xoxb-|xapp-|sk-ant-"` finds nothing.

## 5. Agent demo runs

The hidden Assistant is deleted: P-58 replaced it with the Agent, so ADR 0003's retirement bar (3 runs driven from Slack) no longer applies. Instead, before Friday, run the Flows 1–2 demo of the runbook twice from a fresh `reset_demo --preload`, once with `ADJUD_AGENT_MODE=live` and once with it `off`, starting every step from Slack and opening the app only through a Slack button. Each run passes when DEMO01-0001 reaches its two adjudicators, `uv run python -m tools.notify_parity` prints CLEAN and `uv run python -m tools.verify_log` is all OK.

| Run | Date | Agent mode | Turns | Held | DMs sent | Failed attempts | Parity |
|---|---|---|---|---|---|---|---|
| 1 | | live | | | | | |
| 2 | | off | | | | | |

Each run's parity report is also kept in `data/metrics/notify_parity.jsonl` (a demo reset keeps it).
