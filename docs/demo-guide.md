# Demo run sheet: Flows 1 and 2 with AI

Synthetic data only. This shows the mechanics; it is not validated, and roles are simulated (switch them in the sidebar).

## Before

1. In `.env`, set `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` and `ADJUD_AI_MODE=live`. Use `replay` to show the recorded Opus answers without calling the API.
2. Start the app: `docker compose --profile mail up -d --build`. The UI is on :8501 and Mailpit, for e-mails, on :8025.
3. Reset the demo data: `docker compose run --rm runner python -m tools.reset_demo --preload`

## Script (about 15 minutes)

1. **Roles.** The roles are data manager, site user (101, 102 and 104) and Safety Specialist. As *Site 101 Coordinator*, only site 101's cases and records are visible.
2. **AE import.** As data manager, on *AE import*:
   - **Import queued files** gives 11 cases and 11 records that aren't eligible.
   - **Why?** lists each rule's result.
   - **Pull AE records now** is a stand-in that reads the mock EDC; no live EDC is connected.
3. **Upload.**
   - Case DEMO01-0001 has the folder `data/uploads/DEMO01-0001/`, and its site gets an e-mail (show it in Mailpit).
   - Zip the three files from `data/demo_uploads/documents/DEMO01-101-0001_AE-001/` into that folder. The runner picks the ZIP up within a minute.
4. **Redaction and review.** As *Site 101 Coordinator*, on *Documents*:
   - Review the redacted pages.
   - Tick a wrongly removed item and give a reason to undo it.
   - Drag a box over anything missed.
   - Sign with the password `demo`.
5. **Flow 2.** Nobody acts after the signature:
   - Code runs the quality checks.
   - Agents answer what code can't tell, and code checks each answer.
   - The dossier is built and the agent writes the narrative, with every quote verified by code.
   - The case becomes ready for adjudication.
   - Show *Quality check* and *Case record*: the audit trail, notifications, and the AI calls with their cost.
6. **Exceptions.** Upload a scenario from `data/demo_uploads/flow2/` into a fresh case:
   - `missing_document`, `wrong_patient` and `unreadable` send the site a query.
   - `different_event` is spotted by the agent.
   - Low-confidence answers go to the Safety Specialist.
7. **Not built:** a live EDC connection, real logins, DICOM redaction, Flows 3 and 4, and anything for real patient data.

Cost on Opus 5.5 is about $0.30 per full demo run. If live AI fails, switch to `replay` and restart.
