# UI inventory: every user action, by screen

Input for planning the Slack workflow. Read from `ui/app.py`, `ui/common.py` and every `ui/views/*.py` on branch `feat/slack-workflow` (from `main` at `315a7dd`).

**Clinical content** means anything about the patient's event or care: AE term, onset and other event dates, seriousness, outcome, relatedness, medical history, medication, labs, document pages, identifiers found in them, the narrative, AI findings and quotes, and adjudication answers. Not clinical: case ID, subject ID, AE ID, site name, workflow status, people's names, task wording, and counts. Subject and AE IDs are pseudonymous codes, but they still shouldn't go anywhere outside the tool without a reason.

**Changes** means what the action writes. *Nothing* means it is read-only, or only changes what is on screen. *Signed* means password re-entry, recorded as an electronic signature in the case log.

## Who reaches which screen

| Role | Screens in the menu (first = landing) |
|---|---|
| Data manager | Assistant, AE import, Case record |
| Site user | Assistant, Documents, Case record |
| Safety Specialist | Assistant, Quality check, Case record |
| CEC Coordinator | Assistant, Assignments, Committee, Case record |
| Adjudicator | Assistant, Adjudication, Committee (Chair only), Case record |

A screen outside the role's list redirects to the role's first screen, but e-mail deep links still resolve. Several screens handle other roles defensively ("This page is for…"); those dead ends are not listed as actions.

## Shown on every screen

| Where | Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|---|
| Sidebar | All | Role, name, case picker (case ID and short status) | Pick a role, a name, a case | Nothing (session; URL keeps role, user, case) | No |
| Case header (every case screen) | All | Case ID, **AE term**, status and since when, **onset date**, subject, site, AE ID, flags, lifecycle rail, next-step sentence | Click the next-step link | Nothing (opens the page for the next step) | **Yes**: AE term, onset |
| Case list (no case chosen) | All | Cases this person may see: case, subject, AE, status, since, flags | Click a row | Nothing (opens that case) | No |
| E-mail pop-ups | All | "DEMO01-0001: e-mail sent to Priya Nair. Documents requested…" for visible cases; blinded for sites and other adjudicators | None | Nothing | No |

## Assistant (`views/assistant.py`), all roles

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| All | Greeting; chat history; live updates on their cases every 3 s | Read | Nothing | **Yes**: updates and tasks name the AE term and onset |
| All | Suggested replies (role's starters) | Click a suggestion, e.g. "What's waiting for me?" | Nothing (asks the matching command) | Depends on the answer |
| All | Text box | Type `/tasks` or plain words | Nothing | **Yes**: each task names "(AE term, onset …)" |
| All | Text box | Type `/status 0001` | Nothing | **Yes**: AE term, subject, site, onset |
| All | Text box | Type `/why AE-008` | Nothing | **Yes**: rule results quote AE values (e.g. seriousness) |
| All | Text box | Type `/qc 0001` | Nothing | **Yes**: AI Suggestion summary, field results |
| Data manager | Text box | Type `/import` | **Imports the queued AE files**: cases created, evaluations logged, sites notified | No (IDs and outcome words) |
| Non-data-manager | Text box | Type `/import` | Nothing (explains it's the data manager's job) | No |
| All | Text box | Type `/help` | Nothing | No |
| All | Purple action buttons in answers ("Review and sign DEMO01-0001", "Adjudicate…") | Click | Nothing (sets the case, opens that page) | No |

## AE import (`views/edc_input.py`), data manager

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| Data manager | "Pull from the EDC" | Click **Pull AE records now** | Imports from the mock EDC: snapshots, evaluations, new cases, notifications | No (summary counts) |
| Data manager | File uploader | Choose a CSV/XLSX, click **Import file** | Writes the file to the inbox, then imports it (same effects) | No |
| Data manager | Queue note: n files waiting | Click **Import queued files** | Imports the queued files (same effects) | No |
| Data manager | Import results: file picker, summary, error table (row, column, problem, detail) | Pick a file | Nothing | Possibly: error details can quote a cell value |
| Data manager | Outcomes table: subject, AE, result, case, flags | Click a row | Nothing (shows "Why") | **Yes**: rule-by-rule reasons quote AE values |
| Data manager | "Enter an AE by hand" form: subject, site, AE ID, **AE term, onset/end dates, serious, criterion, outcome, severity, relatedness**, MedDRA codes, last changed, reason | Fill in, click **Save AE** | Manual entry: same rules; a case if eligible; marked as entered by hand | **Yes**: typed AE details |
| Data manager | "Cases to check": flagged cases (source changed, possible duplicate of …) | Read | Nothing | No |

## Documents (`views/site_documents.py`), site user

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| Site | Query banner: "Query from the quality check / the adjudication: reasons. Text" | Read | Nothing | **Yes**: the query text can describe what's clinically missing |
| Site | Uploader (PDF, images, XLSX), while documents are awaited, during review, or a query is open | Choose files, click **Add to case** | Stores originals, starts identifier removal; waits up to 60 s | **Yes**: the documents |
| Site | Note about the case's ZIP upload folder | None (drops a ZIP outside the app) | Picked up by the runner within a minute | **Yes**: the documents |
| Site | While identifiers are being removed | Click **Remove identifiers now** | Runs de-identification at once | No |
| Site | Files table: file, type, status, identifiers removed, still found | Read | Nothing | No (counts) |
| Site | A rejected file's message | Click **Remove <file>** | Deletes the file from the case (recorded, with a reason) | No |
| Site | Review: file picker, document-type picker (with who set it) | Pick a file; pick a type | Type change: relabels the document (recorded) | No |
| Site | Redacted page preview "as it will be sent" | Drag a box, click **Black out the selected area on page n** | Adds a manual redaction; redacts again | **Yes**: redacted page images |
| Site | Spreadsheet preview | Read | Nothing | **Yes** |
| Site | "Still found after removal" list (masked text) | Read | Nothing | Masked identifiers |
| Site | Identifiers removed: masked text, kind, where | Tick **Keep**, type a reason, click **Keep ticked text** | Un-redacts those items, with the reason; redacts again | Masked identifiers |
| Site | Spreadsheet only: text box | Type a term, click **Black out this text** | Adds a term to black out everywhere; redacts again | Possibly (the typed term) |
| Site | Review pane | Click **Remove <file>** (before signing) | Deletes the file (recorded) | No |
| Site | Sign and submit: what signing confirms, n files | Type the password, click **Sign and submit** | **Signed** submission: the case goes to QC; the package for Flow 2 is built | No (the screen); the package holds the redacted documents |
| Site | Not ready: reasons each file is not verified | Read (button disabled) | Nothing | No |

## Quality check (`views/quality_check.py`), Safety Specialist

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| Safety Specialist | Round, checks table (check, result, details) | Read | Nothing | Possibly: details can name documents or dates |
| Safety Specialist | AI Suggestion: summary, required documents, "would also help", match with the EDC (field, EDC value, documents value, result, why, verified quotes) | Read | Nothing | **Yes** |
| Safety Specialist | Suggested outcome (pass / query / none) with reasons | Read | Nothing | Possibly (reasons) |
| Safety Specialist | Documents as submitted | Pick a document, page through it | Nothing | **Yes** |
| Safety Specialist | Earlier queries: when, by whom, reasons, text | Read | Nothing | **Yes**: query text |
| Safety Specialist | Dossier: version, page map | Click **Download the dossier (PDF)** | Nothing (a download) | **Yes** |
| Safety Specialist | Narrative draft with where it came from | Read | Nothing | **Yes** |
| Safety Specialist | Your decision: Pass or Send a query | Choose one | Nothing (switches the fields shown) | No |
| Safety Specialist | Pass: editable narrative; reason (required if a query was suggested) or note | Edit, type the password, click **Sign the pass and the narrative** | **Signed** pass plus narrative: dossier frozen, Flow 3 package, then reviews assigned | **Yes**: the narrative |
| Safety Specialist | Query: reasons (pre-filled), message (pre-filled if suggested) | Pick reasons, type the message and password, click **Sign the query** | **Signed** Site Query: the case reopens at the site; the narrative draft is dropped | **Yes**: the message |
| Safety Specialist | Older path (narrative review state) | Edit, type the password, click **Sign narrative** | **Signed** narrative | **Yes** |
| Other roles (by link) | Read-only checks, Suggestion, narrative | Read | Nothing | **Yes** |

## Assignments (`views/assignments.py`), CEC Coordinator

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| CEC Coordinator | No case: case, status, round, Adjudicator 1/2/3 with "(signed)" | Read | Nothing | No |
| CEC Coordinator | Case: this round's reviewers | Read | Nothing | No |
| CEC Coordinator | Reassign an open review: review, to (eligible, not already assigned), reason | Pick, type a reason, click **Reassign** | Moves the review; the old draft is not passed on; the new adjudicator is notified | No |
| CEC Coordinator | Closed case: Re-adjudicate | Type a reason and password, click **Sign and start re-adjudication** | **Signed**: round n+1 with the same adjudicators; the old outcome superseded; the next export is a correction | No |

## Adjudication (`views/adjudication.py`), adjudicator

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| Adjudicator | No case: only their assigned cases | Click a row | Nothing | No |
| Adjudicator | Round, review pass, dossier version | Click **Download the dossier (PDF)** | Nothing (a download) | **Yes** |
| Adjudicator | Signed narrative | Read | Nothing | **Yes** |
| Adjudicator 1 or 2 | The form: event confirmed, MI type (once confirmed), event date, related to study drug, rationale | Answer | Nothing until saved | **Yes** |
| Adjudicator 3 | The disputed fields only, blinded | Answer | Nothing until saved | **Yes** |
| Adjudicator | Form | Click **Save draft** | Saves a draft for this adjudicator only | **Yes** |
| Adjudicator | Form | Type the password, click **Sign my review** | **Signed** review: after both, code compares → outcome, Adjudicator 3, or committee | **Yes** |
| Adjudicator | Already signed: their own answers, read-only | Read | Nothing | **Yes** |
| Adjudicator 1 or 2 | "Ask for more data" (during reviews) | Type what is needed and the password, click **Sign and ask the site for more data** | **Signed** request: reviews pause, a query goes to the site | **Yes**: the request text |

## Committee (`views/committee.py`), CEC Coordinator and Chair

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| Coordinator, Chair | Open fields: every review's answer on them; rationales | Read | Nothing | **Yes** |
| Coordinator | Record the meeting: date, attendees, summary, a decision per open field | Fill in, click **Record the meeting** | Meeting record; quorum checked; the Chair is notified | **Yes**: summary, decisions |
| Coordinator | Ask the site for more data | Type what is needed and the password, click **Sign and send the request** | **Signed** request: a query to the site; the case comes back to the committee | **Yes** |
| Coordinator, Chair | Recorded meeting: date, attendees, quorum, summary, decisions | Read | Nothing | **Yes** |
| Chair | Consensus (quorum met, no newer data) | Type the password, click **Sign the consensus** | **Signed** consensus → outcome → export | **Yes** (decisions it signs) |
| Other adjudicators | "Signed by its Chair, Dr. …" | None | Nothing | No |

## Case record (`views/results.py`), all roles, read-only except one button

| Role | What they see | Click or type | What it changes | Clinical |
|---|---|---|---|---|
| All | Package for adjudication: who signed, when, what it confirms | Click **Download <file>** per redacted document | Nothing (a download) | **Yes**: documents |
| Non-site | Ready for adjudication: dossier version, narrative approved by | Read | Nothing | No |
| Non-site | "Narrative: what the Safety Specialist changed" | Expand | Nothing | **Yes** |
| Site | "Adjudication is done by an independent committee…" | Read | Nothing | No |
| Coordinator, Chair, others after closing | Round, reviewers, outcome per round: final answer, decided by, signed by; related to drug (PV e-mailed); export and acknowledgement | Expand a round | Nothing | **Yes**: final answers, relatedness |
| Adjudicator during reviews | Round and reviewers only | Read | Nothing | No |
| All | Audit trail intact or not; entries (when, who, what, reason) | Tick **Only actions by people** | Nothing | Possibly: reasons are free text |
| All | Source data: Adverse event tab (AE fields from the EDC) | Switch tabs | Nothing | **Yes** |
| All | Eligibility tab: rule results | Switch tabs | Nothing | **Yes**: quotes AE values |
| All | Related data: sex, age, first dose; medical history, medication, labs | Switch tabs | Nothing | **Yes** |
| All | Earlier similar events for this subject | Switch tabs | Nothing | **Yes** |
| All (blinded per role) | Notifications: when, to, message, e-mail status, Slack status | Read | Nothing | No |
| All | AI activity: each call, mode, cost | Read | Nothing | No (labels and costs) |
| All | Technical details: AI, audit seals, coding and rules, manifests, kit measurement | Expand | Nothing | Possibly: MedDRA codes |
| Safety Specialist | How well identifiers are removed | Click **Measure again (about 30 seconds)** | Runs `tools.deid_check` on the synthetic kit; writes `data/metrics` | No |

## What this means for Slack

- **Changes data:**
  - Sign and submit, sign the QC pass or query, sign a review, ask for more data (adjudicator or committee), sign the consensus, start re-adjudication. All of these are signed.
  - Upload, redaction changes, delete, relabel, import or pull, manual AE entry, reassign, record the meeting, save a draft, and "Measure again". None of these is signed.
- **Signed actions:** every signed action needs a password typed into the app, and most need clinical content on screen to decide. These stay in the app; a Slack message can only link to them.
- **Actions with no clinical content on screen** (but they still change data): import or pull, "Remove identifiers now", reassign, and start re-adjudication. The last is signed.
- **The Assistant already works like a chat**, but it puts clinical content (AE term, onset, AI summary) into the conversation. That must not be copied into Slack as it stands.
