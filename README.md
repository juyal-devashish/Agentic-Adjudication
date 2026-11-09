# Clinical Event Adjudication — Flows 1 and 2, proof of concept

> **SYNTHETIC DATA ONLY.** Every patient, identifier, hospital and drug in this repository is invented. This is a proof of concept. It is not validated, not 21 CFR Part 11 / Annex 11 compliant, and has no real authentication. Never use it with real patient data.

This POC covers Flow 1 (intake) and Flow 2 (quality check and narrative) of the adjudication workflow, built so that **agents do the work, code checks every agent answer, and people sign and handle exceptions**. Adverse-event (AE) records from a mock EDC become cases when they meet the study's rules, with a rule-by-rule explanation for every record. The site drops one ZIP in the case's folder. Identifiers are removed destructively and checked again from scratch; the site reviews, corrects and signs. Then quality checks run, agents settle what code cannot tell, a hashed dossier is built, an agent writes a cited narrative, and the case is handed to adjudication. Step by step, with who does what and where AI is used: [docs/process.md](docs/process.md). Build plan: [docs/plan-mvp.md](docs/plan-mvp.md).

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
uv run python -m adjud.runner &           # background worker: imports, upload folders, de-identification, QC, narrative, handoffs
uv run streamlit run ui/app.py
```

E-mail notifications are off until SMTP is set in `.env` (see `.env.example`). To see them locally, run `docker compose --profile mail up`, set `SMTP_HOST=mailpit` (or `localhost` outside Docker) and `SMTP_PORT=1025`, and open the inbox on http://localhost:8025. For Outlook, use `smtp.office365.com`, port 587 and an app password, if your tenant allows it; `SMTP_REDIRECT_TO` sends every message to one address for a demo.

AI is off by default (`ai.mode` in `data/config/DEMO01/study.yaml`). `replay` returns recorded answers, shown as "Simulated AI" when they were written by hand (`tools/simulate_ai.py` writes them for the demo cases); `live` calls the API with `ANTHROPIC_API_KEY` from the environment, a small pinned model (`claude-haiku-5-5`) and a budget cap. `ADJUD_AI_MODE=replay` (or `live`) in `.env` switches the mode without editing config. Every call, its tools, tokens and cost are on the case record. With AI off, the steps that need judgement go to the Safety Specialist.

Sites can put one ZIP per case in `data/uploads/<case number>/` (or under `ADJUD_UPLOAD_DIR`, e.g. a OneDrive-synced folder) instead of using the Documents page.

Measure de-identification against the test kit (about 30 s):

```bash
uv run python -m tools.deid_check
```

More commands are in [AGENTS.md](AGENTS.md).

## What you can see

| Page | Who | What |
|---|---|---|
| AE import | Data manager | Import AE files (CSV/XLSX) and see why each record did or did not become a case. Also manual entry, and cases to check (AE changed, no longer meets the rules, possible duplicate). |
| Documents | Site user | Each case shows its progress and the next step. Upload files and see their status (verified, needs attention, or rejected with a reason). Review redacted pages, keep a wrongly blacked-out term (a reason is required), black out more text, remove a file, and **Sign and submit** (password `demo`). |
| Quality check | Safety Specialist | The QC checks, what the AI proposed and whether code accepted it, the dossier, and the narrative. Decisions and signatures only when code and agents could not decide: pass, send a query to the site, complete and sign the narrative. |
| Case record | Everyone | The packages for Flow 2 and for adjudication with downloads, the audit trail with its integrity check, the EDC source data in plain words, notifications, AI activity and cost, and how well identifiers are removed on the test kit. |

Direct links preselect the role, user and case, for example `http://localhost:8501/documents?role=site&user=site104.coord&case=DEMO01-0007`.

Roles are simulated in the sidebar. Anyone can switch, which is acceptable only because the data is synthetic.

## Demo script

Reset with `--preload`, set `ADJUD_AI_MODE=replay` in `.env`, then `docker compose up`. Files are in `data/demo_uploads/` after a reset; `tests/test_demo_script.py` rehearses the core of it automatically.

1. **AE import** (Data manager): the queued `01_initial.csv` gives 11 cases and 11 records that are not eligible, each with its reasons. **Pull AE records now** brings in the EDC's newer records; pulling again changes nothing.
2. **Site 101, case DEMO01-0001:** zip the three files from `documents/DEMO01-101-0001_AE-001/` into `data/uploads/DEMO01-0001/`. Within a minute the identifiers are removed. On *Documents*: review the pages, drag a box over anything to hide, sign with `demo`.
3. **Nobody else acts:** the quality checks pass, the dossier is built, Simulated AI writes the narrative, code finds every quote on its dossier page, and the case is ready for adjudication. *Quality check* and *Case record* show each step, who or what did it, and the cost.
4. **Site 102, case DEMO01-0005:** the GP letter has no title code can read. The agent types it, its quote is found on the page, and the case passes as well.
5. **Exceptions:** the scenarios in `flow2/` (upload one into a fresh case DEMO01-0001) show an automatic query for a missing ECG, a document about another patient, an unreadable scan, and a different event that the agent spots. *Quality check* as Safety Specialist shows a case code could not decide.
6. **Flow 1 details** as before: rejected files (site 104), unticking a wrong redaction with a reason, measurements on *Case record*.
7. **Tamper and rebuild:** edit a line of a case's `events.jsonl`, run `uv run python -m tools.verify_log` (it names the line); delete the `case.json` files and run `uv run python -m tools.rebuild_state`.

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
  workflow.py     Flow 1 and 2 states and transitions; state is replayed from events
  intake.py       AE import, eligibility, idempotency, change detection, EDC lookups
  rules.py        declarative eligibility rules (no eval)
  deid/           detection, PDF/XLSX/image redaction, OCR, verification
  documents.py    de-identification runs inside the case workflow
  review.py       site corrections, submission gate, signing
  handoff.py      Flow 2 manifest
  upload_folder.py  per-case upload folders, one ZIP
  qc.py           deterministic quality checks
  flow2.py        QC round, agents, queries, narrative, Flow 3 handoff
  dossier.py      hashed adjudication dossier
  ai/             gateway (the only API client), local tools, gates, prompts, functions
  mailer.py       e-mail for queued notifications
  runner.py       the only background process
ui/               Streamlit pages
tools/            reset_demo, verify_log, rebuild_state, run_corpus, deid_check, screenshots,
                  make_flow2_fixtures, simulate_ai
data/config/      study configuration (rules, policies, demo users)
data/flow1-testkit/  synthetic test kit: corpus, ground truth, scoring harness, acceptance tests
docs/             process, build plan, specs
```

## Tests

```bash
uv run pytest                                                              # our tests
uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests        # the kit's 53 tests, run against our code
```
