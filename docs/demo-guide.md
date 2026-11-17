# Demo run sheet: Flows 1 to 4

Synthetic data only. This shows the mechanics; it is not validated, and roles are simulated (switch them in the sidebar).

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

1. **Roles and the Assistant (P-40).** The roles are data manager, site user (101, 102 and 104) and Safety Specialist. Everyone lands on the **Assistant**, a chat that greets them with what is waiting for them; drive the demo from there. Besides it, each role sees only its own pages: the data manager *AE import*, the site *Documents*, the Safety Specialist *Quality check*, and everyone the read-only *Case record*.
   - The Assistant answers from the case records, in code; where the AI wrote something (the AI Suggestion, the narrative draft) it says so. It never signs or decides: for every manual step it gives a button that opens the right page on the right case. Say this plainly; it is the point (ADR 0001).
   - Starter buttons, or type: `/tasks`, `/status 0001`, `/qc 0001`, `/why AE-008`, `/import`, `/help`, or plain questions such as *why is AE-008 not a case?*
   - Keep the Assistant open in a second browser tab as another role: what happens to that role's cases appears in its chat as it happens (documents received, identifiers removed, quality check ready, query sent, ready for adjudication). As *Priya Nair* (Larkfield General Hospital, site 101), only her hospital's cases and records are visible. The people and hospitals are fictional; Tech Observer staff are Rahul Mehta (data manager) and Dr. Anna Weber (Safety Specialist).
   - **E-mails.** Each e-mail the runner sends pops up at the bottom right within a few seconds, for every role, about cases that role may see. Keep Mailpit (:8025) open beside the app to show the same message arriving; its link opens the case.
2. **AE import.** As *Rahul Mehta* (data manager), in the Assistant:
   - Click **Import the AE records** (or type `/import`): 3 cases and 1 record that isn't eligible, listed in the chat. Do it within 5 minutes of the reset; after that the runner imports the file itself, and the Assistant reports the new cases instead.
   - Click **Why is AE-008 not a case?**: the rule it fails (it is not serious), in words. The same is on *AE import* by clicking the AE-008 row.
   - **Pull AE records now** is a stand-in that reads the mock EDC; no live EDC is connected.
3. **Upload.** Case DEMO01-0001's site gets an e-mail (show it in Mailpit). As *Priya Nair* (site 101), the Assistant lists her two cases and the documents each needs; click **Upload documents for DEMO01-0001**. Then either:
   - on *Documents*, add the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/`. Each is tagged automatically from its title (discharge summary, ECG report, laboratory results); a wrong tag can be changed under *Review*; or
   - zip them into the case's folder `data/uploads/DEMO01-0001/`. The runner picks the ZIP up within a minute.
4. **Redaction and review.** As *Priya Nair* (site 101), on *Documents*:
   - Review the redacted pages.
   - Tick a wrongly removed item and give a reason to undo it.
   - Drag a box over anything missed.
   - Sign with the password `demo`.
5. **Flow 2: one Quality Check stop (P-39).** After the site's signature, code and the AI prepare everything before the Safety Specialist looks. Allow about a minute with live AI.
   - Code runs the six checks and builds the dossier. The AI writes its **AI Suggestion**: which documents are needed (the checklist's, plus advice on what would help) and a field-by-field match with the EDC, each with its reasoning and the quotes code verified. The AI also drafts the narrative.
   - As *Dr. Anna Weber*, the Assistant says the quality check of DEMO01-0001 is ready and gives the suggested outcome; `/qc 0001` summarises the AI Suggestion in the chat. Click **Review and sign DEMO01-0001**. One page shows the checks, the AI Suggestion, the **suggested outcome** (*pass*, worked out by code), the documents, the dossier and the narrative draft.
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
   - Within a runner tick of the Quality Check signature, two adjudicators are assigned by round-robin and e-mailed. Each sees only their own cases; the Assistant offers **Adjudicate DEMO01-0001**.
   - As each adjudicator, fill the form on *Adjudication* and sign. Neither sees the other's answers. If they agree, the outcome is derived, exported to `data/exports/edc/`, acknowledged by the mock EDC, and the case closes. The Case record's *Adjudication and outcome* shows each final answer and who signed it.
   - **Disagreement without filling three forms:** `uv run python -m tools.make_flow3_fixtures --apply disputed --case DEMO01-0002` signs the open reviews; run it again for Adjudicator 3 after the runner's comparison. One field is still open, so as *Sofia Marques* record the meeting on *Committee*, then as *Dr. Okafor* sign the consensus.
   - Show blinding: as *Priya Nair* the case record says only that adjudication is independent; no names or answers.
   - Optional: as *Sofia Marques*, on *Assignments*, reassign an open review with a reason, or re-adjudicate a closed case. The old outcome shows as superseded and the new export is a correction.
8. **Not built:** a live EDC connection, real logins, DICOM redaction, due dates and reminders for adjudicators, and anything for real patient data.

Cost on Opus 5.5 is about $0.50 per full demo run: each Quality Check round asks for the AI Suggestion and a narrative draft. If live AI fails, switch to `replay` and restart. Replay only has answers for the demo's own documents.

## Trying Slack (P-54, ADR 0002)

The full test plan, with failure drills and the record table, is [slack-test-plan.md](slack-test-plan.md).

Slack runs alongside e-mail, never instead of it, until it meets the reliability bar. Synthetic data only.

1. **Set up once.**
   - Create a separate free Slack workspace for the demo, never the company's, with a channel such as `#adjudication-demo`. Keep yourself its only member: it shows every recipient's notices, including who adjudicates which case.
   - Create a Slack app *from scratch* in that workspace, turn on **Incoming Webhooks**, add one for the channel and copy its URL.
2. **Switch it on.** In `.env`, set `NOTIFY_CHANNELS=outbox,smtp,slack` and `SLACK_WEBHOOK_URL=<the URL>`. The URL is a secret: never commit it or paste it into chat. Restart the runner. Only notices queued from then on are posted; a later restart keeps that starting point, and a demo reset clears it.
3. **One reliability run.**
   - `tools.reset_demo --preload`, restart the runner, then follow this run sheet through all four flows: an agreed case, a disputed case through the committee, one more-data request and one re-adjudication.
   - When the runner has caught up, run `uv run python -m tools.notify_parity`. **CLEAN** means every notice was posted exactly once, with no failed attempt and as many e-mails as Slack posts.
   - Record the date and the report in the plan's step 28.
4. **The bar.** Five clean runs in a row with 0 failed Slack attempts. Meeting it only allows a new decision on making Slack a default; e-mail stays.

A failed post shows on the Case record as *Slack message failed* with its reason, and it is retried twice on the next ticks. E-mail and the case carry on regardless.
