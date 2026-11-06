# Clinical Event Adjudication — MVP Specification (Proof of Concept)

| | |
|---|---|
| **Document** | mvp-spec.md |
| **Version** | 0.1 (draft for discussion) |
| **Date** | 5 November 2026 |
| **Parent** | spec.md v0.1. Requirement IDs in the "Maps to" columns refer to spec.md |
| **Stage** | This is the **POC stage** in spec.md §2.3, called MVP here. It is not the validated first release |

---

## 1. Goal

Show, with the least possible build, that the adjudication workflow in spec.md can run end to end:

> A synthetic AE file goes in. Eligible events become cases. Documents are de-identified and checked. A Safety Specialist signs off QC and the narrative. Two adjudicators review independently, disagreements are resolved, and a traceable outcome comes out, with a complete audit trail. AI is optional and switchable.

The MVP answers these questions:

1. Can eligibility rules from configuration reliably decide which AEs become cases?
2. Can PHI be removed from PDFs and scanned images well enough to be verified automatically?
3. Can the whole workflow run as deterministic code, with people making every decision and **no AI in any handover**?
4. Can every final value be traced back to the signed form it came from?
5. Does optional AI (document checks, narrative draft) add value, and how repeatable is it?
6. Can all of this run **without a database server**, using files as the system of record?

## 2. Constraints

| Constraint | Consequence |
|---|---|
| **Synthetic data only** | No real patient data, ever. The UI shows a permanent "Synthetic data only" banner |
| **No database server** | Files and folders are the system of record (§4). No PostgreSQL, no queue broker |
| **Very minimal UI** | Three pages (§7). Function over appearance |
| **No real authentication** | A role switcher simulates users. Signing asks for a demo password |
| **Single machine** | Runs on a laptop or one small VM with Docker |
| **Not validated, not Part 11 compliant** | The MVP *demonstrates* audit trail and signature mechanics; it does not claim compliance |
| **AI only with approved access** | AI functions stay off unless IT approves an API key (spec.md D-07) |

## 3. Scope

### 3.1 In scope

| Flow | MVP coverage | Maps to |
|---|---|---|
| Flow 1 Intake | CSV drop into an inbox folder; eligibility rules; case creation; related data and prior-event lookup from a mock EDC; site notification | INT-02, INT-04 to INT-06, INT-08, INT-09, INT-11 |
| Flow 1 Documents | Upload PDF, PNG, JPG; OCR; deterministic PHI detection; destructive redaction; verification; site review and submit | DOC-01 (partial), DOC-04, DOC-05, DOC-08, DOC-09, DOC-10, DOC-11, DOC-13 |
| Flow 2 QC | Rule checks; dossier build; pre-filled QC form; sign pass/fail; query and rerun | QC-01, QC-02, QC-04 to QC-08 |
| Flow 2 Narrative | Manual editor; optional AI draft; sign and freeze | NAR-01 to NAR-05 |
| Flow 3a Reviews | Manual or round-robin assignment; two blinded forms; sign; field comparison; "more data" request | ASN-01, ASN-02, ASN-05, ADJ-01 to ADJ-07 |
| Flow 3b Disagreement | Adjudicator 3 on disputed fields; consensus recorded by the Coordinator | ADJ-08, ADJ-09, ADJ-12 (simplified) |
| Flow 4 Outcome | Derivation with traceability; outcome CSV as simulated EDC export | OUT-01, OUT-02, OUT-06 (file only) |
| Re-adjudication | New round; original outcome kept as superseded | RDJ-01 to RDJ-03 |
| Cross-cutting | Hash-chained event log as audit trail; demo signatures; notifications written to an outbox | AUD-01, AUD-04, SIG-02, SIG-03, NOT-01, NOT-02 |
| AI (optional) | AI-2 document type, AI-3 EDC consistency, AI-4 narrative draft | AI-G1 to AI-G8 (lightweight) |

### 3.2 Out of scope (deferred to spec.md)

Real EDC connection; DOCX/XLSX upload; DICOM (Could, header-only if time allows); AI-1 PHI detection; authentication, RBAC enforcement and MFA; Part 11 signatures; CEC meeting scheduling and quorum; reminders and escalations; medical coding; reports beyond the results page; configuration approval workflow; multi-study operation; performance, backup and availability targets.

---

## 4. Storage without a database

### 4.1 Approach

Each case is a **folder**. Its single source of truth is an **append-only event log** (`events.jsonl`). The current state is never edited directly. It is computed by replaying the log through the workflow rules. Everything else in the folder (documents, forms, outcome) is a file referenced from the log by path and SHA-256 hash.

This gives three things a database would normally provide:

- **Audit trail for free:** the log *is* the audit trail (who, what, when, why).
- **Recoverable state:** `case.json` is only a cache; delete it and it is rebuilt from the log.
- **Tamper evidence:** each event carries the hash of the previous one, so any edit breaks the chain.

### 4.2 Folder layout

```text
data/
├── config/
│   ├── users.yaml                     # demo users, roles, hashed demo passwords
│   └── DEMO01/
│       ├── study.yaml                 # workflow options, AI switches, adjudicator pool
│       ├── eligibility_rules.yaml
│       ├── document_checklist.yaml
│       ├── narrative_template.md
│       └── forms/MACE_MI.v1.json
├── edc_mock/                          # stands in for the EDC
│   ├── ae.csv
│   ├── demographics.csv
│   ├── medical_history.csv
│   └── conmeds.csv
├── inbox/                             # drop new AE files here
├── processed/                         # imported files + import report
├── cases/
│   └── DEMO01-0001/
│       ├── events.jsonl               # source of truth
│       ├── case.json                  # cached state, rebuildable
│       ├── source/
│       │   ├── ae_snapshot.json
│       │   ├── eligibility.json
│       │   └── related_data.json
│       ├── documents/
│       │   ├── original/              # synthetic only in the MVP
│       │   ├── redacted/
│       │   └── redaction_report.json
│       ├── qc/run-01.json
│       ├── dossier/dossier-v1.pdf
│       ├── narrative/v1.md
│       ├── rounds/
│       │   └── r1/
│       │       ├── assignments.json
│       │       ├── adj1.json
│       │       ├── adj2.json
│       │       ├── discordance.json
│       │       ├── adj3.json
│       │       ├── consensus.json
│       │       └── outcome.json
│       └── ai/                        # one file per AI call
├── outbox/                            # notifications (.eml + .json)
├── exports/                           # outcome CSVs = simulated EDC export
└── logs/
```

### 4.3 Event record

One JSON object per line:

```json
{
  "seq": 14,
  "at": "2026-11-05T10:02:11Z",
  "case": "DEMO01-0001",
  "actor": "user:ss.anna",
  "type": "qc.signed",
  "data": { "result": "pass", "dossier": "dossier/dossier-v1.pdf", "dossier_hash": "sha256:9f2c…" },
  "reason": null,
  "signature": { "signer": "Anna Example", "meaning": "QC approved", "record_hash": "sha256:9f2c…" },
  "prev_hash": "sha256:41ab…",
  "hash": "sha256:c07e…"
}
```

`hash` is SHA-256 of the event without its own `hash` field. `prev_hash` of the first event is a fixed genesis value.

### 4.4 Writing rules

| Rule | How |
|---|---|
| One writer at a time per case | `filelock` on `cases/<id>/.lock` around every append |
| Validate before writing | An event is appended only if the workflow allows it from the current state (§5) |
| Files before events | A file is written first (temp file + atomic rename), then the event that references it |
| Never overwrite | New versions get new names (`run-02.json`, `dossier-v2.pdf`, `rounds/r2/`) |
| Rebuild anytime | `tools/rebuild_state.py` replays every log and must reproduce every `case.json` exactly |
| Verify anytime | `tools/verify_log.py` checks hash chains and that every referenced file still matches its hash |

### 4.5 Storage interface

All code uses one interface, so a database can replace the files later without touching the workflow:

```python
class CaseStore(Protocol):
    def create_case(self, case_id: str, first_event: Event) -> None: ...
    def append_event(self, case_id: str, event: Event) -> Event: ...   # validates, chains, locks
    def events(self, case_id: str) -> list[Event]: ...
    def state(self, case_id: str) -> CaseState: ...                    # replay or cache
    def list_cases(self, state: str | None = None) -> list[CaseState]: ...
    def put_file(self, case_id: str, rel_path: str, content: bytes) -> str: ...  # returns sha256
    def get_file(self, case_id: str, rel_path: str) -> bytes: ...
```

MVP implementation: `FileCaseStore`. Production: `PostgresCaseStore`, where `events.jsonl` maps directly to spec.md's `AuditEvent` table and files move to object storage.

### 4.6 When files stop being enough

The file approach is fine for a demo. It is time to add a database when any of these become true:

- more than one runner process, or more than a handful of simultaneous users;
- reports across hundreds of cases become slow (every case folder must be read);
- access control must actually be enforced (anyone with disk access can read or edit files);
- audit trail integrity must be guaranteed rather than just detectable;
- backup with point-in-time recovery is needed.

If a middle step is needed before the full spec.md stack, SQLite (an embedded file, no server) can hold the event log behind the same `CaseStore` interface.

---

## 5. Workflow

### 5.1 How work moves without a queue

There is no job queue. A single **runner** process loops (every 10 seconds; inbox checked every 5 minutes, configurable):

1. Import any new file in `inbox/`.
2. For every case in an **automatic** state, run that step and append the resulting event.
3. Write any pending notifications to `outbox/`.

Human steps happen in the UI, which appends events directly through `CaseStore`. The runner then picks up the next automatic step. The state of each case is therefore the queue.

### 5.2 States and transitions

Same state names as spec.md §5.2, with two simplifications: the CEC meeting becomes a single CONSENSUS step, and the EDC export happens inside OUTCOME.

| From | Event | To | By |
|---|---|---|---|
| — | `case.created` | AWAITING_DOCUMENTS | Runner (rule passed, related data pulled, site notified) |
| AWAITING_DOCUMENTS | `documents.uploaded` | DEIDENTIFYING | Site |
| DEIDENTIFYING | `deid.completed` | AWAITING_SITE_CONFIRMATION | Runner |
| AWAITING_SITE_CONFIRMATION | `redactions.changed` | DEIDENTIFYING | Site |
| AWAITING_SITE_CONFIRMATION | `submission.signed` | QC_CHECKS | Site |
| QC_CHECKS | `qc.checks_completed` | QC_REVIEW | Runner (checks, dossier, optional AI) |
| QC_REVIEW | `qc.signed` (fail) | QUERY_OPEN | Safety Specialist |
| QUERY_OPEN | `documents.uploaded` | DEIDENTIFYING | Site |
| QC_REVIEW | `qc.signed` (pass) | NARRATIVE | Safety Specialist |
| NARRATIVE | `narrative.signed` | READY_FOR_ADJUDICATION | Safety Specialist |
| READY_FOR_ADJUDICATION | `adjudicators.assigned` | ADJ_REVIEW | Coordinator or runner (auto) |
| ADJ_REVIEW | `more_data.requested` | MORE_DATA | Adjudicator |
| MORE_DATA | `documents.uploaded` | DEIDENTIFYING | Site |
| ADJ_REVIEW | `review.signed` (second of two) → `comparison.completed` | OUTCOME or ADJ3_REVIEW | Adjudicator, then runner |
| ADJ3_REVIEW | `review.signed` → `comparison.completed` | OUTCOME or CONSENSUS | Adjudicator 3, then runner |
| CONSENSUS | `consensus.signed` | OUTCOME | Coordinator |
| OUTCOME | `outcome.derived`, `export.written` | CLOSED | Runner |
| CLOSED | `readjudication.started` | READY_FOR_ADJUDICATION | Coordinator (new round) |
| any open state | `case.withdrawn` | WITHDRAWN | Safety Specialist |

After more data arrives, the case goes back through de-identification and QC (spec.md D-06). A new dossier version is built and the adjudicators review that version.

Every transition also appends a `notification.queued` event; the runner writes it to `outbox/`.

---

## 6. Components

### 6.1 Processes

| Process | What it is | Runs |
|---|---|---|
| `ui` | Streamlit app, three pages | `streamlit run ui/app.py` |
| `runner` | Python loop (§5.1) | `python -m adjud.runner` |
| `mailpit` (optional) | Local mail catcher, to show real emails | Docker image |

`docker compose up` starts all three with `./data` mounted as a volume. Nothing else is needed.

### 6.2 Code layout

```text
adjud/
├── store.py            # CaseStore interface + FileCaseStore
├── workflow.py         # states, transition table, apply_event(), replay()
├── rules.py            # eligibility rule evaluator (no eval())
├── intake.py           # inbox import, case creation, mock-EDC lookups
├── deid.py             # OCR, PHI detection, redaction, verification
├── qc.py               # QC rule checks
├── dossier.py          # cover page, EDC summary, merged bookmarked PDF
├── narrative.py
├── adjudication.py     # assignment, comparison, discordance, outcome derivation
├── signatures.py       # demo signatures: password check, record hash
├── notify.py           # outbox writer, optional SMTP to Mailpit
├── ai/
│   ├── gateway.py      # the only module that calls the AI API
│   ├── prompts/        # versioned prompt templates
│   └── schemas/        # JSON schemas for AI outputs
└── runner.py
ui/
├── app.py              # sidebar: role switcher, case picker, banner
└── pages/
    ├── 1_EDC_input.py
    ├── 2_Review.py
    └── 3_Results.py
tools/
├── make_synthetic.py   # synthetic AEs, EDC data and PDFs with seeded fake PHI
├── rebuild_state.py
├── verify_log.py
├── deid_check.py       # measures seeded-PHI removal
└── ai_repeatability.py
tests/
```

### 6.3 Libraries

| Need | Library |
|---|---|
| UI | Streamlit |
| Validation | Pydantic, jsonschema |
| Config | PyYAML |
| Locking | filelock |
| PDF read, render, redact, merge | PyMuPDF |
| OCR | Tesseract (via pytesseract) |
| PHI detection | Microsoft Presidio + spaCy English model, plus custom regex recognisers |
| Passwords | bcrypt |
| AI | Anthropic Python SDK (only if access is approved) |
| Synthetic data | Faker |
| Tests | pytest |

---

## 7. User interface

Three Streamlit pages, as proposed in the meeting. A sidebar on every page holds:

- the **"Synthetic data only"** banner;
- a **role switcher** (Site, Safety Specialist, Coordinator, Adjudicator 1/2/3) and a demo user picker;
- a **case picker**.

Blinding is shown by what each role's view displays. It is **not** enforced: anyone can switch roles. This is acceptable only because the data is synthetic.

### 7.1 Page 1 — EDC input

- Upload an AE CSV (copied to `inbox/`), or press **Import now** instead of waiting for the runner.
- Import result table: every row with *eligible / not eligible* and the rule-by-rule reasons.
- Created cases with links to the Review page.

### 7.2 Page 2 — Review

The page shows the actions allowed for the selected role in the case's current state:

| Role | State | What the user can do |
|---|---|---|
| Site | AWAITING_DOCUMENTS, QUERY_OPEN, MORE_DATA | Read the query; upload PDF/PNG/JPG; label each file's document type |
| Site | AWAITING_SITE_CONFIRMATION | View redacted pages beside the list of findings; untick a finding (reason required); add extra terms to redact; **Sign and submit** |
| Safety Specialist | QC_REVIEW | QC results table with override and reason; optional AI findings marked "AI"; open dossier; **Sign pass** or **Sign fail** with query text |
| Safety Specialist | NARRATIVE | Editor pre-loaded with the template (or the AI draft, marked); **Sign narrative** |
| Coordinator | READY_FOR_ADJUDICATION | Pick Adjudicators 1 and 2, or **Auto-assign** (round-robin) |
| Adjudicator 1/2 | ADJ_REVIEW | Dossier and narrative on the left, form on the right (generated from the JSON form definition); **Save draft**, **Sign**, **Request more data** |
| Adjudicator 3 | ADJ3_REVIEW | Same layout, disputed fields only |
| Coordinator | CONSENSUS | Unresolved fields with all three answers; enter the agreed value per field and notes; **Sign consensus** |
| Coordinator | CLOSED | **Start re-adjudication** with a reason |

Signing opens a small dialog showing the meaning of the signature and asking for the demo user's password.

### 7.3 Page 3 — Results

- Case list with current state and time in state.
- **Timeline** for the selected case: the event log as a readable audit trail, with a "chain verified ✓" indicator.
- **Outcome** with each final value linked to its source (form, field, signer, time). Superseded outcomes are shown below the current one.
- **Notifications** written for the case.
- **AI calls** for the case: function, model, prompt version, valid or not, and the human decision.
- Download buttons: dossier, outcome CSV, audit trail CSV.

---

## 8. Functional requirements

| ID | Requirement | Priority | Maps to |
|---|---|---|---|
| MVP-01 | AE CSV files dropped in `inbox/` (or uploaded on page 1) are imported every 5 minutes or on demand. Invalid files are rejected whole, with a row-level report in `processed/`. | Must | INT-02, INT-11 |
| MVP-02 | Each row is evaluated against `eligibility_rules.yaml`. The result (rule version, per-rule outcome, inputs) is stored for every row, eligible or not. | Must | INT-05 |
| MVP-03 | The same (study, subject, AE ID) never creates a second case. | Must | INT-06 |
| MVP-04 | A new case stores the AE snapshot, related data from `edc_mock/`, and prior AEs for the same subject matching the configured rule (same PT within 365 days by default). | Must | INT-04, INT-08, INT-09 |
| MVP-05 | Uploads accept PDF, PNG and JPG up to 50 MB. Images become single-page PDFs. | Must | DOC-01 |
| MVP-06 | Text is extracted from PDF text layers and, for scanned pages, by OCR. PHI is detected with Presidio plus custom recognisers (MRN, phone, study-specific patterns). | Must | DOC-04, DOC-05 |
| MVP-07 | Redaction removes the underlying text and pixels (PyMuPDF redaction annotations applied, images rasterised where needed) and strips metadata. | Must | DOC-08, DOC-09 |
| MVP-08 | After redaction, text is re-extracted and detection is re-run. Any residual finding blocks submission and is shown to the site. | Must | DOC-08 |
| MVP-09 | The site can untick findings (reason required) and add terms to redact. Every change is an event. | Must | DOC-10 |
| MVP-10 | The site submits with a demo signature (meaning *Submitted*). | Must | DOC-11 |
| MVP-11 | QC checks: required documents present per checklist; every file readable; no residual PHI; subject ID and onset date found in at least one document (deterministic text search). | Must | QC-02 |
| MVP-12 | A dossier PDF is built: cover page, EDC summary page, prior-events page, redacted documents in checklist order, bookmarks, subject ID stamped on each page. Its hash is recorded. | Must | QC-04, DOC-14 |
| MVP-13 | The Safety Specialist can override any check with a reason and signs pass or fail once. Fail creates a query; the site's new upload re-runs everything. | Must | QC-05 to QC-07 |
| MVP-14 | A passed QC locks the dossier version. Changes only come through More Data, which creates a new version. | Must | QC-08 |
| MVP-15 | Every case needs a signed narrative. The editor starts from the template, or from the AI draft when enabled. AI text stays marked. | Must | NAR-01 to NAR-05 |
| MVP-16 | Adjudicators 1 and 2 are assigned manually or by round-robin from the pool in `study.yaml`, always two different people. | Must | ASN-01, ASN-02, ASN-05 |
| MVP-17 | Forms are rendered from the JSON form definition. Signed responses are saved as files and never changed. | Must | ADJ-01, ADJ-04 |
| MVP-18 | When both reviews are signed, the runner compares fields using each field's comparator and writes `discordance.json`. | Must | ADJ-06 |
| MVP-19 | No disputed fields: go to outcome. Disputed fields: Adjudicator 3 task for those fields only, with someone other than Adjudicators 1 and 2. | Must | ADJ-07, ADJ-08 |
| MVP-20 | Adjudicator 3 resolves a field by matching Adjudicator 1 or 2. Unresolved fields go to CONSENSUS, where the Coordinator records and signs the agreed value. | Must | ADJ-09, ADJ-12 |
| MVP-21 | An adjudicator can request more data with a reason; the case returns to the site and then through de-identification and QC. | Should | ADJ-05 |
| MVP-22 | The outcome is derived from agreed, Adjudicator-3 and consensus values. Each value records its source file, field, signer and signature time. | Must | OUT-01 |
| MVP-23 | The outcome is appended to `exports/outcomes_DEMO01.csv` as a simulated EDC export, and the case closes. | Must | OUT-06, OUT-07 |
| MVP-24 | Re-adjudication creates `rounds/r<n+1>/`. The earlier outcome is marked superseded by an event and its files are untouched. The export row is flagged as a correction. | Should | RDJ-01 to RDJ-04 |
| MVP-25 | Every state change produces a notification (JSON + .eml in `outbox/`, optionally sent to Mailpit) containing no clinical content, only case ID, task and link. | Must | NOT-01, NOT-02 |
| MVP-26 | Every action is an event with actor, time, type, data, reason and hash chain. | Must | AUD-01, AUD-04 |
| MVP-27 | Signatures record signer name, meaning, time and hash of the signed file, and require the demo password. | Must | SIG-02, SIG-03 (demo) |
| MVP-28 | A Safety Specialist can withdraw a case with a reason. | Should | INT-07 |

---

## 9. AI (optional)

### 9.1 Functions

| ID | Function | Input | Output | Shown to |
|---|---|---|---|---|
| AI-2 | Document type | Redacted text of one document | One value from `document_checklist.yaml`, confidence, evidence page | Safety Specialist in QC |
| AI-3 | EDC consistency | Redacted dossier text + key EDC fields | Per field: EDC value, document value, consistent / inconsistent / not found, cited page (schema in spec.md §9.3) | Safety Specialist in QC |
| AI-4 | Narrative draft | Redacted dossier text + EDC data | Sentences, each citing a page or EDC field | Safety Specialist, narrative editor |

AI-1 (PHI finding) is excluded: it would send unredacted content to the provider, which spec.md NFR-PRV-04 ties to contractual approval. The MVP never sends unredacted files to AI.

### 9.2 Rules

- Switches in `study.yaml` (`ai: { doc_type: false, edc_consistency: false, narrative: false }`); all off by default.
- API key read from an environment variable, never stored in `data/` or the repository.
- Pinned model ID and versioned prompt files in `adjud/ai/prompts/`.
- Every output validated against its JSON schema. Up to 2 retries; then the step continues without AI and the UI says so.
- Citations checked by code: the cited page exists, and the cited value appears in that page's text. Otherwise the finding shows "unverified".
- Each call saved in `cases/<id>/ai/` (function, model, prompt version, input hash, output, valid, latency). The human's accept/edit/reject decision is an event.
- AI never appends a workflow transition. Only `runner` steps and signed human actions do.

### 9.3 Repeatability

`tools/ai_repeatability.py` runs each AI function 5 times on every synthetic case and reports, per field, how often the answers are identical. This answers the meeting's concern about outputs varying between runs with numbers rather than assumptions.

---

## 10. Configuration examples

### 10.1 `study.yaml`

```yaml
study: DEMO01
event_types: [MI, STROKE, DEATH]
adjudicator_pool: [adj.ravi, adj.lena, adj.tom, adj.sara]
assignment: round_robin          # or manual
use_adjudicator_3: true
prior_event_window_days: 365
inbox_poll_minutes: 5
ai:
  doc_type: false
  edc_consistency: false
  narrative: false
  model: "<pinned model id>"
```

### 10.2 `eligibility_rules.yaml`

Simple declarative rules. All rules must pass (`all`), or any (`any`).

```yaml
rule_set: DEMO01-ELIG
version: 1
all:
  - id: R1
    description: Serious adverse event
    field: serious
    equals: "Y"
  - id: R2
    description: Term is in the adjudicable list
    field: pt_code
    in_list: adjudicable_pts
  - id: R3
    description: Onset on or after first dose
    field: onset_date
    on_or_after_field: first_dose_date
lists:
  adjudicable_pts: ["10028596", "10042316", "10011906"]   # synthetic examples
```

### 10.3 Form definition

Uses the format in spec.md §11.4 (field type, options, comparator, `show_if`).

---

## 11. Non-functional requirements (MVP only)

| ID | Requirement |
|---|---|
| MNF-01 | Starts with one command (`docker compose up`) on a laptop or small VM. No database, broker or cloud service required. |
| MNF-02 | Automated processing of a synthetic case (10 documents, 50 pages) from submission to QC ready in under 10 minutes on a 4-core machine, AI off. |
| MNF-03 | Every write is atomic (temp file + rename) and every append is locked. Killing the runner mid-step never corrupts a case; restarting it resumes. |
| MNF-04 | `rebuild_state.py` reproduces every `case.json` exactly from the event logs. |
| MNF-05 | `verify_log.py` detects any edited event or modified file. |
| MNF-06 | No secrets in the repository or `data/`. API keys only via environment variables. |
| MNF-07 | Study behaviour changes by editing config files, not code. |

---

## 12. Test data and demo script

### 12.1 Synthetic data

`tools/make_synthetic.py` generates:

- `edc_mock/` CSVs for about 20 subjects;
- an inbox AE file of about 30 rows, mixing eligible and non-eligible events;
- source documents for each eligible event (discharge summaries, ECG reports, lab reports, troponin results) as text PDFs **and** as scanned images, with **seeded fake PHI**: names, dates of birth, addresses, phone numbers, MRNs and email addresses.

The generator writes the list of seeded identifiers, so `deid_check.py` can measure exactly what was and wasn't removed.

### 12.2 Demo scenarios

| # | Scenario | Shows |
|---|---|---|
| S1 | Import the AE file | Eligible vs not eligible, with reasons |
| S2 | Happy path: both adjudicators agree | End-to-end flow, traceable outcome |
| S3 | QC fails (missing ECG), site uploads it, QC passes | Query loop and automatic rerun |
| S4 | Adjudicators disagree on MI type; Adjudicator 3 matches one of them | Disputed fields only, majority resolution |
| S5 | Adjudicator 3 matches neither; consensus recorded | Consensus step |
| S6 | Adjudicator requests more data | Loop back through de-identification and QC, new dossier version |
| S7 | Re-adjudication of a closed case | New round, original outcome kept as superseded |
| S8 | S2 again with AI switched on | AI findings labelled and logged; same workflow and outcome path |
| S9 | Edit a line in `events.jsonl` by hand, run `verify_log.py` | Tamper detection |
| S10 | Delete all `case.json` files, run `rebuild_state.py` | State rebuilt from the event logs |

---

## 13. Success criteria

The MVP is successful when:

1. Import of the synthetic file creates exactly the expected cases (100% match with the generator's expected-results list).
2. `deid_check.py` shows every seeded identifier removed from the redacted text of the text-layer PDFs. The removal rate on scanned images is measured and reported (it is an input to the AI-1 decision, not a pass/fail gate).
3. Scenarios S1–S10 run end to end in front of stakeholders.
4. Every outcome value links to the signed form or consensus it came from.
5. S2 and S8 produce the same workflow path, showing AI changes nothing about handovers.
6. AI repeatability figures are available for each AI function used.
7. S9 and S10 pass, demonstrating the file-based store is trustworthy enough for a demo.

---

## 14. Build order

Each step ends with something demonstrable.

1. **Skeleton:** `CaseStore`, event log, hash chain, workflow table, replay, `rebuild_state.py`, `verify_log.py`, with tests.
2. **Intake:** synthetic data generator, rules engine, inbox import, page 1.
3. **Documents:** upload, OCR, Presidio, redaction, verification, site review on page 2, `deid_check.py`.
4. **QC:** checks, dossier, QC sign-off, query loop, narrative editor.
5. **Adjudication:** assignment, form renderer, comparison, Adjudicator 3, consensus, outcome, export.
6. **Results page,** notifications, re-adjudication.
7. **AI (if access approved):** gateway, AI-2, AI-3, AI-4, repeatability tool.
8. **Demo rehearsal** with scenarios S1–S10.

Steps 1–6 do not depend on AI access, so work can start before IT answers about API keys.

---

## 15. Open points for this MVP

| ID | Question | Default if not answered |
|---|---|---|
| MQ-01 | Which event type and adjudication form to demo? | MI with the example form in spec.md §11.4 |
| MQ-02 | Is Streamlit acceptable for the UI? | Yes; a server-rendered FastAPI + HTMX app is the alternative |
| MQ-03 | Will IT approve a Claude API key for synthetic data? | Build steps 1–6 regardless; AI added if approved |
| MQ-04 | Include DICOM header de-identification? | Only if time remains after step 6 |
| MQ-05 | Who plays each role in the stakeholder demo? | One presenter switching roles |
| MQ-06 | Should the demo send real emails (to Mailpit) or just show the outbox? | Outbox only; Mailpit optional |
