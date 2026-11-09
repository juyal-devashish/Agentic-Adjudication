# Flows 1 and 2: how a case moves

What happens to an adverse event (AE), step by step, from the EDC to a package ready for adjudication. For each step: what triggers it, which component does it, who or what acts, and where AI and the Anthropic API come in.

The design rule behind it ([plan §2](plan-mvp.md#2-autonomy-model)): **agents do the work, code checks every agent answer before it can move a case, and people sign and handle exceptions.** Everything below runs with AI off as well; the steps marked *AI* then go to a person.

Synthetic data only. Nothing here is validated or Part 11 / Annex 11 compliant; it shows the mechanics.

## The steps

| # | Step | Trigger | Component | Who or what acts | AI / API |
|---|---|---|---|---|---|
| 1 | AE records arrive | Data manager uploads the EDC export, presses **Pull from the EDC**, or a file lands in `inbox/` | `adjud/intake.py`, `adjud/edc_pull.py`, runner | Data manager, or the runner every few minutes | None |
| 2 | Eligibility | Each record of an imported file | `adjud/rules.py` (rules from `eligibility_rules.yaml`) | Code | None |
| 3 | Not eligible | A rule not met | Intake log `data/intake/evaluations.jsonl` | Code; every "no" is kept with its reasons | None |
| 4 | Case created, EDC data pulled | Eligible record | `adjud/intake.py` | Code: AE snapshot, related data, earlier similar events | None |
| 5 | Site notified | Case created | `adjud/notify.py`, `adjud/mailer.py` | Code: e-mail with case number, task and link only | None |
| 6 | Upload folder ready | Case waiting for documents | `adjud/upload_folder.py` | Code: `uploads/<case number>/` | None |
| 7 | Site uploads documents | One ZIP in the case's folder, or the Documents page | `adjud/upload_folder.py`, `ui/views/site_documents.py` | **Site** | None |
| 8 | Identifiers removed | Upload received | `adjud/deid/` | Code: text layer and OCR, rule-based detectors, destructive redaction, metadata removed | *AI-1 (optional)*: sees each scanned page image and **adds** identifiers the rules missed. Sends unredacted images: off by default, synthetic data only |
| 9 | Verified from scratch | Redaction finished | `adjud/deid/pdf.py`, `xlsx.py` | Code: re-reads the output; anything still found blocks signing | None |
| 10 | Same patient? | During step 8 | `adjud/deid/identity.py` | Code: compares each document's date of birth and name; stores only the verdict | None |
| 11 | Site review and signature | Files verified | Documents page, `adjud/review.py` | **Site**: checks pages, keeps a wrongly blacked-out item (with a reason), drags a box over anything missed, signs | None |
| 12 | Handoff to Flow 2 | Site signed | `adjud/handoff.py` | Code: `flow2_manifest.json` with redacted files and hashes, never originals | None |
| 13 | Quality checks | Manifest written | `adjud/qc.py` | Code: readable, no identifiers left, document types, required documents, same patient, subject ID and onset date | None |
| 14 | What code cannot tell | A check is unknown | `adjud/flow2.py` | *Agent*: document type of an untitled letter; whether the documents describe this event | *AI-2 and event match*, through `adjud/ai/gateway.py`; each answer passes `adjud/ai/gates.py` (quotes found on the page, confidence, allowed values) or goes to a person |
| 15 | QC decision | Checks done | `adjud/flow2.py` | Code: all passed → passes; a clear failure → **query to the site**, back to step 7; otherwise the **Safety Specialist** decides and signs | Uses only gated agent answers |
| 16 | Dossier | QC passed | `adjud/dossier.py` | Code: cover, EDC summary, documents in checklist order, subject ID on every page, hashed | None |
| 17 | Narrative | Dossier built | `adjud/flow2.py` | *Agent* writes facts-only statements, each quoting a dossier page; code approves it if every quote is found and no forbidden wording is used; otherwise the **Safety Specialist** edits and signs | *AI-4*, gated; with AI off a template is filled and a person completes it |
| 18 | Handoff to Flow 3 | Narrative approved | `adjud/flow2.py` | Code: `flow3_manifest.json` (dossier, narrative, QC results, hashes); Safety Specialist notified | None |

Every step appends a hash-chained event to the case's `events.jsonl`; `tools/verify_log` detects any edited event or changed file.

## Where a person is still needed

- **Site:** uploads, reviews the redacted pages and signs the submission. The signature stays human.
- **Data manager:** imports or pulls the AE records.
- **Safety Specialist:** only on exceptions: a QC round code and agents could not decide, an agent answer that failed its check, a narrative that failed its check.

## AI and the API

| | |
|---|---|
| One entry point | `adjud/ai/gateway.py`; no other module uses the Anthropic SDK |
| Modes | `off` (default), `replay` (recorded answers; hand-written ones are shown as "Simulated AI"), `live` (needs `ANTHROPIC_API_KEY` in the environment) |
| Model | Small and pinned: `claude-haiku-5-5` in `study.yaml` |
| What agents can touch | Read-only local tools over redacted files and EDC snapshots. Agents never write files or move a case |
| Records | Every call in `cases/<id>/ai/`, every check of an answer as an `ai.gate` event, tokens and cost in `data/ai/usage.jsonl`, with a budget cap |
| Real data | Would need IT input and a restricted production setup. AI-1 in particular sends unredacted page images to the provider |
