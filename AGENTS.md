# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, etc.) working in this repository.

## Project

**Clinical Event Adjudication — Flow 1 (Intake) proof of concept:** a file-based workflow that takes a synthetic AE file, creates cases from configured eligibility rules (explaining every "no"), collects site documents, removes PHI destructively and verifies it by code, lets the site review and sign, and writes a hashed handoff manifest for Flow 2. Hash-chained audit trail throughout. AI is optional and off by default.

- **We are building Flow 1 only.** [docs/mvp-flow-1.md](docs/mvp-flow-1.md) is the source of truth for behavior. [docs/plan-mvp-flow-one.md](docs/plan-mvp-flow-one.md) is the step-by-step build plan: follow its step order, scope (core before stretch) and decisions table, and tick its boxes as steps complete.
- [docs/mvp-spec.md](docs/mvp-spec.md) (whole MVP) supplies the shared storage design (§4) that Flow 1 reuses; [docs/spec.md](docs/spec.md) is the full-product spec and the source of requirement IDs. Where they differ from mvp-flow-1.md, mvp-flow-1.md wins. Nothing from Flow 2 onwards is built.
- Read the relevant spec section before changing behavior. If code and spec disagree, flag it; don't silently pick one.
- Don't start UI polish before the store, workflow and their tests pass. Don't pull in the production stack from spec.md §8.3.

Stack: Python 3.12, Streamlit (UI), Pydantic + jsonschema, PyYAML, filelock, PyMuPDF, Tesseract via pytesseract, Microsoft Presidio + spaCy English model, bcrypt, Faker, pytest, Docker Compose, optional Mailpit, optional Anthropic Python SDK (version pinned). **No database server, no queue broker, no cloud service.**

## Working style

- The user's current request controls scope. Resolve routine choices from the specs and the existing code. Ask only when a missing answer would change the result. Open points with defaults are in MVP spec §15; use the default unless told otherwise.
- Work in small steps. After each behavior change, run the focused tests, fix failures before building on them, and commit.
- Inspect the current implementation before describing a feature as working. Scaffolds, plans and passing imports are not evidence that something works.
- Preserve unrelated changes. Don't refactor code outside the task.
- At each checkpoint, briefly report what changed, what was tested, the actual result, and any remaining limitation.

## Git workflow

- **Commit after every completed step** (a build step, a feature, a fix, a refactor, or a test addition). Don't wait to be asked.
- Keep each commit focused on one logical change. Use a clear message in the imperative mood, saying what changed and why, e.g. `Add FileCaseStore with locked, hash-chained appends`.
- Run the relevant tests before committing. Don't commit failing tests unless the commit message says why (e.g. a deliberately failing test added before its fix).
- Work on a feature branch for multi-step work. Don't commit directly to `main` once it has history beyond the initial commit.
- Check `git status` and the staged diff before each commit, so nothing unintended is included.
- **Commit and push after every file edited** (the user's standing instruction). If one file alone would break tests or imports, commit the smallest set of files that keeps the tree green. Push to `origin` immediately after each commit. When a build step's done criteria pass, merge its branch into `main` and push `main`.
- Never commit secrets, `.env*` files (except `.env.example`), API keys, virtualenvs, `__pycache__/`, generated runtime data under `data/` (cases, inbox, processed, outbox, exports, logs), or real documents of any kind. Committed config and seed inputs under `data/config/` are fine.
- Don't deploy or publish without the user's authorization. Never force-push or rewrite published history.

## Engineering practices

**Python**
- Type hints everywhere. Pydantic models for events, config and form definitions; JSON schemas for AI outputs. Don't duplicate a model as a loose dict.
- Use library types (Pydantic, PyMuPDF, Presidio, the Anthropic SDK) instead of redefining them.

**Correctness rules from the spec (don't weaken these):**
- **`events.jsonl` is the only source of truth for a case** (MVP §4.1). `case.json` is a cache that `tools/rebuild_state.py` must reproduce exactly. Never edit state directly.
- All storage goes through the `CaseStore` interface (§4.5). Workflow code never touches the filesystem directly, so a database can replace `FileCaseStore` later.
- Writing rules (§4.4): lock per case around every append; validate the transition against the workflow table before appending; write files first (temp file + atomic rename), then the event that references them by path and SHA-256; **never overwrite**: new versions get new names (e.g. a re-redacted file gets a new versioned name, and its report likewise).
- Every event carries `prev_hash` and `hash` (§4.3). `tools/verify_log.py` must detect any edited event or modified referenced file.
- **No AI in any handover.** State transitions come only from runner steps (deterministic code) or signed human actions. AI never appends a workflow transition, decides which findings are redacted, or signs.
- The full workflow must run with every AI switch off; AI-1 only adds findings, it never changes the workflow path.
- Eligibility rules are evaluated declaratively from YAML. **No `eval()`** or equivalent.
- Redaction is destructive: underlying text and pixels removed, metadata stripped, then text re-extracted and detection re-run. Any residual finding blocks submission.
- Every AE record evaluated, eligible or not, goes to the hash-chained intake log `data/intake/evaluations.jsonl` with each rule's result.
- Intake is idempotent on (study, subject, AE ID). A newer `last_modified` stores a new snapshot and raises `source.changed`; nothing is withdrawn automatically.
- **Sign and submit** is possible only when every file has passed verification. Signed submissions are immutable.
- The Flow 2 handoff manifest lists only redacted files and source snapshots with hashes, never anything from `documents/original/`.
- Notifications contain no clinical content: case ID, task and link only.
- Study behavior changes by editing config in `data/config/`, not code.

**Data and privacy**
- **Synthetic data only.** Never add, request or process real patient data. The UI keeps its permanent "Synthetic data only" banner.
- AI-1 (PHI detection by AI, mvp-flow-1.md §8) is the last stretch step of the plan. It sends **unredacted** page images to the provider, so it runs only on synthetic data, only with an approved key, and never as a replacement for the rule-based detectors.
- Don't claim the MVP is validated or Part 11 / Annex 11 compliant. It demonstrates the mechanics only. Blinding and roles are shown, not enforced; say so where it matters.

**Tests**
- Tests must be deterministic: seeded Faker/RNG, fixed or injected clock, stored AI outputs. **Tests never call the Claude API.**
- Never loosen a test, tolerance or threshold just to make it pass. If a test looks wrong, explain why and ask.
- Each demo scenario S1–S12 (mvp-flow-1.md §14) that is in scope should become an automated test where possible, and the set must stay green.
- `deid_check.py` measures removal of the generator's seeded identifiers. Text-layer PDFs must reach 100% with zero metadata residuals; scan, spreadsheet and DICOM rates are measured and reported, not tuned to look good.

**AI calls (optional, only once API access is approved)**
- All calls go through `adjud/ai/gateway.py`. No other module imports the Anthropic SDK.
- Switches live in `study.yaml` and are all off by default. The model ID is pinned there and prompts are versioned files in `adjud/ai/prompts/`. Don't hard-code either elsewhere.
- The API key comes only from an environment variable. Never write it to `data/`, logs or the repository.
- Validate every output against its JSON schema, up to 2 retries, then continue without AI and tell the user. Verify citations in code; mark unverifiable findings "unverified".
- Save every call to `cases/<id>/ai/` (function, model, prompt version, input hash, output, valid, latency), including failures, refusals and timeouts. Never drop them. The human's accept/edit/reject decision is an event.
- Ask before running anything that makes more than ~10 real API calls (e.g. `tools/deid_check.py` with AI-1 on).

**Code hygiene**
- Small modules matching the layout in MVP spec §6.2 and the plan (`adjud/`, `ui/`, `tools/`, `tests/`). Match the surrounding code's naming and comment density.
- Handle errors explicitly. Log useful context, never secrets, PHI or full document text.
- Run the relevant tests before each commit. Read your own final diff before reporting a step as done.

## Commands

Python 3.12 managed by `uv` (P-08 in the plan). Tools run as modules from the repo root. Data lives in `./data` unless `ADJUD_DATA_DIR` is set.

| Task | Command |
|---|---|
| Install (incl. spaCy model from `uv.lock`) | `uv sync` |
| Run everything in Docker (UI on :8501, runner) | `docker compose up --build` |
| UI | `uv run streamlit run ui/app.py` |
| Runner (loop / one pass) | `uv run python -m adjud.runner` / `uv run python -m adjud.runner --once` |
| Tests | `uv run pytest` |
| Single test | `uv run pytest tests/<file>.py -k "<test name>"` |
| **Flow 1 test kit against our package** (acceptance) | `uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests` |
| Kit, fast subset (no OCR, no regeneration) | `uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests -m "not ocr and not slow"` |
| Single kit test case | `uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests -k "int04"` |
| Lint / format | `uv run ruff check .` / `uv run ruff format .` |
| Verify hash chains and file hashes | `uv run python -m tools.verify_log` |
| Rebuild all `case.json` from event logs (`--check` to compare only) | `uv run python -m tools.rebuild_state` |
| Measure seeded-PHI removal (real API calls if AI-1 is on; ask first) (step 4) | `uv run python -m tools.deid_check` |
| Reset the demo (wipes runtime data, loads kit EDC; `--preload` puts `01_initial.csv` in the inbox) | `uv run python -m tools.reset_demo` |

Before each commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest`, plus the kit cases the change touches. After each plan step, run the whole kit.

Notes:
- **The test kit (`data/flow1-testkit/`) is the acceptance test. Never edit it to make a test pass.** Its conftest would load the kit's own reference `adjud`; always run it with `-p tests.kit_plugin`, which pins our package. If a kit decision should change, record it in the plan's decisions table first.
- Tesseract is a system dependency with English and German (`brew install tesseract tesseract-lang` locally; in the Docker image already). The spaCy model `en_core_web_lg` is a pinned dependency in `pyproject.toml`, so `uv sync` installs it.
- `./data` is mounted into the containers. Only `data/config/` is committed; everything else under `data/` is runtime output.

## Secrets

- `ANTHROPIC_API_KEY` (only if AI access is approved) and any SMTP settings live in environment variables or an untracked `.env`. Keep `.env.example` updated with variable **names only**.
- Demo user passwords are stored only as bcrypt hashes in `data/config/users.yaml`.
