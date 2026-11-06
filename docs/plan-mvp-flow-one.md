# Plan — Flow 1 (Intake) Proof of Concept

| | |
|---|---|
| **Implements** | [mvp-flow-1.md](mvp-flow-1.md) v0.1 (referred to below as "the spec") |
| **Checked against** | [flow-1-testcases.md](flow-1-testcases.md) and the test kit in [data/flow1-testkit/](../data/flow1-testkit/README.md) |
| **Goal** | A really simple, working proof of concept of Flow 1, ready for a stakeholder demo |
| **Status** | Steps 0–1b done. Tick boxes as steps are completed and committed |

---

## 1. What we are building

One demo, end to end, on a laptop with `docker compose up`:

> A synthetic AE file is dropped in the inbox → eligible rows become case folders with an explained result for every row → the site is notified → the site uploads documents (text PDFs, scans, a spreadsheet) → PHI is removed destructively and verified by code → the site reviews, corrects and signs → a hashed handoff manifest for Flow 2 is written → the timeline shows a verified audit trail and the metrics page shows how much PHI was removed.

"Really simple" means: **what the test kit checks, plus the spec's Musts, nothing more until the core demo works.** Remaining Shoulds and Coulds are stretch steps.

## 2. The test kit is the acceptance test

The kit (`data/flow1-testkit/`) replaces the generator and scorer we had planned to write ourselves:

| Kit part | What it gives us | Used in |
|---|---|---|
| `config/` | Study config: rules with synthetic `9xxxxxxx` codes, related-data filters, de-identification categories, users, synthetic dictionary | Adopted as `data/config/` (step 2) |
| `edc_mock/`, `inbox_scenarios/`, `expected/` | Mock EDC, 10 import files + 2 manual entries, expected results | Intake (step 3) |
| `documents/`, `ground_truth/`, `identities/` | 17 documents for 6 cases with seeded PHI and per-item ground truth | De-identification (steps 4–5) |
| `tools/deid_check.py` | Scoring harness with the pass/fail **gate** | Steps 4–5, metrics page |
| `tests/` | 53 tests: TC-RUL, TC-INT (run against **our** `adjud` package), TC-COR, TC-HAR (check the kit itself) | Every step |
| `adjud/` | A reference intake implementation | Reference only. Our own package replaces it |

**How the kit tests run against our code.** The kit's `conftest.py` puts the kit folder first on `sys.path`, which would load the kit's reference `adjud`. `tests/kit_plugin.py` imports our `adjud` modules first, so the kit tests exercise our implementation unchanged:

```bash
uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests            # all kit tests
uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests -m "not ocr and not slow"
```

The kit is not modified. If our team decides a kit decision differently (README "Decisions the tests encode"), the change goes into the kit's expected files with a note here.

## 3. Scope

### 3.1 Core (required for the demo)

| Area | Included | Spec | Kit cases |
|---|---|---|---|
| Storage | `CaseStore`, hash-chained `events.jsonl`, `case.json` cache, intake log + rebuildable index, rebuild and verify tools | §5, F1-NF-03/04 | TC-INT-14, 15 |
| Intake | CSV **and XLSX** import, whole-file rejection with row-level errors, eligibility rules, evaluation log, idempotency, `source.changed`, `no_longer_eligible`, stale rows, related data, prior events, **manual entry**, **possible-duplicate flag**, inbox processing | F1-01 to F1-09 | TC-RUL-01–09, TC-INT-01–21 |
| Documents: rejection | Type from content; encrypted, corrupt, unsupported rejected; mislabelled PNG processed with a warning | §7.2 | TC-DOC-14–17 |
| Documents: text PDFs | English **and German**; Presidio + regex + label-context rules; allow-list; `dob_only`; destructive redaction; metadata scrub; verification | §7.3–7.5, §7.8 | TC-DOC-01, 02, 06, 11, 12 (gated) |
| Documents: XLSX | Every cell incl. hidden sheets/rows/columns, comments, properties, defined names, formula results | §7.6 | TC-DOC-03 (properties gated) |
| Documents: scans | PNG/JPG/TIFF/image-only PDFs, dual extraction, OCR with orientation detection, image-only output pages | §7.1, §7.3, §7.5 | TC-DOC-04, 07–10, 13, 16 (measured) |
| Site review | Previews, findings table, untick with reason, add terms, re-run, **Sign and submit** gated on verification | F1-20 to F1-24, F1-26 | TC-DOC-M1–M3 |
| Handoff | `flow2_manifest.json` with hashes; `handoff.flow2_ready` | §11 | TC-DOC-M4 |
| Notifications | JSON (+ `.eml`) in `outbox/`, no clinical content | §10 | TC-INT-16, 17 |
| Measurement | Kit harness on our pipeline's output; results on page 3 | §13 | TC-HAR, gate |
| UI | Three Streamlit pages, banner, role switcher, case picker | §9 | — |

### 3.2 Stretch (only after the core demo is rehearsed, in this order)

| Stretch | Spec | Kit / scenario |
|---|---|---|
| A. Case withdrawal by the Safety Specialist | F1-11 | — |
| B. DICOM header de-identification, then burned-in pixels | §7.7 | TC-DOC-05, S10 |
| C. AI-1 as an extra detector (needs an approved API key) | §8 | S11 |
| D. File-type label per upload; `.eml` copies; Mailpit | F1-25, §10 | — |

DICOM is not gated by the kit, but a missing output counts against the measured rates, so until stretch B the DICOM rates show as "missing".

### 3.3 Not in this POC

Everything from Flow 2 on; DOCX; the SQLite EDC adapter (F1-10); the `shift` and `redact_all` date policies; hand-drawn redaction boxes; languages other than English and German.

## 4. Decisions

From the spec's open questions (§17), the kit's encoded decisions (kit README) and the gaps both leave. Change any of these by editing this table first.

| # | Decision | Value | Source |
|---|---|---|---|
| P-01 | Date policy (Q-F1-01) | `dob_only`: clinical dates must be kept | Spec default, kit #11 |
| P-02 | PHI definition (Q-F1-02) | Kit categories: must-redact = patient name, DOB, MRN, insurance no., address, phone, e-mail, next of kin, case reference; should-redact (reported, not gated) = staff names/contacts, facilities | Kit `deid_policy.yaml` |
| P-03 | Known identifiers typed in by site (Q-F1-03) | Not in core | Plan |
| P-04 | Document languages (Q-F1-05) | **English and German** (site 104). Tesseract `eng` + `deu` | Kit corpus |
| P-05 | Related EDC data (Q-F1-06) | Demographics (sex, age, first dose); all medical history; con meds ongoing at onset (end = onset counts); labs in list within ±30 days | Kit `related_data.yaml`, #7 |
| P-06 | AE import format | CSV (UTF-8, BOM allowed, comma only; semicolon rejected) and XLSX | Kit #2 |
| P-07 | Study | One study, `DEMO01`; sites 101, 102, 104 | Kit |
| P-08 | Python tooling | Python 3.12, `uv`, `ruff`, `pytest` | Plan |
| P-09 | Time in code | Clock injected everywhere | Plan |
| P-10 | Runner | One process; every step safe to re-run after a crash | Plan |
| P-11 | Demo reset | `tools/reset_demo.py` loads kit scenarios and documents into a fresh `data/` | Plan |
| P-12 | Uncoded AE | Not eligible (R2 "missing value") | Kit #1 |
| P-13 | Coded values | Trimmed, case-insensitive (`" y "` → `Y`) | Kit #3 |
| P-14 | AE stops qualifying | Flag `no_longer_eligible`, never withdraw | Kit #4 |
| P-15 | Older `last_modified` | Ignored as stale, logged; equal timestamp with different content = changed | Kit #5 |
| P-16 | Prior events | Strictly before onset, ≤ 365 days (365 included); PT match, else SOC | Kit #6 |
| P-17 | Possible duplicates | Same subject and PT, overlapping or touching dates | Kit #8 |
| P-18 | Mislabelled file | PNG named `.pdf` processed as image with a warning | Kit #10 |
| P-19 | Actors in events | People by user ID (`ss.anna`); automation as `system:<component>` | Kit TC-INT-11 |
| P-20 | Snapshots | Versioned, never overwritten: `source/ae_snapshot_v1.json`, `_v2` … | Kit TC-INT-04 |

## 5. How we work

- One step at a time, in order. Each step lists **deliverables**, the **kit cases** it must turn green, and **done when**. A step is done only when its criteria pass on a clean checkout.
- A branch per step (`step-3-intake`, …); commit and push after each substantial change; merge to `main` when the step is done (see [AGENTS.md](../AGENTS.md)).
- After every step, run the **whole** kit and our own tests, not just the new ones.
- Tests never call an AI API. Synthetic data only.

---

## 6. Steps

### Step 0 — Project setup ✅

- [x] `pyproject.toml` (uv) with pinned dependencies, spaCy `en_core_web_lg`, ruff, pytest
- [x] Package layout: `adjud/`, `ui/`, `tools/`, `tests/`
- [x] `.gitignore`, `.env.example`
- [x] `Dockerfile` (Tesseract) and `docker-compose.yml`
- [x] Sample config and demo users
- [x] Real commands in AGENTS.md

### Step 1 — Storage skeleton ✅

- [x] `adjud/events.py`, `adjud/chain.py` (hash chain, lock, torn-write recovery with quarantine)
- [x] `adjud/store.py`: `FileCaseStore` (lock, transition check, file-reference check, never overwrite, atomic writes)
- [x] `adjud/workflow.py`: Flow 1 states, transition table, replay
- [x] `tools/rebuild_state.py`, `tools/verify_log.py` (cases and intake log)
- [x] Tests: chain, tamper, rebuild byte-identical, refusals, orphan file, torn write

### Step 1b — Align storage with the kit interface ✅

- [x] `ChainedLog` stores flat records (`{seq, at, …fields, prev_hash, hash}`): case events keep the `Event` shape; intake entries are flat evaluation records
- [x] `adjud.store` exposes `read_log(path)` and `verify_log(path) -> (ok, message)` with the failing line number
- [x] `FileCaseStore` exposes the kit's view: `root`, `events(id) -> list[dict]`, `list_cases() -> list[str]`, `get_json`, `put_json`, `case_dir`, `evaluations_path`, `load_index`, `rebuild_index`
- [x] Workflow table gains `case.flagged` and `source.stale_ignored` (no state change)
- [x] Our step 1 tests still pass

**Done when:** our tests pass and the kit's TC-INT-14 and TC-INT-15 could run against the store (they also need intake, step 3).

### Step 2 — Adopt the test kit

- [ ] Commit `data/flow1-testkit/` (un-ignore it; exclude it from ruff)
- [ ] Replace `data/config/` with the kit's config, keeping our additions: bcrypt hashes for demo users, detector settings (regex recognisers) in `deid_policy.yaml`, requested documents in `study.yaml`
- [ ] Add the kit's dependencies: `openpyxl`, `pydicom` (runtime), `pypdf`, `numpy` (dev)
- [ ] `tests/kit_plugin.py` and the kit commands in AGENTS.md
- [ ] Install Tesseract German (`brew install tesseract-lang` locally; `tesseract-ocr-deu` in the Dockerfile)

**Kit cases:** TC-COR-01–12 and TC-HAR-01–09 pass (they test the kit itself, so they pass before our code exists).

**Done when:** the full kit runs from the repo root; COR and HAR are green; RUL and INT fail only because intake is not built yet.

### Step 3 — Intake

- [ ] `adjud/rules.py`: `evaluate(rule_set, ctx)` and `RuleError`; all eight operators, `all`/`any` nesting with child results, "missing value" reasons, config errors for unknown lists and malformed rules. No `eval()`
- [ ] `adjud/intake.py`: `IntakePipeline(store_root, config_dir, edc_dir)` with `import_file`, `manual_entry`, `process_inbox`, `case_for`, `case_state`, `store`
  - [ ] CSV (BOM, comma only) and XLSX (Excel and text dates → ISO) readers
  - [ ] Validation against the template: missing columns, invalid dates/values, unknown subject, duplicate key, wrong study, missing value; reject the whole file with row/column/code errors
  - [ ] Normalisation of coded values; Unicode preserved
  - [ ] Evaluation log entry for every record (rule set, version, explanation, outcome); index cache rebuildable from the log
  - [ ] Outcomes `created`, `not_eligible`, `changed`, `unchanged`, `stale`; versioned snapshots; `source.changed`, `no_longer_eligible`, `source.stale_ignored`
  - [ ] Related data and prior events per `related_data.yaml`; possible-duplicate flag
  - [ ] New case goes to AWAITING_DOCUMENTS; `documents_requested` notification to `site:<id>`
  - [ ] Manual entry with mandatory reason, `source = MANUAL`
- [ ] `adjud/notify.py`: outbox writer, no clinical content
- [ ] `adjud/runner.py`: inbox polling via `process_inbox`, reports to `processed/`
- [ ] UI: sidebar (banner, role switcher, case picker) and **Page 1** (upload, Import now, per-row results with reasons, flagged cases)

**Kit cases:** TC-RUL-01–09, TC-INT-01–21.

**Done when:** all RUL and INT cases pass against our package, and scenarios S1–S4 work in the UI.

### Step 4 — De-identification gate: rejection, text PDFs, spreadsheets

- [ ] `adjud/deid/` modules: `sniff` (type from content), `validate` (limits; ENCRYPTED, CORRUPT, UNSUPPORTED_TYPE; mismatch warning), `extract`, `detect` (Presidio + regex + English and German label-context rules), `policy` (allow-list, `dob_only`), `redact`, `scrub`, `verify`, `xlsx`
- [ ] German: label rules (`geb.`, `Pat.-Nr.`, `Versichertennr.`), dd.mm.yyyy dates, umlauts; decide whether a German spaCy model is needed by measuring D12
- [ ] XLSX: every cell incl. hidden sheets/rows/columns, identifier-header columns, comments, properties, external links, defined names; formulas over redacted cells replaced by values
- [ ] Pipeline wired into the case workflow (DEIDENTIFYING → AWAITING_SITE_CONFIRMATION, or `deid.failed`); one report per file
- [ ] `tools/run_corpus.py`: run our pipeline over the kit's `documents/` into `<out>/<case_key>/<expected_output>`
- [ ] `tools/deid_check.py`: `run_corpus` then the kit harness with `--ocr --gate`; result saved to `data/metrics/`

**Kit cases:** gate passes — TC-DOC-01, 02, 06, 11, 12 (all must-redact removed), TC-DOC-03 (properties), TC-DOC-14, 15, 17 (no output). Must-keep items preserved (clinical dates, drug, eponyms, trial ID).

**Done when:** `deid_check --gate` passes. Thresholds and expected files are not loosened to get there.

### Step 5 — Scans, image-only and mixed PDFs

- [ ] Normalise images to PDF pages; orientation detection (Tesseract OSD) before OCR
- [ ] Dual extraction on every page (text layer + 300 dpi OCR), boxes mapped to PDF coordinates
- [ ] Image and scanned pages flattened with black boxes and rebuilt as image-only pages
- [ ] Verification re-OCRs the output

**Kit cases (measured, not gated):** TC-DOC-04, 07, 08, 09, 10, 13, 16. Target: beat the kit's oracle reference (`metrics/reference_oracle_run.txt`) on every scan, image-banner and rotated variant, without losing must-keep items. The text-PDF gate from step 4 still passes.

**Done when:** the rates are measured and saved, and they beat the oracle on image content.

### Step 6 — Site review, submission and handoff

- [ ] `adjud/signatures.py`: bcrypt demo-password check; signature records signer, meaning, time, hashes of signed files
- [ ] **Page 2**: case header with requested documents; upload with status (*processing / ready for review / blocked / verified*, plus *rejected* with the reason); page previews with outlined redactions; findings table; untick with reason; add terms; delete file; spreadsheet table view; each change re-runs redaction and verification for the affected file
- [ ] **Sign and submit** only when every file is verified; `submission.signed` → SUBMITTED
- [ ] Handoff manifest per spec §11 (nothing from `original/`), `handoff.flow2_ready`, notifications
- [ ] **Page 3**: case list by state; timeline with "chain verified ✓"; source data and eligibility; manifest and downloads; notifications; latest metrics

**Kit cases:** TC-DOC-M1–M4 (manual, plus automated tests where possible).

**Done when:** S5–S9 and S12 work in the UI.

### Step 7 — Demo hardening and rehearsal

- [ ] `tools/reset_demo.py`: fresh `data/` from the kit (config, EDC, first inbox file, documents ready to upload)
- [ ] `docker compose up` runs the full demo on a clean machine
- [ ] Short `README.md`: what this is, synthetic-data warning, how to run, demo script
- [ ] Demo script (§7) rehearsed end to end at least twice from a reset
- [ ] Final metrics run saved for page 3

**Done when:** the core demo runs from reset to the last scenario without touching code, in about 20 minutes, and the whole kit is green.

### Stretch steps (A–D)

Same pattern: deliverables, tests, the matching scenario, a fresh rehearsal, the whole kit still green. AI-1 (C) runs only with an approved key, only on synthetic data, through `adjud/ai/gateway.py`, and its value is shown by a harness run with AI off vs on.

---

## 7. Demo script (core)

Kit documents are named by case key; see the kit README table.

| Order | Scenario | Role | Kit input | What the audience sees |
|---|---|---|---|---|
| 1 | S1 Import mixed AE file | Study admin | `01_initial.csv` | 22 rows: 11 cases, 11 not eligible, rule-by-rule reasons (incl. fatal-before-dose, uncoded term) |
| 2 | S2 Import the same file again | Study admin | `02_reimport_identical.csv` | No new cases |
| 3 | S3 Import updates | Study admin | `03_updates.csv` | Changed, downgraded (flagged, not withdrawn), stale, new |
| 4 | S4 Manual AE entry | Safety Specialist | `manual_entry_valid.json` | Source MANUAL, reason in the log |
| 5 | S5 Text PDFs, review, submit | Site 101 | AE-001: D01, D02, D03 | Redactions in banner, table, footer; hidden spreadsheet PHI removed; signature; manifest |
| 6 | S6 Poor scans | Site 102 | AE-005: D08, D09 | OCR redaction; image-banner PHI found despite a clean text layer |
| 7 | S7 Residual caught | Site | any | Submit blocked; site adds the term; passes |
| 8 | S8 Untick a drug name | Site | any | Correction with reason in the timeline |
| 9 | Rejections | Site 104 | AE-007: D14, D15, D17, D16 | Encrypted/corrupt/unsupported rejected with reasons; mislabelled PNG handled |
| 10 | Metrics | Safety Specialist | harness run | Gate passed; recall per variant vs the oracle |
| 11 | S12 Tamper and rebuild | Study admin | — | `verify_log` detects the edit at its line; state rebuilt |

## 8. Success check before the demo

- [ ] Whole kit green: TC-RUL, TC-INT, TC-COR, TC-HAR (`-p tests.kit_plugin`)
- [ ] `deid_check --gate` passes (text PDFs 100%, no metadata/properties identifiers, rejected documents have no output)
- [ ] Scan, image-banner and rotated-page rates measured and beating the oracle reference
- [ ] No file can be submitted while verification finds a residual
- [ ] Every hash in the handoff manifest matches its file
- [ ] The core demo script runs from reset

## 9. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Presidio misses German names (English model) | D12 gate fails | German label rules first; add `de_core_news` model only if D12 still misses |
| Presidio over-flags clinical text | Must-keep items (dates, drug, eponyms) lost | Allow-list and `dob_only` policy; tune config, never the kit |
| OCR box alignment drifts on skewed/rotated scans | Box misses part of a word | Orientation detection, padded boxes; verification re-OCR blocks submission |
| Tesseract language packs differ between laptop and Docker | Different German results on the day | Rehearse only through `docker compose up` |
| Kit's `sys.path` loads its reference `adjud` | Kit tests silently test the wrong code | `tests/kit_plugin.py` pre-imports ours; a plugin test asserts `adjud.__file__` is in our package |
| Processing too slow for a live demo | Long waits | Measure time per page; pre-process large scans before the demo |
| Streamlit reruns cause double actions | Duplicate events | All writes go through `CaseStore`, which refuses illegal transitions |
