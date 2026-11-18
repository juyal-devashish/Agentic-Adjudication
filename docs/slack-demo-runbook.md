# Slack demo runbook (P-57)

This runbook takes one case, DEMO01-0001, from a new AE record to its assignment to two adjudicators (the start of Flow 3). Slack starts and guides each step. The app is where documents are uploaded and reviewed, the Quality Check is done and everything is signed. Every notice also goes out by e-mail. The demo takes about 10 minutes.

Use synthetic data only. Slack shows case IDs, statuses, people's names, the next step and *Open in the app* buttons. It never shows Clinical Content ([ADR 0004](adr/0004-clinical-content-never-leaves-the-app.md)), and nothing is signed in Slack. That "no PHI in Slack" is acceptable is the team's assumption from the planning meeting. It is not an approval from compliance or IT, so say so if asked.

## 1. Set up once (about 30 minutes)

You need:

- **Docker Desktop.** It brings Python, Tesseract and the language model, so nothing else needs installing.
- **A clone of the repository.** Use branch `main`.
- **A Slack workspace for the demo.** Use a free one, never the company's.

**Create your own Slack app.** Each presenter has their own app and tokens, so no secrets change hands.

1. Go to <https://api.slack.com/apps>, then choose **Create New App → From a manifest**. Pick the demo workspace, paste [slack-app-manifest.yaml](slack-app-manifest.yaml) and click **Create**.
2. Go to **Basic Information → App-Level Tokens → Generate**. Give it any name and the scope `connections:write`. Copy the token, which starts `xapp-`.
3. Go to **Install App → Install to Workspace**. Copy the **Bot User OAuth Token**, which starts `xoxb-`.

**Create `.env`** in the repository folder by copying `.env.example`, then fill in these lines. Never commit this file and never paste it into a chat.

```
NOTIFY_CHANNELS=outbox,smtp,slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_FROM=adjudication-demo@example.org
ADJUD_AI_MODE=replay
```

`ADJUD_AI_MODE=replay` uses AI answers that were recorded earlier. No Claude API key is needed and no API calls are made. Leave `ADJUD_DATA_DIR` and `ANTHROPIC_API_KEY` empty.

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
- **Slack:** your demo workspace. Find **Adjudication Demo** under *Apps*.

Every demo person signs with the password `demo`.

**Without Docker (macOS or Linux, with `uv` and Tesseract installed):**

1. Run `scripts/slack-demo.sh start`. It resets the demo in its own folder, `data/demo`, so `./data` is never touched, and starts the runner, the UI and the Slack app.
2. Start Mailpit separately, and use `SMTP_HOST=localhost` in `.env`.
3. Run `scripts/slack-demo.sh stop` afterwards.

## 3. The script

One Slack account plays every person. `/adj act-as <name>` switches between them, the same way the app's sidebar does. Acting As is simulated, not enforced: say so.

| # | In Slack | In the app | Talking point |
|---|---|---|---|
| 1 | `/adj act-as rahul`. On the **Home** tab, Rahul's task reads *1 AE file waiting to be imported*. Click **Import queued files**. | (none) | Work starts in Slack. Import is the one step done fully in Slack, because it needs no signature and shows no clinical detail. Three records become cases. One record is not a case, and the app shows why. |
| 2 | A DM: *Case DEMO01-0001: documents requested for an adjudication case. For: Priya Nair (Site user, Larkfield General Hospital). Next: Upload the documents.* The same notice arrives in Mailpit. | (none) | Slack tells the site what is needed and what to do next. The e-mail is still sent. |
| 3 | `/adj act-as priya`, then **Open in the app** on the DM. | On the Documents page for DEMO01-0001, upload the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/` (with the script: `data/demo/demo_uploads/…`). | Uploading happens in the app. Uploading through Slack is a later idea. |
| 4 | About 10 to 15 s later, a DM: *redacted documents are ready for your review. Next: Review the redacted pages and sign.* | Refresh the page. Every file shows *Verified*. Look at a redacted page, then enter the password `demo` and click **Sign and submit**. | Identifiers are removed destructively and then checked again from scratch. Anything still found blocks signing. The signature happens only in the app. |
| 5 | DMs: *submission received* (Priya; *Next: Nothing more for you on this case now.*), *documents received, ready for QC* and, about 10 s later, *a quality check needs your review. Next: Review the Quality Check and sign · suggested: pass* (Dr Weber). | (none) | The handoff to Flow 2 contains only the redacted files and their hashes. The suggested outcome is a single word worked out by code, and Slack shows nothing else about the case. |
| 6 | `/adj act-as anna`, then **Open in the app** on the DM. | On the Quality check page, show the six checks, the AI Suggestion, the dossier and the narrative draft. Edit a sentence, enter `demo` and click **Sign the pass and the narrative**. | The AI prepares and a person decides ([ADR 0001](adr/0001-ai-is-decision-support-at-quality-check.md)). One signature covers the pass and the narrative. |
| 7 | Within 10 to 20 s: *case ready for adjudication* (Dr Weber), then two DMs, *an adjudication review is assigned to you. Next: Your independent review (round 1)*, one for each adjudicator. Run `/adj status 0001`. | (none) | The case is assigned to two independent adjudicators, never from the case's site. **The demo stops here.** Flow 3 continues in the app. |

The Case record page, under *Notifications*, shows each notice's e-mail and Slack status.

## 4. If something goes wrong

| Symptom | What to do |
|---|---|
| No DMs arrive | Run `/adj act-as rahul` first, because DMs wait until someone is acting as a person. Check that `.env` has `NOTIFY_CHANNELS` with `slack` and the `xoxb-` token, then run `docker compose logs runner`. |
| `/adj` says the app did not respond | The Slack app container is not connected. Run `docker compose --profile slack logs slack`; you should see *Bolt app is running*. Check the `xapp-` token, and make sure only one Slack app process is running against this Slack app. |
| The cases appeared before you clicked Import | The runner imported the queued file after 5 minutes. Carry on from step 2, or reset and start again (section 2). |
| A file stays in *Removing identifiers* | Wait 20 s and refresh. If it is still stuck, run `docker compose logs runner`. |
| Slack goes down during the demo | Carry on from the e-mail link in Mailpit, or from the app. Nothing in the workflow waits for Slack. |
| You need to start over | Repeat section 2. |

To check the audit trail after a run, use `docker compose run --rm runner python -m tools.verify_log` (every log should verify) and `docker compose run --rm runner python -m tools.notify_parity` (it should end with *CLEAN*).

## 5. Out of scope for this demo

- Slack Workflows or a Slack AI agent. Showing case details or narratives in Slack would mean showing Clinical Content there.
- Uploading in Slack.
- Teams, which needs IT approval.
- Completing Flow 3.

These are recorded in plan P-57.
