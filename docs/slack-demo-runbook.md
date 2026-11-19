# Slack demo runbook (P-57, P-58)

This runbook takes one case, DEMO01-0001, from a new AE record to its assignment to two adjudicators (the start of Flow 3). Each person does their work by talking to the **Agent**: one Claude agent that acts as that person, with that person's tools only. In the demo each character has a Slack channel, and one Slack account works in all of them (section 3). Signatures stay in the app, which the Agent opens with an *Open in the app* button. Every notice also goes out by e-mail. The demo takes about 10 minutes.

Use synthetic data only. Slack may now show anything without PHI ([ADR 0005](adr/0005-phi-never-leaves-the-app-slack-mirrors-teams.md)): the AE term, event dates, the subject ID, Checks, the AI Suggestion, the narrative and query text. Names, dates of birth, record numbers, addresses and the like never go to Slack: every reply the Agent writes is checked by the same detectors de-identification uses, and a reply with a finding is held back with *Open in the app*. Slack here stands in for Microsoft Teams; real data waits for Teams or a Slack plan with a BAA. This is the user's decision for the synthetic demo, not an approval from compliance or IT, so say so if asked.

## 1. Set up once (about 30 minutes)

You need:

- **Docker Desktop.** It brings Python, Tesseract and the language model, so nothing else needs installing.
- **A clone of the repository.** Use branch `main`.
- **A Slack workspace for the demo.** Use a free one, never the company's.
- **An Anthropic API key** for the Agent (optional: without it the Agent still answers, see below).

**Create your own Slack app.** Each presenter has their own app and tokens, so no secrets change hands.

1. Go to <https://api.slack.com/apps>, then choose **Create New App → From a manifest**. Pick the demo workspace, paste [slack-app-manifest.yaml](slack-app-manifest.yaml) and click **Create**. It turns on Slack's agent experience (the Messages tab you can write in, with a *My tasks* suggested prompt) and the events and scopes the Agent needs.
2. Go to **Basic Information → App-Level Tokens → Generate**. Give it any name and the scope `connections:write`. Copy the token, which starts `xapp-`.
3. Go to **Install App → Install to Workspace**. Copy the **Bot User OAuth Token**, which starts `xoxb-`.

An app created from an older manifest (including the presenter's, before the character channels) needs the current manifest pasted under **App Manifest**, then **Install App → Reinstall to Workspace**, because its scopes and events changed: the channels need `channels:read`, `channels:manage`, `channels:join`, `channels:history` and the event `message.channels`. The tokens stay the same.

**Create `.env`** in the repository folder by copying `.env.example`, then fill in these lines. Never commit this file and never paste it into a chat.

```
NOTIFY_CHANNELS=outbox,smtp,slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_FROM=adjudication-demo@example.org
ADJUD_AI_MODE=replay
ADJUD_AGENT_MODE=live
ANTHROPIC_API_KEY=...
```

- `ADJUD_AI_MODE=replay` keeps the Quality Check agents (document types, AI Suggestion, narrative) on answers recorded earlier, so Flow 2 is the same every run and makes no API calls.
- `ADJUD_AGENT_MODE=live` lets the Agent talk through Claude, with the key in `ANTHROPIC_API_KEY`. Each turn is logged and costed in `data/ai/usage.jsonl`.
- With `ADJUD_AGENT_MODE=off` (the default), or `live` without a key, the Agent's answers are written by code: the person's tasks with their buttons, so the demo never goes silent. `replay` answers only turns recorded earlier, so use it only with a recording.

Leave `ADJUD_DATA_DIR` empty.

**Build** the containers once. This takes about 10 minutes:

```
docker compose --profile slack --profile mail build
```

## 2. Before each demo (start no more than 3 minutes before presenting)

```
docker compose --profile slack --profile mail down
docker compose run --rm runner python -m tools.reset_demo --preload
docker compose --profile slack --profile mail up -d
```

- The reset wipes the demo data in `./data` and puts the AE file in the import queue. `data/config` is kept.
- You start each demo with a fresh reset. A single case is never deleted on its own, because its audit trail is hash-chained.
- The runner imports the queued file by itself 5 minutes after it starts, so do step 1 of the script before then.

Then open these:

- **The app:** <http://localhost:8501>
- **E-mail (Mailpit):** <http://localhost:8025>
- **Slack:** your demo workspace, with the five character channels starred (section 3).

Every demo person signs with the password `demo`.

**Without Docker (macOS or Linux, with `uv` and Tesseract installed):**

1. Run `scripts/slack-demo.sh start`. It resets the demo in its own folder, `data/demo`, so `./data` is never touched, and starts the runner, the UI and the Slack app. It takes the Agent's mode from `ADJUD_AGENT_MODE` in `.env`; if that is empty, the Agent runs `live` when `.env` has an `ANTHROPIC_API_KEY`, else `off`. It prints the mode it chose.
2. Start Mailpit separately, and use `SMTP_HOST=localhost` in `.env`.
3. Run `scripts/slack-demo.sh stop` afterwards.

## 3. The script

**One channel per character, one Slack account.** When the Slack app starts it makes sure these public channels exist and joins them (`slack_character_channels` in `data/config/DEMO01/study.yaml`):

| Channel | Character |
|---|---|
| `#data-manager` | Rahul Mehta, data manager |
| `#site-user` | Priya Nair, site 101 |
| `#safety-specialist` | Dr Anna Weber, Safety Specialist |
| `#adjudicator-1` | Dr Daniel Okafor, Adjudicator 1 of DEMO01-0001 |
| `#adjudicator-2` | Dr Elin Lindqvist, Adjudicator 2 of DEMO01-0001 |

Once, after the first start with the current manifest: in Slack, **join** the five channels (they are public: *Browse channels*, or type `/join #site-user` and so on) and star them. That survives resets. Then do section 2 once more (reset and start): on the very first start the runner may send the first notice before the Slack app has made the channels, and it would go as a DM. If the workspace does not let apps create channels, create the five by hand, then restart: the app finds them by name and joins them. Then the demo is: click a character's channel and work there. Each notice for that character arrives in their channel as a new message, and the Agent answers every message written there as that character, whoever you are signed in as and whatever *Switch person* says. Reply in the thread of a notice: a reply in a notice's thread is about that notice's case. Its *Open in the app* buttons open the app as that character.

This is a demo simulation, like Acting As: anyone in a channel takes that person's part, and nothing is enforced. Say so. In real use each person has their own Slack account and talks to the Agent in its **Messages** tab. People without a channel (Sofia Marques, Dr Reyes) still get DMs, which reach the account that chose them, or the last account to choose anyone, with *Switch person*. If a channel could not be created or joined (see `docker compose --profile slack logs slack`), that character's notices go as DMs as before and the Messages tab with *Switch person* still works.

Adjudicators 1 and 2 are given round robin, so `#adjudicator-1` is Dr Okafor's only when DEMO01-0001 is the first case assigned after a reset, as in this script.

| # | In Slack | In the app | Talking point |
|---|---|---|---|
| 1 | In **#data-manager**, the notice *AE records are waiting to be imported* is waiting. Type **import the AE file** there. (If the Agent does not import, click **Import queued files** on that notice.) | (none) | Work is a conversation. Import needs no signature, so the Agent does it as Rahul: three records become cases. Ask **why is one record not a case?**: the Agent reads the intake log's reasons. |
| 2 | In **#site-user**: *Case DEMO01-0001: documents requested for an adjudication case. For: Priya Nair (Site user, Larkfield General Hospital). Next: Upload the documents.* The same notice arrives in Mailpit. | (none) | The notice starts a conversation about its case. The e-mail is still sent, and still says only the case, the task and the link. |
| 3 | In **#site-user**, **reply in the thread of the *documents requested* notice** with the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/` (with the script: `data/demo/demo_uploads/…`) attached, e.g. *here are the documents*. (Fallback: **Open in the app** and upload on the Documents page.) | (none) | The site uploads from the conversation. These are originals: in the demo they then also stay in Slack. The app tries to delete them from Slack and records the result; a live test answered `cant_delete_file`, so say they stay there. Synthetic data only. |
| 4 | About 10 to 15 s later, in **#site-user**: *redacted documents are ready for your review. Next: Review the redacted pages and sign.* Ask in its thread **what was removed?**: counts per file. Then **Open in the app**. | Every file shows *Verified*. Look at a redacted page, then enter the password `demo` and click **Sign and submit**. | Identifiers are removed destructively and checked again from scratch. Anything still found blocks signing. The signature happens only in the app: Slack must not ask for passwords. |
| 5 | *submission received* in **#site-user**; *documents received, ready for QC* and, about 10 s later, *a quality check needs your review … suggested: pass* in **#safety-specialist**. | (none) | The handoff to Flow 2 contains only the redacted files and their hashes. |
| 6 | In **#safety-specialist**, in the thread of *a quality check needs your review*, ask **what does the QC say for 0001?**: the six checks, the AI Suggestion and the suggested outcome. Then **change the last sentence of the narrative to "…"**: the Agent edits the draft (unsigned). Then **Open in the app**. | On the Quality check page the edited narrative is pre-filled. Check it, enter `demo` and click **Sign the pass and the narrative**. | The AI prepares and a person decides ([ADR 0001](adr/0001-ai-is-decision-support-at-quality-check.md)). The Agent drafts; one signature in the app covers the pass and the narrative. |
| 7 | Within 10 to 20 s: *case ready for adjudication* in **#safety-specialist**, then *an adjudication review is assigned to you* in **#adjudicator-1** and **#adjudicator-2**. In either, ask **status of 0001**. | (none) | The case is assigned to two independent adjudicators, never from the case's site. **The demo stops here.** Flow 3 continues in the app. |

If the Agent's AI is off (or has no key), each answer is the person's tasks with their buttons: the demo still runs, with the buttons in the Agent's reply in the character's channel and **Open in the app** for each step. The Case record page, under *Notifications*, shows each notice's e-mail and Slack status.

## 3a. Let someone use it from their own computer (public links)

`scripts/share-demo.sh start` opens two public links (Cloudflare quick tunnels, no account; `brew install cloudflared` once): the app and the Mailpit inbox. It then resets and starts the demo so that every *Open in the app* button and e-mail link points at the public app link, and keeps the Mac from idle sleep. Send the two links and invite the person to the Slack workspace; they join the five role channels. Anyone with a link can open it: synthetic data only, never real documents.

The links work only while this Mac is on, awake (lid open, or on power with a display), online and running the demo. They change every time the script starts, so send the new ones. Everyone shares one demo: a reset resets it for all. `scripts/share-demo.sh stop` closes the links and stops the demo.

## 4. If something goes wrong

| Symptom | What to do |
|---|---|
| No notices arrive | Check that the Slack app started and joined the channels (`docker compose --profile slack logs slack`: *character channels ready*) and that you joined them too. DMs for people without a channel wait until someone is acting as a person (`/adj act-as`). Check that `.env` has `NOTIFY_CHANNELS` with `slack` and the `xoxb-` token, then run `docker compose logs runner`. |
| The Agent answers only with tasks and buttons | Its AI is off: check `ADJUD_AGENT_MODE=live` and `ANTHROPIC_API_KEY` in `.env`, then restart the Slack app. Or carry on with the buttons. |
| *That answer may contain identifiers; open it in the app.* | The PHI gate held the reply. Click **Open in the app**, or ask again in other words. The hold is in `data/slack/conversations.jsonl`. |
| The Agent seems to stay *working* | Send another message; if it persists, restart the Slack app. |
| `/adj` says the app did not respond | The Slack app container is not connected. Run `docker compose --profile slack logs slack`; you should see *Bolt app is running*. Check the `xapp-` token, and make sure only one Slack app process is running against this Slack app. |
| The cases appeared before Rahul imported | The runner imported the queued file after 5 minutes. Carry on from step 2, or reset and start again (section 2). |
| A file stays in *Removing identifiers* | Wait 20 s and refresh. If it is still stuck, run `docker compose logs runner`. |
| Slack goes down during the demo | Carry on from the e-mail link in Mailpit, or from the app. Nothing in the workflow waits for Slack. |
| You need to start over | Repeat section 2. |

To check the audit trail after a run, use `docker compose run --rm runner python -m tools.verify_log` (every log should verify, the conversation log included) and `docker compose run --rm runner python -m tools.notify_parity` (it should end with *CLEAN*).

## 5. Out of scope for this demo

- Signing in Slack, ever.
- Flows 3 and 4 through the Agent: adjudicators and the CEC Coordinator get tasks and status only.
- Teams, which needs IT approval.
- Real data of any kind.

These are recorded in plan P-57 and P-58.
