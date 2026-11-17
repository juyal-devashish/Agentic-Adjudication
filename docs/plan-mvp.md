# Plan — MVP: Flow 1 (Intake) + Flow 2 (QC and narrative), built around AI; Flows 3–4 since step 27

| | |
|---|---|
| **Goal** | A working proof of concept of Flows 1 and 2 in which AI agents do the work and people step in only where a signature has meaning or an agent cannot decide |
| **Starting point** | Flow 1 is built and demonstrated (steps 0–7 below). Kit 53/53, de-identification gate passed |
| **Behavior sources** | Flow 1: the code, its tests and the test kit (the Flow 1 spec and plan were retired after the demo; they are in git history before this plan). Flow 2: [mvp-spec.md](mvp-spec.md) §5.2, §8 (MVP-11 to MVP-15), §9 and [spec.md](spec.md) §6.3–6.4 (QC-, NAR- IDs), **as changed by the autonomy model (§2) and the decisions (§4)** |
| **Status** | Steps 8–26 done; live rehearsal outstanding. Step 27 (Flows 3 and 4, P-41 to P-53) built on branch `flow3-4`, not merged. Tick boxes as steps are completed and committed |

---

## 1. What we are building

> An AE file arrives → eligible records become cases, every "no" explained → the case gets its own upload folder and the site is e-mailed → the site drops one ZIP → identifiers are removed (rules plus an AI detector on scans) and verified by code → the site reviews, draws boxes over anything missed, and signs → **an AI agent runs QC**: required documents, readability, residual PHI, document type, documents match the subject and the event → the dossier is built → **an AI agent drafts the narrative**, every sentence cited and checked by code → the case is ready for adjudication (Flow 3) and the Safety Specialist is e-mailed. A person is asked only when an agent's result fails a code gate. Since step 27 (branch `flow3-4`): two blind reviews, Adjudicator 3 and the committee settle the outcome, which is exported to the EDC; people adjudicate, code assigns and compares (P-41 to P-53).

The project's aim is to **minimise the people needed in the whole process.** Every new step is designed agent-first. Since P-37, a step that needs a medical specialist's judgement stays with a person on every case; agents do the work before and between those steps and prepare each decision.

**Until the API key arrives** everything runs in one of two modes: AI off (deterministic checks, humans handle what needs judgement) or replay (recorded agent outputs, clearly labelled *simulated*). Switching to live is a config change plus an environment variable, not new code.

## 2. Autonomy model

This replaces the old rule "no AI in any handover". It is the design rule for every step below.

| Who | Does | Never does |
|---|---|---|
| **Agents** (Claude, through `adjud/ai/gateway.py`) | Read documents, classify, check against the EDC, find extra PHI, draft the narrative, propose a decision with reasons and citations. Agents act by **calling local tools** (our deterministic functions), never by touching files | Remove a PHI finding; change a signed or locked file; send unredacted content anywhere except the AI-1 detector on synthetic data |
| **Code gates** (deterministic) | Decide whether an agent's answer may be used: output valid against its schema, every citation found in the text, confidence at or above the study's threshold, every deterministic check passed. Pass → the answer fills in the QC check or becomes the narrative draft, shown to the Safety Specialist as a proposal. Fail → the answer is shown with the gate's reasons. Since P-37 no gate moves a case past a medical judgement | Accept an unverified citation or a schema-invalid output |
| **People** | Site: upload, review, draw boxes, **sign the submission** (kept by the meeting). Safety Specialist, **on every case** (P-37): sign the QC result or send the query, and edit and sign the narrative. Data manager: AE import | Work that needs no medical judgement and that code or an agent can do |

Rules that do not change:

- PHI verification after redaction is deterministic and blocking. AI can **add** findings, never remove them.
- An automated decision is recorded as an event (model, prompt version, input hash, output, gate result). It is **not an electronic signature**. Real use would need validation; the MVP demonstrates the mechanics only and claims no Part 11 / Annex 11 compliance.
- With AI off the workflow still completes: the steps that need judgement become human tasks.
- Synthetic data only. Unredacted content goes to the provider only for AI-1, only on synthetic data.

## 3. The test kit and our tests

The Flow 1 kit (`data/flow1-testkit/`) stays the acceptance test for Flow 1 and is never edited; run it with `-p tests.kit_plugin`. Flow 2 has no kit, so it gets our own seeded fixtures (step 13): a complete case, a missing required document, a document for the wrong patient, a document for a different event, and an unreadable page. Agent tests use **replay**: stored outputs keyed by input hash. Tests never call the API.

## 4. Decisions

Change any of these by editing this table first.

### Carried over from Flow 1

| # | Decision | Value |
|---|---|---|
| P-01 | Date policy | `dob_only`: clinical dates are kept |
| P-02 | PHI definition | Kit categories. Must-redact: patient name, DOB, MRN, insurance no., address, phone, e-mail, next of kin, case reference. Should-redact (reported, not gated): staff names and contacts, facilities |
| P-04 | Document languages | English and German (Tesseract `eng` + `deu`) |
| P-05 | Related EDC data | Demographics; all medical history; con meds ongoing at onset; labs in list within ±30 days |
| P-06 | AE import format | CSV (UTF-8, BOM allowed, comma only) and XLSX |
| P-07 | Study | One study, `DEMO01`; sites 101, 102, 104 |
| P-08 | Python tooling | Python 3.12, `uv`, `ruff`, `pytest` |
| P-09 | Time in code | Clock injected everywhere |
| P-10 | Runner | One process; every step safe to re-run after a crash |
| P-12 | Uncoded AE | Not eligible |
| P-13 | Coded values | Trimmed, case-insensitive |
| P-14 | AE stops qualifying | Flag `no_longer_eligible`, never withdraw |
| P-15 | Older `last_modified` | Ignored as stale and logged; same timestamp with different content = changed |
| P-16 | Prior events | Strictly before onset, ≤ 365 days; PT match, else SOC |
| P-17 | Possible duplicates | Same subject and PT, overlapping or touching dates |
| P-18 | Mislabelled file | PNG named `.pdf` processed as an image with a warning |
| P-19 | Actors in events | People by user ID; automation as `system:<component>`; **agents as `agent:<function>`** |
| P-20 | Snapshots | Versioned, never overwritten |
| P-21 | Partly rejected uploads | Processed files go to site review; only an all-rejected run returns the case |
| P-22 | Identifiers in logs | Never. Findings by ID and masked text; site-added terms in a quarantined case file referenced by hash |

### New for this plan

| # | Decision | Value | Source |
|---|---|---|---|
| P-23 | Scope | Flows 1 and 2. Flow 2 ends at `READY_FOR_ADJUDICATION` with a handoff manifest for Flow 3. *Extended by P-41: Flows 3 and 4 on branch `flow3-4`* | User |
| P-24 | Autonomy | Agents act, code gates decide, people handle exceptions (§2). *Narrowed by P-37: steps that need medical judgement are always done by a person* | User |
| P-25 | AE import role | **Data manager** replaces study admin (import and manual entry). The study admin role is removed | Meeting, user |
| P-26 | Model | Pinned in `study.yaml`. First `claude-haiku-5-5` (small, tested on a personal key); since 2026-11-12 `claude-opus-5-5`, the originally intended model, on the company key: every narrative passes its gate (Haiku 18 of 25), at about 20 times the cost (step 22 results) | Checklist |
| P-27 | AI provider modes | `off` / `replay` / `live` in `study.yaml`. `live` also needs `ANTHROPIC_API_KEY`; without it the gateway falls back to `off` and says so | Plan |
| P-28 | Cost control | Every call logs input and output tokens; prices per million tokens are entered in `study.yaml` from the pricing page at go-live; a budget cap (`ai.budget_usd`) stops live calls when reached | Checklist |
| P-29 | E-mail | Bare minimum: SMTP via environment variables (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`); Outlook through `smtp.office365.com:587` if the presenter's tenant allows SMTP AUTH, otherwise Mailpit. Not configured → outbox only, as today. Body: case ID, task, link; never clinical content | User |
| P-30 | Manual redaction | The site drags a box on the page preview (Streamlit's box selection on a Plotly chart). The box applies to that page of that file; text and pixels under it are removed and verification re-runs. Spreadsheets keep "black out this text" | User |
| P-31 | Upload flow | On case creation the runner makes `uploads/<case-id>/`. The site drops **one ZIP**; a complete ZIP is the "upload finished" signal (code, not AI). The folder can sit in a OneDrive-synced directory (`ADJUD_UPLOAD_DIR`) to stand in for SharePoint. The UI upload stays as a second route | Meeting |
| P-32 | Same-patient check | Deterministic first: identifiers learned from each document of a case (name, DOB, MRN) must agree across documents; event term and dates must appear near the EDC onset. The AI agent adds a judgement with citations for unclear cases. A mismatch is a QC finding, not a block on upload | Demo discussion |
| P-33 | Flow 2 sign-offs | ~~QC pass and narrative approval are automated decisions when every gate passes~~ *Superseded by P-37* | P-24 |
| P-34 | "Pull data from EDC" | A demo tab that copies the mock EDC export into the import queue. No integration | Meeting (optional) |
| P-35 | No redaction tracker | No separate list of redacted or mapped values; the approval process and the audit log are enough; the site keeps its original | Meeting |
| P-36 | QC pass without AI | ~~When every check passes, QC passes automatically; a clear failure sends a query automatically~~ *Superseded by P-37*. Still true: only what code cannot tell goes to the QC agent | Plan (P-24) |
| P-38 | Document type at upload | The site no longer picks a type when uploading. When a file is processed, code reads the title on its first redacted page (the same title check QC uses) and tags it, e.g. *Discharge or death summary*, *ECG report*, *Laboratory results*. The site can correct the tag during its review (`document.labelled`, state unchanged); a corrected tag is handed to Flow 2 as the site's label, an automatic one is not, so QC and the agents get the same inputs as before. A document without a recognisable title stays untagged until QC, where the document-type agent answers | User (2026-11-15) |
| P-37 | Manual steps stay manual | Every step the deck marks as manual, and anything that needs a medical specialist's judgement, is done by a person on every case. AI covers the work between them. In Flow 2 every case stops twice for the Safety Specialist: **QC review** (the checks and the agents' answers are a proposal, shown pre-selected; the Safety Specialist signs the pass or sends the query, spec QC-05 to QC-07) and **narrative review** (the AI or template draft is pre-filled; the Safety Specialist edits and signs it, NAR-04). No query goes to the site without the Safety Specialist's signature. A signed pass is refused while a required check has failed, so a clear failure still always becomes a query, as before. Cases on disk with the earlier automated `qc.passed` or `narrative.approved` still replay. Why a person decides: [ADR 0001](adr/0001-ai-is-decision-support-at-quality-check.md). *The two stops become one by P-39* | User (2026-11-15, after talking to his manager) |
| P-40 | The Assistant: a guided chat in front of the workflow | Everyone lands on an *Assistant* page in the app: a chat with starter buttons, slash actions (`/tasks`, `/status`, `/qc`, `/why`, `/import`) and plain questions. **Code answers** from the case records, quoting what the AI already wrote (labelled); it makes no API call, so it works with AI off, on replay or live and costs nothing. Every manual step is a button to its page; the Assistant never signs, submits, passes QC or sends a query (ADR 0001). Its one action is the data manager's import, recorded as that person. Updates on the person's cases appear in the chat as they happen. Chosen over LibreChat for Friday's demo: no new server, MongoDB or second AI path; the same tools can move behind an MCP server for LibreChat later ([chat-layer-plan.md](chat-layer-plan.md)) | Demo review 2026-11-16; user (2026-11-17, option A) |
| P-39 | Narrative and AI Suggestion before the Quality Check | The dossier, the Narrative Draft and the AI Suggestion are all prepared before the Safety Specialist's Quality Check, so the specialist reviews documents, findings and narrative together at **one stop** and signs once: a pass signs the QC result and the edited Narrative together; a Site Query drops the draft (a new dossier version and draft follow the site's answer). The AI Suggestion (`qc_suggestion_v1`, every case) gives separate Findings: Required Documents missing and documents that would help (advisory), and a field-by-field match with the EDC (event term, onset, end, outcome, seriousness), each with reasoning and verified quotes. Code, not AI, derives the Suggested Outcome: query if a check failed or a gated Finding shows a mismatch; pass if every check passed and the event term and onset Findings are sure (doubt on end date, outcome or seriousness is shown but does not hold a pass back; decided 2026-11-17 when the recorded answers rated \"discharged home\" against \"Recovered\" at 0.60); none otherwise. A pass against an AI-found problem needs a reason. Queries go to the site only; a data-manager route is later. `event_match_v1` leaves QC; earlier cases still replay through `NARRATIVE_REVIEW`. Terms in `GLOSSARY.md`, rationale in ADR 0001 | Demo review, Nirav Darji and Nabin GC (2026-11-16) |
| P-41 | Flows 3 and 4 are built | Supersedes P-23's scope, on branch `flow3-4`. The full path: assignment, two blind reviews, comparison by code, Adjudicator 3 on the disputed fields, the committee, more data, outcome, export, close and re-adjudication. **Not built:** due dates, reminders and escalation (ASN-06, ADJ-11), specialty matching (ASN-04), declared conflicts beyond the site, unavailability (ASN-08), digests, per-study workflow variants (ADJ-14) | User (2026-11-17, Q1) |
| P-42 | People for Flows 3–4 | A CEC Coordinator (Tech Observer staff) and three Adjudicators, external clinicians at hospitals that are not study sites; one of them chairs the committee (`chair: true` in `users.yaml`). Fictional, password `demo` | User (Q2) |
| P-43 | Adjudication form | `data/config/DEMO01/forms/mi_v1.yaml`: event confirmed (exact), MI type 1–5 shown only if confirmed (exact), event date (±1 day), related to the study drug (exact), rationale (free text, not compared). A copy is stored in the case when the round starts, so the version is fixed for the round (ADJ-01) | User (Q3) |
| P-44 | Assignment | The runner assigns Adjudicators 1 and 2 by round-robin over the pool (rotated by the number of first assignments study-wide), never one whose `affiliated_sites` include the case's site (ASN-03, ACC-06). Adjudicator 3 is the next in rotation who is neither. The Coordinator can reassign an open review, with a reason (ASN-02, ASN-07) | User (Q4) |
| P-45 | Blinding | An adjudicator sees only cases assigned to them and never another review's answers; Adjudicator 3 sees only the disputed fields, not the earlier answers (Q-04). Sites see nothing of Flows 3–4: no adjudicator names, events or e-mails (ACC-04). Shown, not enforced: roles are simulated | User (Q5) |
| P-46 | Drafts and signatures | A draft is saved for its author only; a reassigned review starts empty. A signed review never changes; signing is checked before anything is written | Spec ADJ-04, ASN-07 |
| P-47 | More data | An assigned adjudicator (in review) or the Coordinator (for the committee) asks; the site gets a query with no names. The answer goes through Flow 1 and the Quality Check again (D-06), a new Flow 3 package is written, and both adjudicators review again; earlier signed reviews stay, superseded (Q-05). After the committee asked, the case returns to the committee (Q-06) | User (Q6) |
| P-48 | Committee | The meeting is held outside the tool and recorded by the Coordinator: date, attendees, summary, a decision per open field. Code checks the quorum (`adjudication.quorum`, with the Chair present); the **Chair signs** the consensus (ADJ-10, ADJ-12, Q-14) | User (Q7) |
| P-49 | No AI in Flows 3–4 | Adjudicators review the dossier and the narrative signed at the Quality Check; AI proposes nothing on the form (ADJ-03, ADR 0001). Code assigns, compares, derives and exports; people adjudicate and sign | User (Q10) |
| P-50 | Outcome and export | Code derives each final value (agreed: Adjudicator 1's answer; resolved: Adjudicator 3's; committee: the signed consensus) and records the signed form, field and signature it came from, with the derivation version `outcome-v1` (OUT-01, OUT-02). *Related to the study drug = related* e-mails the pharmacovigilance mailbox in `study.yaml`, no clinical content (OUT-03, Q-07). The CSV goes to `exports/edc/`; a mock EDC checks its hash and acknowledges; only then the case closes and the Coordinator and Safety Specialist are told (OUT-04, OUT-06, OUT-07) | User (Q8) |
| P-51 | Re-adjudication | Only the Coordinator, signed, with a reason, on a closed case. Round n+1 with the same adjudicators (RDJ-06); nothing earlier changes; the old outcome shows as superseded and the new export says it is a correction (RDJ-01 to RDJ-04, Q-08) | User (Q9) |
| P-52 | Demo data for Flow 3 | The short demo stays. `tools/make_flow3_fixtures.py` writes `agree` and `disputed` answer sets (staged by the reset in `demo_uploads/flow3/`) and can sign a case's open reviews with them as the assigned demo adjudicators | User (Q12) |
| P-53 | Pages and the Assistant | New pages *Assignments* (Coordinator), *Adjudication* (an adjudicator's form) and *Committee* (Coordinator records, Chair signs); the Case record gains *Adjudication and outcome*. The Assistant lists the new roles' tasks and reports each Flow 3–4 step, blinded as P-45 | User (Q11) |

## 5. How we work

- One step at a time, in order. Each step lists deliverables and **done when**. A step is done only when its criteria pass on a clean checkout.
- A branch per step; commit and push after every file edited; merge to `main` when the step is done (see [AGENTS.md](../AGENTS.md)).
- After every step run our tests and the whole Flow 1 kit.
- Tests never call an AI API. Synthetic data only.

---

## 6. Steps

### Steps 0–7 — Flow 1 ✅

Storage, intake, de-identification (text PDFs, spreadsheets, scans), site review and signing, handoff, the redesigned UI, demo hardening. Kit 53/53; every image variant beats the oracle. Details are in git history (`docs/plan-mvp-flow-one.md` before this plan).

### Step 8 — Scope and documents

- [x] This plan; Flow 1 spec, plan and test-case documents removed; AGENTS.md, CLAUDE.md, README and code references updated

### Step 9 — Data manager role (P-25) ✅

- [x] `data_manager` role and a demo user in `users.yaml`; `study_admin` removed
- [x] AE import, manual entry and "Import queued files" only for the data manager; UI tests updated
- [x] Kit tests still green (the kit's own `users.yaml` is not ours and is not changed)

**Done when:** only the data manager sees the import controls, and every test passes.

### Step 10 — E-mail notifications (P-29) ✅

- [x] `adjud/mailer.py`: send each queued notification by SMTP when configured; record `notification.sent` or `notification.failed` (no retry storm: one attempt per runner tick, at most 3)
- [x] User e-mail addresses in `users.yaml`; `.env.example` lists the SMTP variable names
- [x] Fix the notification link (`/review` → `/documents` or `/record`)
- [x] Tests with a fake SMTP server; one manual send to Mailpit or Outlook

**Done when:** creating a case sends the site an e-mail with only the case ID, task and link.

**Result:** 11 e-mails delivered to Mailpit from one runner pass. E-mail links name the case, not the person, so a `role=site&case=…` link now opens the case as a user of the case's own site.

### Step 11 — Draw a box (P-30) ✅

- [x] Drag a box on the page preview; it is shown before it is applied
- [x] `redactions.changed` carries `boxes: [{file, page, rect}]` in PDF coordinates; the re-run applies them (text pages: redaction annotation; image pages: black out pixels) and verification re-runs
- [x] Boxes from earlier runs are kept on later runs, like added terms
- [x] Tests: a box over text removes it from the text layer; a box on a scan removes the pixels

**Done when:** the site can cover a handwritten name on a scan and still sign.

**Result:** `streamlit-image-coordinates` does not work with Streamlit 1.67, so the preview is a Plotly chart with Streamlit's own box selection (`ui/boxdraw.py`). Checked in a real browser: drag, button, event with page fractions, re-run. Boxes cannot be removed yet; a wrong box needs the file uploaded again.

### Step 12 — AI foundation (P-26 to P-28) ✅

- [x] `adjud/ai/gateway.py`: the only module that imports the Anthropic SDK; modes `off` / `replay` / `live`; pinned model from `study.yaml`; tool-use loop so agents call local tools
- [x] `adjud/ai/tools.py`: the local tools agents may call (read page text, OCR a page, look up EDC fields, list case documents, *propose* a finding or decision). No tool writes a transition
- [x] Prompts as versioned files in `adjud/ai/prompts/`; a JSON schema per function; up to 2 retries, then continue without AI and say so
- [x] Every call saved to `cases/<id>/ai/` (function, mode, model, prompt version, input hash, output, valid, tokens, cost, latency), failures included; usage ledger `data/ai/usage.jsonl`; budget cap
- [x] `adjud/ai/gates.py`: the code gate (schema, citations, confidence, deterministic checks) → transition or exception task
- [x] Replay fixtures labelled `simulated` until real outputs are recorded; the UI shows "Simulated AI" or "AI" on every agent result
- [x] UI: an "AI activity" panel on the case record (what each agent did, its gate result, its cost); a study-wide usage and cost view

**Done when:** a dummy agent function runs end to end in replay mode through a gate, and with AI off the workflow is unchanged.

**Result:** the first real function, document type (AI-2), runs through the gateway in all three modes, tested with a scripted fake client (tool loop, schema retries, refusal, budget cap, records, chained ledger) and checked against the installed SDK's request types. The gateway uses a manual tool loop rather than the beta tool runner, so replay and off modes and the per-call record share one path. Pinned: `anthropic==1.17.0`. Not yet wired into the workflow; that is step 15. Not run against the live API: no key yet.

### Step 13 — Flow 2 workflow ✅

- [x] States after `SUBMITTED`: `QC_CHECKS` (auto) → `QC_REVIEW` (exception only) / `QUERY_OPEN` (back to the site) → `NARRATIVE` (auto) → `NARRATIVE_REVIEW` (exception only) → `READY_FOR_ADJUDICATION`. Withdrawal stays out of scope
- [x] Flow 2 reads only `handoff/flow2_manifest.json` (the Flow 1 contract does not change)
- [x] `QUERY_OPEN` → new upload → `DEIDENTIFYING`: more documents go through Flow 1 again
- [x] Rebuild and verify tools cover the new states

- [x] Safety Specialist actions, signed: pass the QC, send a query (reason codes + message), complete and sign the narrative (template draft from the EDC record); a "Quality check" page for them; the site sees the query and can upload again

**Done when:** a submitted case moves through every Flow 2 state with AI off, people handling every judgement step.

**Result:** `tests/test_flow2.py` runs the whole path, including a query that sends the case back through Flow 1 (upload, de-identification, signature, `flow2_manifest_v2.json`) before QC passes and the narrative is signed. Each resubmission gets a new manifest version, so a handed-off manifest is never overwritten. The QC round has no checks yet (step 14), so it always asks the Safety Specialist.

### Step 14 — QC checks: deterministic layer (QC-01, QC-02, P-32) ✅

- [x] `tools/make_flow2_fixtures.py`: seeded synthetic cases for QC (complete, missing document, wrong patient, different event, unreadable page), built from the kit's documents and Faker; never edits the kit
- [x] `data/config/DEMO01/document_checklist.yaml`: required documents per event type
- [x] Checks: required documents present, every file readable with page counts, no residual PHI, subject ID and onset date found, **same patient across documents**, event term and dates match the EDC
- [x] Results written as `qc/checks_v<n>.json` with an event; a failed required check opens a query to the site automatically, with reason codes

**Done when:** each Flow 2 fixture gets the expected check results with AI off.

**Result:** all five fixtures get their expected decision (`tests/test_qc.py`). On the kit's own cases: AE-001, -002, -003 and -007 pass with nobody involved; AE-005 and AE-006 each have a letter without a recognisable title, so they go to review (the QC agent's job in step 15). A first version typed documents by the first keyword anywhere on the page and mistyped two kit letters ("Catheter Laboratory" in a letterhead); types now come only from title lines and whole words. The same-patient comparison runs during de-identification and stores only verdicts; no false conflict on the corpus. The de-identification gate still passes.

### Step 15 — QC agent (AI-2, document–event match) ✅

- [x] Agent functions: document type (AI-2) for documents without a recognisable title; event match ("do these documents describe this AE?") with quotes. AI-3 (field-by-field EDC consistency) is left out: the event match covers what QC needs from it
- [x] Gate: valid answer, confidence ≥ threshold, every quote found on its page, the type on the study's list, the verdict not "unclear" → the answer fills in the unknown check, and the round then passes or queries the site with actor `agent:qc`; otherwise the gate's reasons go to the Safety Specialist (`ai.gate` event each time)
- [x] Safety Specialist exception page: the agent's proposals with their source (AI / Simulated AI) and whether code accepted them, override with reason, sign pass or fail
- [x] Simulated replays for every agent input of the demo cases (`tools/simulate_ai.py`, three answers); `ADJUD_AI_MODE=replay` switches a demo to them without editing config

**Done when:** in replay mode the complete case passes QC with no person involved, and each faulty case ends in the right exception or query.

**Result:** in replay mode the agent types kit AE-005's GP summary letter (the case then passes with nobody involved) and AE-006's clinic letter (the case gets a query for the missing discharge summary and ECG), and spots that the "different event" fixture is a hip fracture (query `wrong_event`). A doctored answer whose quote is not on the page is refused by the gate and goes to the Safety Specialist. The complete fixture needs no agent at all.

### Step 16 — Dossier (QC-04, QC-08) ✅

- [x] Dossier PDF: cover, EDC summary, prior similar events, redacted documents in checklist order, bookmarks, subject ID on every page; hashed and versioned
- [x] Locked once QC passes; later changes only through a new version

**Done when:** every passed case has a hashed dossier and changing a file afterwards is refused.

**Result:** `adjud/dossier.py` builds `dossier/dossier_v<n>.pdf` at the start of the narrative step, once per passed QC round, from the manifest only, with a page map (which document starts where) for the narrative agent to cite. Documents are already locked by the workflow after signing; the dossier file is never overwritten, and editing it is caught by `verify_log` (tested). Download on the Quality check page.

### Step 17 — Narrative agent (AI-4, NAR-01 to NAR-06) ✅

- [x] Agent drafts a facts-only narrative; every sentence cites a dossier page or EDC field; code checks every citation
- [x] Gate: all citations verified, no forbidden wording (diagnosis, causality, adjudication, blinded fields) → `narrative.approved` by `agent:narrative`; otherwise `NARRATIVE_REVIEW` for the Safety Specialist (editor, AI text labelled, edits tracked, sign)
- [x] AI off: the template is filled from EDC fields and goes to `NARRATIVE_REVIEW`

**Done when:** in replay mode a complete case reaches a frozen, cited narrative with no person involved.

**Result:** the agent reads the dossier through a `read_dossier_page` tool and returns statements, each with a dossier page and a quote. The gate checks every quote on its page, a minimum number of statements, and the study's forbidden-wording list in `study.yaml` (cause, relatedness, adjudication, allocation). In replay mode five of the six kit cases and the complete fixture reach `READY_FOR_ADJUDICATION` with only the site's people in the audit trail; AE-006 waits on its query. The simulated narratives are mechanical (EDC facts plus one statement per document, from each case's own dossier) and labelled "Simulated AI"; real ones come with the key (step 22).

### Step 18 — Handoff to Flow 3 ✅

- [x] `READY_FOR_ADJUDICATION` writes `handoff/flow3_manifest.json`: dossier, narrative, QC results, with hashes
- [x] E-mail to the Safety Specialist: "Case ready for adjudication" (and the existing "Documents received")

**Done when:** the Flow 3 manifest verifies and contains nothing from `documents/original/`.

**Result:** the runner writes `handoff/flow3_manifest.json` once per case (dossier with its page map, narrative with who approved it and whether the AI was simulated, QC results, the Flow 2 manifest, each with its hash) and notifies the Safety Specialist. Shown on the case record.

### Step 19 — Per-case folder and ZIP upload (P-31) ✅

- [x] Runner creates `uploads/<case-id>/` on case creation (under `ADJUD_UPLOAD_DIR` when set); the e-mail names the folder
- [x] A ZIP in the folder is imported once it is complete (size stable, archive valid); its files go through `upload()` exactly as UI uploads do; the ZIP is moved to `processed/`
- [x] Rejected ZIPs (corrupt, encrypted, nested archives, path traversal) get a reason and a notification
- [x] Tests (`tests/test_upload_folder.py`), including `ADJUD_UPLOAD_DIR` pointing elsewhere
- [ ] Manual run with the folder inside a real OneDrive-synced directory (left to the presenter: it syncs files to their account)

**Done when:** dropping one ZIP takes a case from "documents requested" to site review with nobody opening the UI.

**Result:** done and tested, except the real OneDrive run. A ZIP is taken once its size and time stamp have not changed for one runner tick (about 10 s). Folders exist for cases waiting for documents or answering a query; the e-mails for those tasks name the folder. `runner --once` makes one pass, so it never takes a ZIP it has just seen.

### Step 20 — AI-1: AI finds PHI on scans and images ✅

- [x] Agent looks at page images and *adds* findings (never removes); its findings go through the same redaction and verification
- [x] Synthetic data only; off unless `functions.phi_detection` is switched on
- [ ] `tools.deid_check` with AI off vs on: moved to step 22, because it needs live calls (ask first: more than ~10). Simulated AI-1 answers on the scored corpus would make the recall figures meaningless, so none are written

**Done when:** replay shows AI-1 findings redacted and verified; the live comparison waits for the key.

**Result:** on every OCR'd page the agent gets the unredacted page image (downscaled, its hash in the input) and returns strings with categories; each is located in the page's OCR text, blacked out with detector `ai`, and passed to verification as a term that must be gone. An identifier OCR cannot place becomes a warning for the site. Tested with a scripted client: a made-up name no rule catches is removed from a scan.

### Step 21 — "Pull data from EDC" tab (P-34, optional) ✅

- [x] A button on the import page that exports the mock EDC's AE table (without its `H-` history records, set in `study.yaml`) into the queue and imports it, labelled as a demo stand-in; pulling again only adds or updates

### Step 22 — Go live with the API key ✅

- [x] Key in the environment only (`.env`, gitignored; `ADJUD_AI_MODE=live`); prices checked ($0.10 / $0.50 per MTok, Haiku 5.5); budget cap $1 while testing on a personal key
- [x] Record real outputs for every fixture and replace the simulated replays; measure cost per case and repeatability (5 runs per function): `tools.ai_eval --runs 5 --record`
- [x] `tools.deid_check` with AI-1 off vs on, live (`--ai`)
- [x] Re-run the full demo live; report cost (each `ai_eval` run is the full demo: 6 kit cases and 5 Flow 2 scenarios)

**Done when:** the full Flows 1+2 demo runs live with measured cost per case.

**Result** (2026-11-09, `claude-haiku-5-5`, 5 live runs, `data/metrics/ai_eval/`):

- 40 agent calls, all schema-valid; total $0.072, about $0.014 per full demo run. Mean cost per case: $0.001–0.004 (narrative ≈ $0.0025, 14 s median; document type $0.0006, 3 s; event match $0.0009, 5 s).
- Outcomes as expected in 5/5 runs for every case except kit AE-005: the agent types its poor-quality GP letter as a discharge summary but with confidence 0.55–0.60 in every run, so the gate sends it to the Safety Specialist (the simulated answer had assumed 0.86). The answer itself is consistent; the gate does its job. With the citation fix below, 4 of 6 kit cases reach adjudication with only the site's signature (AE-005 to review, AE-006 queried for missing documents).
- Found by the evaluation and fixed: narrative quotes from **scanned** documents always failed the citation gate, because the dossier footer gave the image-only page a text layer and its OCR text was never read (AE-002 in 5/5 runs, AE-007 in 2/5). Dossier pages now carry their source page's text; re-checked, citation failures on the 25 live narratives fall from 7 to 0.
- Repeatability: document type and event verdicts identical in 5/5 runs. Narrative wording varies run to run (expected); gate outcome now identical once the fix applies.
- AI-1 (7 scanned pages, $0.0039): identical scores to AI off, because the rule-based detectors already remove 100% on every scan variant; the agent found the same identifiers (including on the German letter) and flagged no clinical content. It adds a second, independent reader rather than measurable recall on this corpus.

**Result on Opus** (2026-11-12, `claude-opus-5-5`, company key and workspace, 5 live runs, `data/metrics/ai_eval/`; replays re-recorded from these answers, Haiku's removed):

- 40 agent calls, all schema-valid; total $1.48, about $0.30 per full demo run. Mean cost per case $0.02–0.06 (narrative ≈ $0.046, 10 s median; document type $0.018, 4 s; event match $0.028, 8 s).
- Narratives: 25 of 25 pass the citation gate (Haiku: 18 of 25 before the fix).
- Outcomes as expected in 5/5 runs except kit AE-005 (document type confidence 0.6–0.7, so the Safety Specialist reviews, as with Haiku) and the `different_event` fixture in 1 of 5 runs (event-match confidence 0.75: to the Safety Specialist rather than an automatic query). 4 of 6 kit cases reach adjudication with only the site's signature.
- Repeatability: no inconsistent verdicts across the 5 runs. Spending cap raised to $10 for the company key.

- Limitation seen in the first live narrative: the gate checks that each quote is on its page, not that the quote supports every fact in the statement (a statement may add a second value from the same page). Open: tighten (one quote per fact, or numbers in the statement must appear in its quote).

### Step 23 — Process document and demo ✅ (live rehearsal outstanding)

- [x] `docs/process.md`: each step with trigger, component, who or what does it, and where AI and the API are used
- [x] Demo script for Flows 1+2 (README) and an automated rehearsal in replay mode (`tests/test_demo_script.py`)
- [ ] Live rehearsal by the presenter

**Result:** screenshots of every page, including the Quality check page and the eight-step rail, at desktop and phone width (`tools/screenshots.py`, now in replay mode). They showed one misleading message, "AI is off" in replay mode when no answer was recorded; fallbacks now give the gateway's own reason.

### Step 24 — Safety Specialist signs every case (P-37) ✅

- [x] Plan: P-37 recorded, §2 rewritten
- [x] QC: the round's outcome (pass, query or unclear) is stored as a proposal; the case always goes to `QC_REVIEW`; no unsigned query; no signed pass while a required check has failed
- [x] Narrative: the AI draft (gate result shown) or the template always goes to `NARRATIVE_REVIEW`
- [x] Quality check page: decision and query reasons pre-selected from the proposal; wording says what the AI did, not that it decided
- [x] Tests, fixtures' expected proposals, `tools.ai_eval`, docs (AGENTS.md, CLAUDE.md, process, demo guide, README) updated

**Done when:** in replay mode no case reaches `READY_FOR_ADJUDICATION` without `qc.signed_pass` and `narrative.signed`, every `query.opened` carries a signature, and the full kit and test suite pass.

**Result** (2026-11-15): every QC round now ends at QC review with a proposal; queries carry the Safety Specialist's signature; a check failed in code cannot be signed as passed; every narrative is signed by a person, and a signed AI draft keeps its simulated label. The replay demo reaches adjudication with three signatures (site, QC, narrative). The recorded agent answers still match, because the agents' inputs did not change. Full test suite and kit green; the 11 cases already on disk rebuild unchanged.

### Step 25 — One Quality Check with the narrative and the AI Suggestion (P-39)

- [x] Decisions: P-39, ADR 0001, `GLOSSARY.md`
- [x] Workflow: dossier and Narrative Draft built before QC review; one signed pass covers QC and the Narrative
- [x] `qc_suggestion_v1`: Findings with reasoning and quotes, gated in code; Suggested Outcome derived in code
- [x] Quality check page: findings, suggested outcome, narrative editor, one signature; Case record shows the Narrative Edit
- [x] Stored answers and tests: clean pass, more documents, mismatch, not sure
- [x] Docs: process, demo guide, README, screenshots

**Done when:** in replay mode every case stops once for the Safety Specialist with the narrative and the AI Suggestion on screen, no case reaches `READY_FOR_ADJUDICATION` without one signature covering QC and the narrative, the four suggestion scenarios have tests, and the full kit and test suite pass.

**Result** (2026-11-17): the QC round now builds the dossier, gets the AI Suggestion (`qc_suggestion_v1`) and drafts the narrative before the Safety Specialist looks; one `quality_check.signed` covers the pass and the narrative as edited, and the Case record shows the Narrative Edit. Live answers recorded with `tools.record_ai` ($0.89 in all, over three recordings: keying on the documents' text hash first broke between runs, then broke again when the site edited a redaction, so the AI Suggestion is now keyed on the documents' names and types, as the narrative is, and its quotes are still checked against the real text): AE-001 and `complete` → suggested pass, AE-006 → query (discharge summary and ECG missing; lab results would help), `different_event` → query (onset 02/11/2025 against 2026-03-14), AE-005 → none (event term and onset not sure). The recorded answers rated "discharged home" against "Recovered" at 0.60, so only the event term and onset must be sure for a suggested pass (recorded in P-39). Full test suite and the kit (53) green; `verify_log` and `rebuild_state --check` clean. Cases from before P-39 still replay; one waiting at QC review from before P-39 cannot be signed and needs a demo reset.

### Step 26 — The Assistant (P-40) ✅

- [x] Decision P-40 and [chat-layer-plan.md](chat-layer-plan.md) (option A)
- [x] `ui/assistant.py`: tasks per role and site, case status, quality-check summary, eligibility reasons, the data manager's import, live updates; buttons to the page for each manual step
- [x] Assistant page, the first page for every role; tests (`tests/test_assistant.py`)
- [x] Run sheet scenes

**Done when:** each role lands on the Assistant, sees only its own cases, can import (data manager only), ask why a record is not a case, get the quality-check summary, and reach every manual step by a button; nothing is signed from the chat; full suite and kit pass.

### Step 27 — Flows 3 and 4 (P-41 to P-53) ✅ on branch `flow3-4`

- [x] States, transitions and notifications for Flows 3–4 (`adjud/workflow.py`, `adjud/notify.py`; e-mail to one person or a mailbox)
- [x] The form and its comparators (`adjud/forms.py`, `forms/mi_v1.yaml`)
- [x] Flow 3 (`adjud/flow3.py`): assignment, drafts, signed reviews, comparison, Adjudicator 3, more data, reassignment, committee meeting and the Chair's consensus
- [x] Flow 4 (`adjud/flow4.py`): outcome with provenance, pharmacovigilance e-mail, CSV export, mock EDC acknowledgement, close, re-adjudication
- [x] Runner steps; a new Flow 3 package after more data; blinding in the UI, toasts and Assistant
- [x] Pages: Assignments, Adjudication, Committee; *Adjudication and outcome* on the Case record
- [x] Tests (`tests/test_flow3.py`, `tests/test_ui_flow3.py`) and the demo answer sets (`tools/make_flow3_fixtures.py`)

**Done when:** a case goes from ready for adjudication to closed by agreement, through Adjudicator 3, and through the committee; more data comes back through Flow 1 and the Quality Check; a re-adjudication supersedes the old outcome; every final value names the signature it came from; the site sees nothing of it; full suite and kit pass; `verify_log` and `rebuild_state --check` clean.

---

## 7. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The API key is delayed | Agents cannot be shown live | Replay mode, clearly labelled simulated; everything else is built and tested |
| Company tenant blocks SMTP AUTH for Outlook | No e-mail from the presenter's account | Mailpit; or a Microsoft Graph app registration through IT |
| Small model too weak for QC or narrative | Many exceptions; people needed again | Measure the exception rate per function; raise the model per function by config (P-26) |
| Automated approvals read as e-signatures | Regulatory misunderstanding | Events and UI say "automated decision"; no compliance claim |
| AI-1 sends unredacted images | Real PHI could leave if real data is used | Synthetic only; AI-1 off by default; IT input before any real data |
| Costs grow during testing | Budget surprise | Usage ledger, budget cap, cost per case on the AI activity view |
| Box drawing relies on Plotly box selection in Streamlit | Behaviour changes on upgrade | Browser check in the step; pinned versions |
