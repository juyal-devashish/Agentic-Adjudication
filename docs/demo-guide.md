# Demo guide: Flows 1 and 2 with AI

How to prepare and present the end-to-end demo. AI agents do the work, code checks every answer, and people sign. Anyone on the team can run it without the developer.

Synthetic data only. The demo shows the mechanics. It is not validated and not Part 11 / Annex 11 compliant, and roles are simulated (anyone can switch in the sidebar).

Everything runs on the real backend: the Docker app with its runner, redaction pipeline and hash-chained case files. A chat or Claude artifact mock-up would look similar but would not process or redact anything.

## Before the demo

Do this on the machine that will host the demo, the day before and again an hour before.

1. **Key in the backend, not in a chat.** Put these in `.env` at the repository root:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_WORKSPACE_ID=wrkspc_...   # needed when the key is not scoped to a workspace
   ADJUD_AI_MODE=live
   ```
   `.env` is never committed. The model and the spending cap are in `data/config/DEMO01/study.yaml` (`ai.model`, `ai.budget_usd`).
2. **Start the app:**
   ```
   docker compose --profile mail up -d --build
   ```
   This starts the UI on port 8501, the runner, and Mailpit on port 8025 for e-mails.
3. **Check that AI works from inside the app:**
   ```
   docker compose run --rm runner python -m tools.ai_check
   ```
   It makes one tiny call, costing a fraction of a cent. `OK` means the key, the workspace and the model all work. `FAILED` prints the API's own message, for example a missing workspace ID or no credits. If it fails, contact Nirav for another key.
4. **Full rehearsal (optional, a few dollars):**
   ```
   uv run --env-file .env python -m tools.ai_eval --runs 1
   ```
   This runs every demo case through Flows 1 and 2 in a throwaway folder and reports each outcome against the expected one.
5. **Reset the demo data:**
   ```
   docker compose run --rm runner python -m tools.reset_demo --preload
   ```
   This wipes the cases, loads the mock EDC, queues `01_initial.csv` and puts the demo files in `data/demo_uploads/`.
6. **E-mails.**
   - Mailpit is ready to use: set `SMTP_HOST=mailpit` and `SMTP_PORT=1025` in `.env`, then open http://localhost:8025.
   - For Outlook, set `smtp.office365.com`, port 587 and an app password, if the tenant allows SMTP. Messages from the company's own server are less likely to be caught as spam.
   - `SMTP_REDIRECT_TO` sends every message to the presenter's inbox.
7. **Access for others.** The UI listens on all interfaces, so people on the same network can open `http://<host-ip>:8501`. Set `APP_BASE_URL=http://<host-ip>:8501` so links in e-mails point there. Access from outside that network needs hosting, which IT has to provide.

**If live AI fails on the day:** set `ADJUD_AI_MODE=replay` and restart. The agents then return their recorded answers, which are real answers recorded live and labelled as such. Every other step stays the same. With `off`, the agent steps become tasks for the Safety Specialist, and the workflow still completes.

## Script

Roles are chosen in the sidebar. Open *Case record* as Safety Specialist in a second tab to follow each case's audit trail.

### 1. Roles and site segregation (2 min)

- **Data manager:** imports AE records. This role replaces the kit's study admin (plan P-25).
- **Site user:** one per site (101, 102 and 104). Uploads documents, reviews the redaction and signs.
- **Safety Specialist:** steps in only on exceptions.

Switch to *Site 101 Coordinator*. The case list, the case overview on *Case record* and the import results show only site 101's subjects. Switch to site 102 to show its own, different list.

### 2. AE import (3 min)

As Data manager, on *AE import*:

- **Import queued files.** `01_initial.csv` gives 11 cases and 11 records that are not eligible.
- **Why?** Pick a record. Every rule is listed with its result, whether or not the record became a case. Each evaluation is kept in a hash-chained intake log.
- **Two ways in:** the data manager uploads the EDC export (CSV or XLSX), or presses **Pull AE records now**. The pull is a stand-in that reads the mock EDC; a live EDC connection is not integrated. Pulling twice changes nothing, because intake is idempotent.
- **Optional:** `03_updates.csv` from `data/demo_uploads/inbox_files/` shows a changed AE flagged under *Cases to check*. Nothing is withdrawn automatically.

### 3. Upload: case folder, notification, ZIP (3 min)

- Each new case gets an upload folder named after its case number: `data/uploads/DEMO01-0001/`.
- The site is notified by e-mail. Show it in Mailpit. It contains the case number, the task and a link, and no clinical content.
- Zip the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/` (discharge summary, ECG report, troponin results) and put the ZIP in that folder.
- The finished ZIP is the signal that the documents are ready. Within a minute the runner imports it and removes the identifiers.
- The *Documents* page has an upload button as well, for sites that prefer it.

### 4. Redaction and site review (5 min)

As *Site 101 Coordinator*, case DEMO01-0001, on *Documents*:

- **The files table:**
  - Status per file: verified, needs attention, or rejected with a reason.
  - The number of identifiers removed.
  - **This case?**, which checks that each document carries this subject's ID and no other subject's. A document uploaded to the wrong case shows up here before signing, and QC checks it again afterwards.
- **Review:**
  - Page through the redacted discharge summary, ECG report and spreadsheet.
  - Text is removed from the file, not just covered: the underlying text and pixels are gone and the metadata is stripped. The output is then re-read from scratch, and anything still found blocks signing.
- **Undo a wrong redaction:**
  - Tick an item in *Identifiers removed* and give a reason. The file is redacted again under a new version, and the old version stays.
- **Manual redaction:**
  - Drag a box over something the rules missed, then press **Black out the selected area**.
  - The text under the box is removed, and the box is kept for later runs.
- **Sign and submit** with password `demo`. The site's signature always stays human.

Then, on *Case record*:
- **Audit trail:** every step with who or what did it, plus a chain check.
- **Notifications:** each message at each step, with its delivery status.

### 5. Flow 2: AI does the work, code decides (5 min)

Nobody acts after the signature. Watch *Quality check* and *Case record* for DEMO01-0001:

1. **Quality checks** by code: the documents are readable, no identifiers are left, the required documents are present, it is the same patient, and the subject ID and onset date are found.
2. **Agents** answer what code cannot tell: the type of an untitled letter, and whether the documents describe this event.
   - Each answer passes a code gate before it can move the case: valid output, quotes found on the page, and confidence at or above 0.8.
   - The page shows the answer, the gate's verdict and the cost.
3. **Dossier:** built and hashed.
4. **Narrative:** the agent writes facts-only statements, each quoting a dossier page. Code finds every quote and checks for forbidden wording such as causality, allocation or adjudication language.
5. **Ready for adjudication:** the hashed package goes to Flow 3.

**Exceptions:** the scenarios in `data/demo_uploads/flow2/`, each uploaded into a fresh case.

| Scenario | What happens |
|---|---|
| `missing_document` | The site gets a query automatically |
| `wrong_patient` | The site gets a query automatically |
| `unreadable` | The site gets a query automatically |
| `different_event` | The agent spots that the documents describe another event, and the site gets a query |

Where an agent is not confident enough, or a gate fails, the Safety Specialist gets the case on *Quality check*. In the recorded evaluation, case DEMO01-0005's GP letter (site 102) was one of these.

### 6. Expectations (1 min)

- **Built and working:** Flows 1 and 2 end to end, with live AI agents behind code gates and people for signatures and exceptions.
- **Not built:**
  - A live EDC connection.
  - Real authentication and enforced roles.
  - DICOM redaction.
  - Flows 3 and 4.
  - Anything for real patient data, which would need IT and a validated, restricted setup.
- **Cost:** $0.02–0.06 per case on Opus 5.5, about $0.30 for the whole demo. See [plan step 22](plan-mvp.md).

## After the demo

- Set `ADJUD_AI_MODE=replay` or `off`, so nothing calls the API by accident.
- Revoke the demo key if it was temporary.
- Run `uv run python -m tools.verify_log` to confirm every case log is intact.
