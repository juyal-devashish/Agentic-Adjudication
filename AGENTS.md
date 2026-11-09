# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, etc.) working in this repository.

## Project

**Clinical Event Adjudication — Flow 1 (Intake) + Flow 2 (QC and narrative) proof of concept, built around AI:** a file-based workflow that takes a synthetic AE file, creates cases from configured eligibility rules (explaining every "no"), collects site documents, removes PHI destructively and verifies it by code, lets the site review and sign, then has AI agents run QC, build the dossier and draft the narrative, and hands a hashed package to Flow 3. Hash-chained audit trail throughout. **The goal is to minimise the people needed:** agents do the work, code gates decide whether their results may move a case, and people handle signatures and exceptions.

- **We are building Flows 1 and 2.** [docs/plan-mvp.md](docs/plan-mvp.md) is the step-by-step build plan and holds the autonomy model (§2) and the decisions table: follow its step order and tick its boxes as steps complete. Flow 1 is built; its behavior is defined by the code, its tests and the test kit.
- For Flow 2, [docs/mvp-spec.md](docs/mvp-spec.md) (§4 storage, §5.2 states, §8 MVP-11 to MVP-15, §9 AI) and [docs/spec.md](docs/spec.md) (§6.3–6.4, requirement IDs) describe the behavior. Where they differ from the plan's autonomy model or decisions, the plan wins. Nothing from Flow 3 onwards is built.
- Read the relevant spec section before changing behavior. If code and spec disagree, flag it; don't silently pick one.
- Don't start UI polish before the store, workflow and their tests pass. Don't pull in the production stack from spec.md §8.3.

Stack: Python 3.12, Streamlit (UI), Pydantic + jsonschema, PyYAML, filelock, PyMuPDF, Tesseract via pytesseract, Microsoft Presidio + spaCy English model, bcrypt, Faker, pytest, Docker Compose, SMTP via `smtplib` (Mailpit or Outlook for the demo), Anthropic Python SDK (version pinned, used only through the gateway). **No database server, no queue broker, no cloud service.**

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
- **Agents act, code gates decide, people handle exceptions** (plan §2). An agent's result moves a case only when a deterministic gate passes it (schema valid, every citation verified, confidence at or above the configured threshold, every deterministic check passed); the runner then appends the transition with actor `agent:<function>` and the AI call ID. Otherwise a person gets an exception task. Agents act through local tools and never write files or events directly.
- An automated decision is an event, **never an electronic signature**. The site's submission stays a human signature.
- AI can add PHI findings, never remove them. Post-redaction verification is deterministic and blocking.
- The full workflow must still complete with AI off: steps that need judgement become human tasks.
- Eligibility rules are evaluated declaratively from YAML. **No `eval()`** or equivalent.
- Redaction is destructive: underlying text and pixels removed, metadata stripped, then text re-extracted and detection re-run. Any residual finding blocks submission.
- Every AE record evaluated, eligible or not, goes to the hash-chained intake log `data/intake/evaluations.jsonl` with each rule's result.
- Intake is idempotent on (study, subject, AE ID). A newer `last_modified` stores a new snapshot and raises `source.changed`; nothing is withdrawn automatically.
- **Sign and submit** is possible only when every file has passed verification. Signed submissions are immutable.
- The Flow 2 handoff manifest lists only redacted files and source snapshots with hashes, never anything from `documents/original/`. Flow 2 reads only that manifest. The Flow 3 manifest likewise lists only the dossier, narrative and QC results with hashes.
- Notifications contain no clinical content: case ID, task and link only.
- Study behavior changes by editing config in `data/config/`, not code.

**Data and privacy**
- **Synthetic data only.** Never add, request or process real patient data. The UI keeps its permanent "Synthetic data only" banner.
- AI-1 (AI finds PHI on scans and images) sends **unredacted** page images to the provider, so it runs only on synthetic data and never as a replacement for the rule-based detectors. Real data would need IT input and a restricted production setup.
- Don't claim the MVP is validated or Part 11 / Annex 11 compliant. It demonstrates the mechanics only. Blinding and roles are shown, not enforced; say so where it matters.

**Tests**
- Tests must be deterministic: seeded Faker/RNG, fixed or injected clock, stored AI outputs. **Tests never call the Claude API.**
- Never loosen a test, tolerance or threshold just to make it pass. If a test looks wrong, explain why and ask.
- Each demo scenario should become an automated test where possible, and the set must stay green. Agent behavior is tested in replay mode (stored outputs keyed by input hash).
- `deid_check.py` measures removal of the generator's seeded identifiers. Text-layer PDFs must reach 100% with zero metadata residuals; scan, spreadsheet and DICOM rates are measured and reported, not tuned to look good.

**AI calls**
- All calls go through `adjud/ai/gateway.py`. No other module imports the Anthropic SDK.
- The mode (`off` / `replay` / `live`), the per-function switches, the pinned model (a small model by default) and the gate thresholds live in `study.yaml`; prompts are versioned files in `adjud/ai/prompts/`. Don't hard-code any of them elsewhere. `live` without a key falls back to `off` and says so.
- Replay outputs written by hand are labelled *simulated* in events and in the UI until real outputs are recorded.
- Log tokens and cost for every call in `data/ai/usage.jsonl`; respect the budget cap in `study.yaml`.
- The API key comes only from an environment variable. Never write it to `data/`, logs or the repository.
- Validate every output against its JSON schema, up to 2 retries, then continue without AI and tell the user. Verify citations in code; mark unverifiable findings "unverified".
- Save every call to `cases/<id>/ai/` (function, mode, model, prompt version, input hash, output, valid, tokens, cost, latency), including failures, refusals and timeouts. Never drop them. The gate result is an event, and so is any human override.
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
| Reset demo data in Docker | `docker compose run --rm runner python -m tools.reset_demo --preload` |
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
| Measure de-identification on the kit corpus with the kit's harness and gate (~30 s; real API calls if AI-1 is on: ask first) | `uv run python -m tools.deid_check` |
| Run only our pipeline over the kit corpus (outputs + per-file reports) | `uv run python -m tools.run_corpus` |
| Reset the demo (wipes runtime data, loads kit EDC; `--preload` puts `01_initial.csv` in the inbox) | `uv run python -m tools.reset_demo` |

Before each commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest`, plus the kit cases the change touches. After each plan step, run the whole kit.

Notes:
- **The test kit (`data/flow1-testkit/`) is the acceptance test. Never edit it to make a test pass.** Its conftest would load the kit's own reference `adjud`; always run it with `-p tests.kit_plugin`, which pins our package. If a kit decision should change, record it in the plan's decisions table first.
- Tesseract is a system dependency with English and German (`brew install tesseract tesseract-lang` locally; in the Docker image already). The spaCy model `en_core_web_lg` is a pinned dependency in `pyproject.toml`, so `uv sync` installs it.
- `./data` is mounted into the containers. Only `data/config/` is committed; everything else under `data/` is runtime output.

## Secrets

- `ANTHROPIC_API_KEY` (only if AI access is approved) and any SMTP settings live in environment variables or an untracked `.env`. Keep `.env.example` updated with variable **names only**.
- Demo user passwords are stored only as bcrypt hashes in `data/config/users.yaml`.
