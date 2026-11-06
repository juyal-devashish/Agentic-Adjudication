# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, etc.) working in this repository.

## Project

**Clinical Event Adjudication — MVP (proof of concept):** a file-based workflow that takes a synthetic AE file, creates adjudication cases from configured eligibility rules, de-identifies and QCs source documents, runs blinded dual adjudication with Adjudicator 3 and consensus, and produces a traceable outcome with a hash-chained audit trail. AI is optional and off by default.

- **[docs/mvp-spec.md](docs/mvp-spec.md) is the source of truth for what we build now.** [docs/spec.md](docs/spec.md) is the parent (full product) spec; requirement IDs in the MVP spec's "Maps to" columns point into it. Read the relevant section before changing behavior. If code and spec disagree, or the two specs disagree, flag it; don't silently pick one.
- Build in the order of MVP spec §14. Steps 1–6 must not depend on AI access. Don't start UI polish before the store, workflow and their tests pass.
- Where the MVP spec simplifies spec.md (files instead of PostgreSQL, a runner loop instead of a job queue, a role switcher instead of real auth), follow the MVP spec. Don't pull in the production stack from spec.md §8.3.

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
- Writing rules (§4.4): lock per case around every append; validate the transition against the workflow table before appending; write files first (temp file + atomic rename), then the event that references them by path and SHA-256; **never overwrite**: new versions get new names (`run-02.json`, `dossier-v2.pdf`, `rounds/r2/`).
- Every event carries `prev_hash` and `hash` (§4.3). `tools/verify_log.py` must detect any edited event or modified referenced file.
- **No AI in any handover.** State transitions come only from runner steps (deterministic code) or signed human actions. AI never appends a workflow transition, fills a signed field, makes a QC or adjudication decision, or signs.
- The full workflow must run with every AI switch off. Scenario S2 and S8 must follow the same workflow path.
- Eligibility rules are evaluated declaratively from YAML. **No `eval()`** or equivalent.
- Redaction is destructive: underlying text and pixels removed, metadata stripped, then text re-extracted and detection re-run. Any residual finding blocks submission.
- Signed forms, dossiers and outcomes are immutable once signed. Re-adjudication creates a new round; the old outcome is superseded by an event, its files untouched.
- Every final outcome value records its source file, field, signer and signature time.
- Notifications contain no clinical content: case ID, task and link only.
- Study behavior changes by editing config in `data/config/`, not code.

**Data and privacy**
- **Synthetic data only.** Never add, request or process real patient data. The UI keeps its permanent "Synthetic data only" banner.
- **Never send unredacted content to an AI provider.** AI-1 (PHI detection by AI) is out of scope for the MVP.
- Don't claim the MVP is validated or Part 11 / Annex 11 compliant. It demonstrates the mechanics only. Blinding and roles are shown, not enforced; say so where it matters.

**Tests**
- Tests must be deterministic: seeded Faker/RNG, fixed or injected clock, stored AI outputs. **Tests never call the Claude API.**
- Never loosen a test, tolerance or threshold just to make it pass. If a test looks wrong, explain why and ask.
- Each demo scenario S1–S10 (MVP §12.2) should become an automated test where possible, and the set must stay green.
- `deid_check.py` measures removal of the generator's seeded identifiers. Text-layer PDFs must reach 100%; the scanned-image rate is measured and reported, not tuned to look good.
- Rerunning the same model is a repeatability measurement (MVP §9.3), not independent validation.

**AI calls (optional, only once API access is approved)**
- All calls go through `adjud/ai/gateway.py`. No other module imports the Anthropic SDK.
- Switches live in `study.yaml` and are all off by default. The model ID is pinned there and prompts are versioned files in `adjud/ai/prompts/`. Don't hard-code either elsewhere.
- The API key comes only from an environment variable. Never write it to `data/`, logs or the repository.
- Validate every output against its JSON schema, up to 2 retries, then continue without AI and tell the user. Verify citations in code; mark unverifiable findings "unverified".
- Save every call to `cases/<id>/ai/` (function, model, prompt version, input hash, output, valid, latency), including failures, refusals and timeouts. Never drop them. The human's accept/edit/reject decision is an event.
- Ask before running anything that makes more than ~10 real API calls (e.g. `tools/ai_repeatability.py`).

**Code hygiene**
- Small modules matching the layout in MVP spec §6.2 (`adjud/`, `ui/`, `tools/`, `tests/`). Match the surrounding code's naming and comment density.
- Handle errors explicitly. Log useful context, never secrets, PHI or full document text.
- Run the relevant tests before each commit. Read your own final diff before reporting a step as done.

## Commands

The repository currently holds only the specs. These are the commands the MVP spec defines; update this table (and add install, lint and type-check commands) as the project is scaffolded.

| Task | Command |
|---|---|
| Run everything (UI, runner, optional Mailpit) | `docker compose up` |
| UI | `streamlit run ui/app.py` |
| Runner | `python -m adjud.runner` |
| Generate synthetic data | `python tools/make_synthetic.py` |
| Rebuild all `case.json` from event logs | `python tools/rebuild_state.py` |
| Verify hash chains and file hashes | `python tools/verify_log.py` |
| Measure seeded-PHI removal | `python tools/deid_check.py` |
| AI repeatability (real API calls; ask first) | `python tools/ai_repeatability.py` |
| Tests | `pytest` |
| Single test | `pytest tests/<file>.py -k "<test name>"` |

Notes:
- Tesseract and the spaCy English model are installed separately from the Python packages; the Docker image should include both.
- `./data` is mounted into the containers as a volume.

## Secrets

- `ANTHROPIC_API_KEY` (only if AI access is approved) and any SMTP settings live in environment variables or an untracked `.env`. Keep `.env.example` updated with variable **names only**.
- Demo user passwords are stored only as bcrypt hashes in `data/config/users.yaml`.
