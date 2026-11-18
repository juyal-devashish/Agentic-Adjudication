# Demo run sheet: Flows 1 to 4

Synthetic data only. This shows the mechanics; it is not validated, and roles are simulated (`/adj act-as` in Slack, or the sidebar in the app).

## Before

1. In `.env`, set `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` and `ADJUD_AI_MODE=live`. Use `replay` to show the recorded Opus answers without calling the API.
2. Start the app: `docker compose --profile mail up -d --build`. The UI is on :8501 and Mailpit, for e-mails, on :8025.
3. Reset the demo data: `docker compose run --rm runner python -m tools.reset_demo --preload`. This loads the short demo: four AE records, three of them cases. Add `--all` for the whole test kit (22 records, 11 cases).

## The demo records

| Record | Case | What it shows |
|---|---|---|
| AE-001 Myocardial infarction, site 101 | DEMO01-0001 | The main path: the AI Suggestion finds everything matches, suggested *pass*, one signature with the narrative |
| AE-005 Myocardial infarction (fatal), site 102 | DEMO01-0002 | A poor scan the AI is unsure about: nothing pre-selected, Dr. Weber decides |
| AE-006 Unstable angina, site 101 | DEMO01-0003 | No discharge summary or ECG: more documents needed, a query is suggested |
| AE-008 Myocardial infarction, not serious, site 101 | none | Not eligible: the rule it fails is explained |

## Script (about 15 minutes)

1. **Slack, roles and the app (P-56, ADR 0003).** Slack is where each person's work starts; the app is where it is read and signed. In Slack, `/adj act-as <name>` chooses whom you act as, App Home lists that person's Tasks, and each notice arrives as a DM. Every step that needs a signature or Clinical Content has **Open in the app**, which opens the right page on the right case as that person. In the app, each role lands on its own page with a short *Your tasks* list on top: the data manager *AE import*, the site *Documents*, the Safety Specialist *Quality check*, and everyone the read-only *Case record*.
   - Slack never shows Clinical Content and never asks for a password (ADR 0004): case IDs, status, tasks and buttons only. Say this plainly; it is the point. The AI decides nothing either: it prepares the Quality Check, and people sign (ADR 0001).
   - In Slack: `/adj tasks`, `/adj cases`, `/adj status 0001`, `/adj import`, `/adj pull`, `/adj help`. Replies are visible only to you.
   - As *Priya Nair* (Larkfield General Hospital, site 101), only her hospital's cases are visible, in Slack and in the app. The people and hospitals are fictional; Tech Observer staff are Rahul Mehta (data manager) and Dr. Anna Weber (Safety Specialist). Without Slack, every step works from the app and the e-mail links.
   - **E-mails.** Each e-mail the runner sends pops up at the bottom right within a few seconds, for every role, about cases that role may see. Keep Mailpit (:8025) open beside the app to show the same message arriving; its link opens the case.
2. **AE import.** In Slack, `/adj act-as rahul` (Rahul Mehta, data manager):
   - App Home shows *1 AE file waiting to be imported*. Click **Import queued files**: 3 new cases and 1 record that is not a case. Do it within 5 minutes of starting the runner; after that the runner imports the file itself. In the app, the same import is on *AE import*.
   - Home says *1 record is not a case*; **Open AE import** shows, on the AE-008 row, the rule it fails (it is not serious). Slack doesn't say why: the reason is Clinical Content.
   - `/adj pull` (or **Pull AE records now** on *AE import*) is a stand-in that reads the mock EDC; no live EDC is connected.
3. **Upload.** Case DEMO01-0001's site gets an e-mail (show it in Mailpit). `/adj act-as priya` (site 101): the DM *documents requested* arrives, and App Home lists her two cases with *Upload the documents*. Click **Open in the app** on DEMO01-0001: *Documents* opens on that case. Then either:
   - on *Documents*, add the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/`. Each is tagged automatically from its title (discharge summary, ECG report, laboratory results); a wrong tag can be changed under *Review*; or
   - zip them into the case's folder `data/uploads/DEMO01-0001/`. The runner picks the ZIP up within a minute.
4. **Redaction and review.** As *Priya Nair* (site 101), on *Documents*:
   - Review the redacted pages.
   - Tick a wrongly removed item and give a reason to undo it.
   - Drag a box over anything missed.
   - Sign with the password `demo`.
5. **Flow 2: one Quality Check stop (P-39).** After the site's signature, code and the AI prepare everything before the Safety Specialist looks. Allow about a minute with live AI.
   - Code runs the six checks and builds the dossier. The AI writes its **AI Suggestion**: which documents are needed (the checklist's, plus advice on what would help) and a field-by-field match with the EDC, each with its reasoning and the quotes code verified. The AI also drafts the narrative.
   - `/adj act-as anna`: a DM says the quality check of DEMO01-0001 needs her review, and App Home's task shows *suggested: pass*, one word worked out by code (`/adj status 0001` says the same). The AI Suggestion itself stays in the app. Click **Open in the app**. One page shows the checks, the AI Suggestion, the **suggested outcome** (*pass*, worked out by code), the documents, the dossier and the narrative draft.
   - Say it plainly: this is not adjudication, and the AI decides nothing. It supports her review (ADR 0001). The outcome field shows *Not sure* where the documents say "discharged home" but not "recovered"; that doesn't hold back a pass, because the event itself matches.
   - Edit the narrative, for example add a sentence, and sign once: **Sign the pass and the narrative** (password `demo`). The case becomes ready for adjudication.
   - Show *Case record*: two signatures in the audit trail (site, and the Quality Check), *Narrative: what the Safety Specialist changed*, the notifications, and the AI calls with their cost.
6. **Exceptions.**
   - Optional, after a reset: upload a scenario from `data/demo_uploads/flow2/` into DEMO01-0001 instead of the kit documents, and sign. `missing_document`, `wrong_patient` and `unreadable` arrive at Dr. Weber with a query proposed and pre-filled; a check that failed in code can't be passed, so she sends the query. The site answers by uploading again and signing.
   - `different_event`: the AI Suggestion finds the onset date doesn't match (the documents describe an episode months earlier), quoting the documents, and code suggests a query. Dr. Weber decides; to pass anyway she must give a reason.
   - AE-005's and AE-006's letters have no recognisable title, so they show *Not recognised yet*; leave them for the quality check's agent. Tagging them by hand would skip the agent and change these scenes.
   - **Low confidence gets no suggestion.** As *Tom Hollis* (site 102), upload `data/demo_uploads/documents/DEMO01-102-0004_AE-005/` into DEMO01-0002 and sign. The AI is not sure about the poor-quality scan, or about the event term, so nothing is pre-selected and the Not sure findings say why. Dr. Weber decides.
   - **More documents needed.** As *Priya Nair* (site 101), upload `data/demo_uploads/documents/DEMO01-101-0005_AE-006/` into DEMO01-0003 and sign. The AI types the untitled letter as a clinic letter, so the discharge summary and ECG are missing; the AI Suggestion also says lab results would help (advice only). The query is suggested and pre-filled: choose **Send a query to the site** and sign. The narrative draft is dropped; a new one follows the site's answer.
   - With AI off, the page says there is no AI Suggestion and the narrative starts from the template; it can only be signed once the template's [bracketed] instruction is replaced.
7. **Flows 3 and 4: adjudication and outcome** (P-41 to P-53). No AI takes part: code assigns, compares and exports; people adjudicate and sign.
   - New people (password `demo`): *Sofia Marques* (CEC Coordinator) and three adjudicators, *Dr. Daniel Okafor* (the committee's Chair), *Dr. Elin Lindqvist* and *Dr. Marco Reyes*. Pick the role *Adjudicator* or *CEC Coordinator* in the sidebar.
   - Within a runner tick of the Quality Check signature, two adjudicators are assigned by round-robin and e-mailed. Each sees only their own cases; `/adj act-as okafor` (and the other assigned adjudicator) shows *Your independent review* with **Open in the app**.
   - As each adjudicator, fill the form on *Adjudication* and sign. Neither sees the other's answers. If they agree, the outcome is derived, exported to `data/exports/edc/`, acknowledged by the mock EDC, and the case closes. The Case record's *Adjudication and outcome* shows each final answer and who signed it.
   - **Disagreement without filling three forms:** `uv run python -m tools.make_flow3_fixtures --apply disputed --case DEMO01-0002` signs the open reviews; run it again for Adjudicator 3 after the runner's comparison. One field is still open, so as *Sofia Marques* record the meeting on *Committee*, then as *Dr. Okafor* sign the consensus.
   - Show blinding: as *Priya Nair* the case record says only that adjudication is independent; no names or answers.
   - Optional: as *Sofia Marques*, on *Assignments*, reassign an open review with a reason, or re-adjudicate a closed case. The old outcome shows as superseded and the new export is a correction.
8. **Not built:** a live EDC connection, real logins, DICOM redaction, due dates and reminders for adjudicators, and anything for real patient data.

Cost on Opus 5.5 is about $0.50 per full demo run: each Quality Check round asks for the AI Suggestion and a narrative draft. If live AI fails, switch to `replay` and restart. Replay only has answers for the demo's own documents.

## Slack app (P-55, ADR 0003, ADR 0004)

Slack is where work starts; the app is where it is read and signed. Slack shows case IDs, statuses, tasks and buttons, never Clinical Content, and no signature is taken there. E-mail and every app screen carry on as before. Synthetic data only. The test plan is [slack-test-plan.md](slack-test-plan.md).

1. **Set up once** (a separate free demo workspace, never the company's; you its only member).
   - At api.slack.com/apps: **Create New App → From a manifest**, pick the demo workspace, paste [slack-app-manifest.yaml](slack-app-manifest.yaml), create.
   - **Basic Information → App-Level Tokens → Generate**, scope `connections:write`: that is `SLACK_APP_TOKEN`.
   - **Install App → Install to Workspace**, then copy the **Bot User OAuth Token**: that is `SLACK_BOT_TOKEN`.
   - In `.env`: `NOTIFY_CHANNELS=outbox,smtp,slack`, `SLACK_BOT_TOKEN=…`, `SLACK_APP_TOKEN=…`. Never commit them or paste them into a chat.
2. **Start.** `uv run python -m tools.reset_demo --preload`, then three processes with `--env-file .env`: the runner, the UI, and `uv run --env-file .env python -m adjud.slack_app`. Run one Slack app process only.
3. **Show it.**
   - In Slack, type `/adj act-as rahul` (or `/adj act-as` for a list). Open the app's **Home** tab: *Acting as Rahul Mehta (Data manager)*, one task: *1 AE file waiting to be imported*. Click **Import queued files**. Three cases are created; Home shows the summary.
   - The sites' notices arrive as DMs in the app's **Messages** tab, each saying who it is for, with **Open in the app**. E-mail still arrives in Mailpit.
   - `/adj act-as priya`: Home lists DEMO01-0001's task, *Upload the documents*. **Open in the app** opens the Documents page as Priya Nair. Upload and sign there, as before.
   - `/adj act-as anna` after the site signs: *Review the Quality Check and sign* opens the Quality check page. Say it plainly: the decision and the signature are in the app, because Slack must not ask for passwords and must not hold Clinical Content.
   - `/adj status 0001` answers with the status and the next step, visible only to you.
   - The Case record's *Notifications* shows e-mail and Slack status per notice.
4. **If Slack misbehaves on stage,** carry on from the e-mail link or the app; nothing in the workflow waits for Slack.

Acting As is simulated, as the app's sidebar is. Every Slack action is in `data/slack/actions.jsonl` (hash-chained; `tools.verify_log` checks it) with your Slack user and whom you acted as. A demo reset clears that log but keeps whom you act as, so DMs keep reaching you.
