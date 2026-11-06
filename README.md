# Clinical Event Adjudication — Flow 1 (Intake) proof of concept

> **SYNTHETIC DATA ONLY.** Every patient, identifier, hospital and drug in this repository is invented. This is a proof of concept. It is not validated, not 21 CFR Part 11 / Annex 11 compliant, and has no real authentication. Never use it with real patient data.

This POC covers Flow 1 of the adjudication workflow ([spec](docs/mvp-flow-1.md)). Adverse-event (AE) records from a mock EDC become adjudication cases when they meet the study's rules, and every record gets a rule-by-rule explanation. Sites upload source documents. The system removes PHI destructively and checks the output again from scratch. The site reviews, corrects and signs. The result is a hashed handoff package for Flow 2.

There is no database. Each case is a folder whose single source of truth is a hash-chained, append-only event log.

## Run it

With Docker (Tesseract with English and German is built in):

```bash
docker compose run --rm runner python -m tools.reset_demo --preload   # fresh demo data
docker compose up                                                        # UI on http://localhost:8501
```

Without Docker you need Python 3.12, [uv](https://docs.astral.sh/uv/) and Tesseract (`brew install tesseract tesseract-lang`):

```bash
uv sync
uv run python -m tools.reset_demo --preload
uv run python -m adjud.runner &           # background worker: inbox import, de-identification, handoff
uv run streamlit run ui/app.py
```

Measure de-identification against the test kit (about 30 s):

```bash
uv run python -m tools.deid_check
```

More commands are in [AGENTS.md](AGENTS.md).

## What you can see

| Page | Who | What |
|---|---|---|
| 1 · EDC input | Study admin, Safety Specialist | Import AE files (CSV/XLSX) and see every record's eligibility with rule-by-rule reasons. Also manual entry and flagged cases (source changed, no longer eligible, possible duplicate). |
| 2 · Site documents | Site user | Upload files and check their status (verified, blocked or rejected with a reason). Review redacted pages, untick a wrong finding (a reason is required), add a term, delete a file, and **Sign and submit** (password `demo`). |
| 3 · Results | Everyone | Case list, timeline with "chain verified ✓", source data, notifications, the Flow 2 manifest with downloads, and de-identification metrics compared with the kit's oracle. |

Roles are simulated in the sidebar. Anyone can switch, which is acceptable only because the data is synthetic.

## Demo script

Files to drag into the UI are in `data/demo_uploads/` after a reset. The full script is in [the plan](docs/plan-mvp-flow-one.md#7-demo-script-core).

1. **Import** `01_initial.csv` with *Import now*: 22 records give 11 cases and 11 not eligible, each with reasons.
2. **Re-import** `02_reimport_identical.csv`: no new cases.
3. **Import updates** `03_updates.csv`: a changed case, a downgraded case (flagged, not withdrawn), a stale row and new cases.
4. **Manual entry** with a reason.
5. **Site 101, case AE-001:** upload the discharge summary, ECG report and troponin spreadsheet. Press *Process now*, review, then sign and submit.
6. **Site 102, case AE-005:** a poor fax scan and a letter whose identifiers sit only in an image banner. Both are removed by OCR.
7. **Site 104, case AE-007:** German documents. The encrypted, corrupt and `.txt` files are rejected with reasons, and a PNG named `.pdf` is handled.
8. **Untick** a wrongly flagged name with a reason. Only its ID and masked text reach the log.
9. **Metrics** on page 3: the gate passes, and every scan variant beats the oracle.
10. **Tamper and rebuild:** edit a line of a case's `events.jsonl`, then run `uv run python -m tools.verify_log`. It reports the line. Delete the `case.json` files and run `uv run python -m tools.rebuild_state`.

## Results (synthetic test kit, OCR on)

| | Must-redact removed | Must-keep preserved |
|---|---|---|
| Text PDFs (incl. German) | 41/41 | 28/28 |
| Spreadsheet (hidden sheet and column, comments, properties) | 8/8 | 3/3 |
| Scans, image-only, rotated, mixed and mislabelled images | 100% on every variant (oracle 0–40%) | 3 items scored "lost" by the harness's own OCR, but intact on visual check |
| Encrypted, corrupt and unsupported files | rejected, no output | — |
| DICOM | not yet (stretch step B) | — |

## Layout

```text
adjud/            our implementation
  store.py        hash-chained case logs and files (no database), CaseStore interface
  workflow.py     Flow 1 states and transitions; state is replayed from events
  intake.py       AE import, eligibility, idempotency, change detection, EDC lookups
  rules.py        declarative eligibility rules (no eval)
  deid/           detection, PDF/XLSX/image redaction, OCR, verification
  documents.py    de-identification runs inside the case workflow
  review.py       site corrections, submission gate, signing
  handoff.py      Flow 2 manifest
  runner.py       the only background process
ui/               Streamlit pages
tools/            reset_demo, verify_log, rebuild_state, run_corpus, deid_check
data/config/      study configuration (rules, policies, demo users)
data/flow1-testkit/  synthetic test kit: corpus, ground truth, scoring harness, acceptance tests
docs/             specs, test cases, build plan
```

## Tests

```bash
uv run pytest                                                              # our tests
uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests        # the kit's 53 tests, run against our code
```
