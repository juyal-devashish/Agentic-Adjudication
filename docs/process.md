# Flows 1 to 4: how a case moves

What happens to an adverse event (AE), step by step, from the EDC to a package ready for adjudication, and (steps 19–26, branch `flow3-4`) through adjudication to the outcome sent back to the EDC. For each step: what triggers it, which component does it, who or what acts, and where AI and the Anthropic API come in.

The design rule behind it ([plan §2](plan-mvp.md#2-autonomy-model)): **agents do the work, code checks every agent answer before it is used, and people do every step that needs medical judgement (P-37), sign, and handle exceptions.** Everything below runs with AI off as well; the steps marked *AI* then go to a person.

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
| 14 | AI Suggestion | Every QC round | `adjud/flow2.py`, `adjud/suggestion.py` | *Agent*: document type of an untitled letter; then, on every case, which documents would help and a field-by-field match with the EDC (event term, onset, end, outcome, seriousness), with reasoning and quotes | *AI-2 and `qc_suggestion`*, through `adjud/ai/gateway.py`; code checks each finding (quotes found on the page, confidence, allowed values) and marks the rest *not sure* |
| 15 | Dossier | Same QC round | `adjud/dossier.py` | Code: cover, EDC summary, documents in checklist order, subject ID on every page, hashed; a new version each round | None |
| 16 | Narrative draft | Same QC round | `adjud/flow2.py` | *Agent* writes facts-only statements, each quoting a dossier page; code checks every quote and the wording | *AI-4*, gated; with AI off the study template is filled in |
| 17 | Quality Check (one stop, P-39) | Round prepared | Quality check page, `adjud/flow2.py` | Code suggests the outcome: a failed check, missing required document or verified mismatch → query; all clear → pass; event term or onset not sure → none. The **Safety Specialist** reviews the checks, the AI Suggestion, the documents and the narrative, and signs once: the **pass with the narrative as edited**, or a **query to the site** (back to step 7). A check that failed in code can only end in a query; passing against an AI-found problem needs a reason | Uses only gated findings, as a suggestion ([ADR 0001](adr/0001-ai-is-decision-support-at-quality-check.md)) |
| 18 | Handoff to Flow 3 | Pass and narrative signed | `adjud/flow2.py` | Code: `flow3_manifest.json` (dossier, narrative, QC results, hashes); Safety Specialist notified | None |
| 19 | Assignment | Package written | `adjud/flow3.py` | Code: Adjudicators 1 and 2 by round-robin, never from the case's site; the CEC Coordinator can reassign with a reason | None (P-49) |
| 20 | Independent reviews | Assigned | Adjudication page | **Adjudicators 1 and 2**, blind, each sign the form (drafts are theirs only); either can ask for more data → a query to the site, back to step 7 and the Quality Check | None |
| 21 | Comparison | Both signed | `adjud/flow3.py`, `adjud/forms.py` | Code, field by field (exact, ±1 day for dates, free text not compared): all agree → step 24; otherwise **Adjudicator 3** signs the disputed fields only, blind | None |
| 22 | Adjudicator 3 result | Signed | `adjud/flow3.py` | Code: a field is resolved when Adjudicator 3 matches review 1 or 2; anything left goes to the committee | None |
| 23 | Committee | Open fields | Committee page | **CEC Coordinator** records the meeting (date, attendees, summary, decision per field; quorum checked by code); the **Chair** signs the consensus. More data from the committee returns here | None |
| 24 | Outcome | Agreed, resolved or consensus | `adjud/flow4.py` | Code: each final value with the signature it came from (`outcome-v1`); *related* → e-mail to pharmacovigilance | None |
| 25 | Export and close | Outcome derived | `adjud/flow4.py` | Code: CSV to `exports/edc/`; the mock EDC checks the hash and acknowledges; the case closes | None |
| 26 | Re-adjudication | Coordinator, signed, with a reason | Assignments page | New round, same adjudicators; the old outcome is superseded, the new export is a correction | None |

Every step appends a hash-chained event to the case's `events.jsonl`; `tools/verify_log` detects any edited event or changed file.

## Where a person is still needed

- **Site:** uploads, reviews the redacted pages and signs the submission. The signature stays human.
- **Data manager:** imports or pulls the AE records.
- **Adjudicators:** every review, blind and signed; Adjudicator 3 on disputed fields; the Chair signs the committee's consensus.
- **CEC Coordinator:** records the committee meeting, reassigns reviews, starts a re-adjudication.
- **Safety Specialist:** on every case, once (P-37, P-39): reviews the checks, the AI Suggestion and the narrative draft together, then signs the pass with the narrative as edited, or sends the query. The screen arrives pre-filled by code and the agents.

## AI and the API

| | |
|---|---|
| One entry point | `adjud/ai/gateway.py`; no other module uses the Anthropic SDK |
| Modes | `off` (default), `replay` (recorded answers; hand-written ones are shown as "Simulated AI"), `live` (needs `ANTHROPIC_API_KEY` in the environment) |
| Model | Pinned in `study.yaml`: `claude-opus-5-5` |
| What agents can touch | Read-only local tools over redacted files and EDC snapshots. Agents never write files or move a case |
| Records | Every call in `cases/<id>/ai/`, every check of an answer as an `ai.gate` event, tokens and cost in `data/ai/usage.jsonl`, with a budget cap |
| Real data | Would need IT input and a restricted production setup. AI-1 in particular sends unredacted page images to the provider |
