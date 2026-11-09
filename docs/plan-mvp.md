# Plan — MVP: Flow 1 (Intake) + Flow 2 (QC and narrative), built around AI

| | |
|---|---|
| **Goal** | A working proof of concept of Flows 1 and 2 in which AI agents do the work and people step in only where a signature has meaning or an agent cannot decide |
| **Starting point** | Flow 1 is built and demonstrated (steps 0–7 below). Kit 53/53, de-identification gate passed |
| **Behavior sources** | Flow 1: the code, its tests and the test kit (the Flow 1 spec and plan were retired after the demo; they are in git history before this plan). Flow 2: [mvp-spec.md](mvp-spec.md) §5.2, §8 (MVP-11 to MVP-15), §9 and [spec.md](spec.md) §6.3–6.4 (QC-, NAR- IDs), **as changed by the autonomy model (§2) and the decisions (§4)** |
| **Status** | Steps 8–17 done. Tick boxes as steps are completed and committed |

---

## 1. What we are building

> An AE file arrives → eligible records become cases, every "no" explained → the case gets its own upload folder and the site is e-mailed → the site drops one ZIP → identifiers are removed (rules plus an AI detector on scans) and verified by code → the site reviews, draws boxes over anything missed, and signs → **an AI agent runs QC**: required documents, readability, residual PHI, document type, documents match the subject and the event → the dossier is built → **an AI agent drafts the narrative**, every sentence cited and checked by code → the case is ready for adjudication (Flow 3) and the Safety Specialist is e-mailed. A person is asked only when an agent's result fails a code gate.

The project's aim is to **minimise the people needed in the whole process.** Every new step is designed agent-first, with the human as the exception path.

**Until the API key arrives** everything runs in one of two modes: AI off (deterministic checks, humans handle what needs judgement) or replay (recorded agent outputs, clearly labelled *simulated*). Switching to live is a config change plus an environment variable, not new code.

## 2. Autonomy model

This replaces the old rule "no AI in any handover". It is the design rule for every step below.

| Who | Does | Never does |
|---|---|---|
| **Agents** (Claude, through `adjud/ai/gateway.py`) | Read documents, classify, check against the EDC, find extra PHI, draft the narrative, propose a decision with reasons and citations. Agents act by **calling local tools** (our deterministic functions), never by touching files | Remove a PHI finding; change a signed or locked file; send unredacted content anywhere except the AI-1 detector on synthetic data |
| **Code gates** (deterministic) | Decide whether an agent's proposal may move the case: output valid against its schema, every citation verified in the text, confidence at or above the study's threshold, every deterministic check passed. Pass → the runner appends the transition with actor `agent:<function>` and the call ID. Fail → an exception task for a person | Accept an unverified citation or a schema-invalid output |
| **People** | Site: upload, review, draw boxes, **sign the submission** (kept by the meeting). Safety Specialist: **exceptions only** (gate failures, overrides, on-hold). Data manager: AE import | Routine approvals that a gate can make |

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
| P-23 | Scope | Flows 1 and 2. Flow 2 ends at `READY_FOR_ADJUDICATION` with a handoff manifest for Flow 3 | User |
| P-24 | Autonomy | Agents act, code gates decide, people handle exceptions (§2) | User |
| P-25 | AE import role | **Data manager** replaces study admin (import and manual entry). The study admin role is removed | Meeting, user |
| P-26 | Model | Small model pinned in `study.yaml`: `claude-haiku-5-5`. A bigger model only per function, by config, if measurements show the small one is not good enough | Checklist |
| P-27 | AI provider modes | `off` / `replay` / `live` in `study.yaml`. `live` also needs `ANTHROPIC_API_KEY`; without it the gateway falls back to `off` and says so | Plan |
| P-28 | Cost control | Every call logs input and output tokens; prices per million tokens are entered in `study.yaml` from the pricing page at go-live; a budget cap (`ai.budget_usd`) stops live calls when reached | Checklist |
| P-29 | E-mail | Bare minimum: SMTP via environment variables (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`); Outlook through `smtp.office365.com:587` if the presenter's tenant allows SMTP AUTH, otherwise Mailpit. Not configured → outbox only, as today. Body: case ID, task, link; never clinical content | User |
| P-30 | Manual redaction | The site drags a box on the page preview (Streamlit's box selection on a Plotly chart). The box applies to that page of that file; text and pixels under it are removed and verification re-runs. Spreadsheets keep "black out this text" | User |
| P-31 | Upload flow | On case creation the runner makes `uploads/<case-id>/`. The site drops **one ZIP**; a complete ZIP is the "upload finished" signal (code, not AI). The folder can sit in a OneDrive-synced directory (`ADJUD_UPLOAD_DIR`) to stand in for SharePoint. The UI upload stays as a second route | Meeting |
| P-32 | Same-patient check | Deterministic first: identifiers learned from each document of a case (name, DOB, MRN) must agree across documents; event term and dates must appear near the EDC onset. The AI agent adds a judgement with citations for unclear cases. A mismatch is a QC finding, not a block on upload | Demo discussion |
| P-33 | Flow 2 sign-offs | QC pass and narrative approval are **automated decisions** when every gate passes. The Safety Specialist signs only on exceptions or overrides. *Confirm with the team; spec.md QC-06 and NAR-04 had the SS sign every case* | P-24 |
| P-34 | "Pull data from EDC" | A demo tab that copies the mock EDC export into the import queue. No integration | Meeting (optional) |
| P-35 | No redaction tracker | No separate list of redacted or mapped values; the approval process and the audit log are enough; the site keeps its original | Meeting |
| P-36 | QC pass without AI | When every deterministic check passes, QC passes automatically (`system:qc`); a check that fails for a clear reason sends a query to the site automatically; only what code cannot tell goes to the QC agent, then a person | Plan (P-24) |

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

### Step 18 — Handoff to Flow 3

- [ ] `READY_FOR_ADJUDICATION` writes `handoff/flow3_manifest.json`: dossier, narrative, QC results, with hashes
- [ ] E-mail to the Safety Specialist: "Case ready for adjudication" (and the existing "Documents received")

**Done when:** the Flow 3 manifest verifies and contains nothing from `documents/original/`.

### Step 19 — Per-case folder and ZIP upload (P-31)

- [ ] Runner creates `uploads/<case-id>/` on case creation (under `ADJUD_UPLOAD_DIR` when set); the e-mail names the folder
- [ ] A ZIP in the folder is imported once it is complete (size stable, archive valid); its files go through `upload()` exactly as UI uploads do; the ZIP is moved to `processed/`
- [ ] Rejected ZIPs (corrupt, encrypted, nested archives, path traversal) get a reason and a notification
- [ ] Tests, plus a manual run with the folder inside a OneDrive-synced directory

**Done when:** dropping one ZIP takes a case from "documents requested" to site review with nobody opening the UI.

### Step 20 — AI-1: AI finds PHI on scans and images

- [ ] Agent looks at page images and *adds* findings (never removes); its findings go through the same redaction and verification
- [ ] Synthetic data only; `tools.deid_check` reports recall with AI off vs on (ask before a live run: more than ~10 calls)

**Done when:** replay shows AI-1 findings redacted and verified; the live comparison waits for the key.

### Step 21 — "Pull data from EDC" tab (P-34, optional)

- [ ] A button on the import page that copies the mock EDC export into the queue, labelled as a demo stand-in

### Step 22 — Go live with the API key

- [ ] Key in the environment only; `ai.mode: live`; prices entered; budget cap set
- [ ] Record real outputs for every fixture and replace the simulated replays; measure cost per case and repeatability (5 runs per function)
- [ ] Re-run the full demo live; report cost

**Done when:** the full Flows 1+2 demo runs live with measured cost per case.

### Step 23 — Process document and demo

- [ ] `docs/process.md`: each step with trigger, component, who or what does it, and where AI and the API are used
- [ ] Demo script for Flows 1+2; automated rehearsal test; live rehearsal

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
