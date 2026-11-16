# Demo run sheet: Flows 1 and 2 with AI

Synthetic data only. This shows the mechanics; it is not validated, and roles are simulated (switch them in the sidebar).

## Before

1. In `.env`, set `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` and `ADJUD_AI_MODE=live`. Use `replay` to show the recorded Opus answers without calling the API.
2. Start the app: `docker compose --profile mail up -d --build`. The UI is on :8501 and Mailpit, for e-mails, on :8025.
3. Reset the demo data: `docker compose run --rm runner python -m tools.reset_demo --preload`. This loads the short demo: four AE records, three of them cases. Add `--all` for the whole test kit (22 records, 11 cases).

## The demo records

| Record | Case | What it shows |
|---|---|---|
| AE-001 Myocardial infarction, site 101 | DEMO01-0001 | The main path: proposal *pass*, AI narrative, two sign-offs |
| AE-005 Myocardial infarction (fatal), site 102 | DEMO01-0002 | A poor scan the agent is unsure about: no proposal, Dr. Weber decides |
| AE-006 Unstable angina, site 101 | DEMO01-0003 | No discharge summary or ECG: a query is proposed |
| AE-008 Myocardial infarction, not serious, site 101 | none | Not eligible: the rule it fails is explained |

## Script (about 15 minutes)

1. **Roles.** The roles are data manager, site user (101, 102 and 104) and Safety Specialist. Each sees only its own pages: the data manager *AE import*, the site *Documents*, the Safety Specialist *Quality check*, and everyone the read-only *Case record*. Switching role opens that role's page. As *Priya Nair* (Larkfield General Hospital, site 101), only her hospital's cases and records are visible. The people and hospitals are fictional; Tech Observer staff are Rahul Mehta (data manager) and Dr. Anna Weber (Safety Specialist).
   - **E-mails.** Each e-mail the runner sends pops up at the bottom right within a few seconds, for every role, about cases that role may see. Keep Mailpit (:8025) open beside the app to show the same message arriving; its link opens the case.
2. **AE import.** As data manager, on *AE import*:
   - **Import queued files** gives 3 cases and 1 record that isn't eligible. Click it within 5 minutes of the reset; after that the runner imports the file itself.
   - Click the AE-008 row in the results table: below it, each rule's result shows why it is not a case (it is not serious).
   - **Pull AE records now** is a stand-in that reads the mock EDC; no live EDC is connected.
3. **Upload.** Case DEMO01-0001's site gets an e-mail (show it in Mailpit). Then either:
   - as *Priya Nair* (site 101), on *Documents*, add the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/`. Each is tagged automatically from its title (discharge summary, ECG report, laboratory results); a wrong tag can be changed under *Review*; or
   - zip them into the case's folder `data/uploads/DEMO01-0001/`. The runner picks the ZIP up within a minute.
4. **Redaction and review.** As *Priya Nair* (site 101), on *Documents*:
   - Review the redacted pages.
   - Tick a wrongly removed item and give a reason to undo it.
   - Drag a box over anything missed.
   - Sign with the password `demo`.
5. **Flow 2.** After the site's signature, code and the agents prepare everything; the Safety Specialist makes the two medical calls (P-37):
   - Code runs the quality checks, and agents answer what code can't tell, each answer checked by code.
   - As *Dr. Anna Weber*, follow **Review the quality check**. The page proposes *pass*, with the checks and the documents as submitted. Sign the decision (password `demo`).
   - The dossier is built and the agent drafts the narrative, every quote checked by code. Allow about 30 seconds with live AI.
   - Follow **Complete the narrative**: check the draft against the dossier, edit if needed, and sign. The case becomes ready for adjudication.
   - Show *Case record*: three signatures in the audit trail (site, QC, narrative), the notifications, and the AI calls with their cost.
6. **Exceptions.**
   - Optional, after a reset: upload a scenario from `data/demo_uploads/flow2/` into DEMO01-0001 instead of the kit documents, and sign. `missing_document`, `wrong_patient` and `unreadable` arrive at Dr. Weber with a query proposed and pre-filled; a check that failed in code can't be passed, so she sends the query. The site answers by uploading again and signing.
   - `different_event`: the agent spots that the documents describe an earlier episode and proposes a query, quoting the documents. Dr. Weber decides whether to send it.
   - AE-005's and AE-006's letters have no recognisable title, so they show *Not recognised yet*; leave them for the quality check's agent. Tagging them by hand would skip the agent and change these scenes.
   - **Low confidence gets no proposal.** As *Tom Hollis* (site 102), upload `data/demo_uploads/documents/DEMO01-102-0004_AE-005/` into DEMO01-0002 and sign. The agent is not sure about the poor-quality scan, so Dr. Weber gets the case without a proposal. She passes it, and the agent drafts the narrative for her to sign.
   - **Missing documents.** As *Priya Nair* (site 101), upload `data/demo_uploads/documents/DEMO01-101-0005_AE-006/` into DEMO01-0003 and sign. The agent types the untitled letter as a clinic letter, so the checks find no discharge summary or ECG and Dr. Weber gets a query proposed and pre-filled. Because the finding rests on an AI answer, she may overrule it; a failure found by code alone can only end in a query.
   - If Dr. Weber writes a narrative herself, it can only be signed once the template's [bracketed] instruction is replaced.
7. **Not built:** a live EDC connection, real logins, DICOM redaction, Flows 3 and 4, and anything for real patient data.

Cost on Opus 5.5 is about $0.30 per full demo run. If live AI fails, switch to `replay` and restart. Replay only has answers for the demo's own documents.
