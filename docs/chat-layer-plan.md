# Chat layer: plan for LibreChat, and the options without a new server

**Status:** option A chosen for Friday and built (P-40, `ui/assistant.py`); LibreChat stays the option for after the demo. **Date:** 2026-11-17.
**Why:** in the demo review of 2026-11-16, management's view was that the demo "looks like conventional software" while they expect an AI-driven workflow. Most people know AI as a chat window (ChatGPT, Claude). The idea agreed there: a **guided chat** in front of the existing app. Users drive the workflow from the chat with slash actions, the chat reports progress, and for every manual step (upload, review, signature) it gives a link into the current app. Teams is blocked by IT and can't be ready by Friday; Slack is out because pharma users are on Teams. LibreChat (open source, MIT licence) was the suggested chat. Sources: the meeting notes, and [research/librechat-workflow-chat.md](research/librechat-workflow-chat.md) for every LibreChat fact below.

## 1. How it would look with LibreChat

Each person opens **one chat page** (LibreChat, branded *Tech Observer Adjudication Assistant*), logs in, and lands in the assistant. There is no model picker or other clutter. Four starter buttons (*My tasks*, *Case status*, *Import AE records*, *Explain a decision*) and typed slash actions start everything. The current app stays behind it, unchanged, for the steps a person must do.

```
Person ─► LibreChat: login, one assistant, starter buttons, slash actions
              │   asks our tools, saying who is logged in
              ▼
          adjud tool server (MCP) ─► the same case files, hash chain and runner as today
              │
              └─► answer in plain words + a link ─► current app page ─► upload / review / sign
```

### The demo, scene by scene

**Rahul Mehta, data manager**
> **Rahul:** /import
> **Assistant:** I imported the EDC export *01_initial.csv*: 4 records, **3 became cases** (DEMO01-0001 to 0003), **1 is not eligible**: AE-008 is not serious, and the study only adjudicates serious events. The sites have been e-mailed. [Open the import results →]

**Priya Nair, Larkfield General (site 101)**
> **Priya:** /my-tasks
> **Assistant:** You have 2 cases waiting for documents. **DEMO01-0001** (myocardial infarction, onset 14 Mar 2026) needs a discharge summary and an ECG report. [Upload documents →]
>
> *(she uploads in the app; the runner removes identifiers)*
>
> **Priya:** status of 0001
> **Assistant:** Identifiers were removed from your 3 files and checked again from scratch: nothing is left. Each file's type was recognised. It's waiting for your review and signature. [Review and sign →]

**Dr. Anna Weber, Safety Specialist**
> **Anna:** /my-tasks
> **Assistant:** 2 cases need your Quality Check.
> • **DEMO01-0001**: AI suggests **pass**. All required documents are there; event term, onset, end date and seriousness match the EDC; the outcome is *not sure* (the documents say "discharged home", the EDC says "recovered"). The narrative draft is ready. [Review and sign →]
> • **DEMO01-0003**: AI suggests a **query**: no discharge summary or ECG report. [Review →]

**Anyone**
> **Anna:** why is AE-008 not a case?
> **Assistant:** It doesn't meet the study's rules: *Serious: No*; only serious events are adjudicated. All other rules passed.

### What the chat does, and what it never does

| The chat does | The chat never does |
|---|---|
| Lists each person's tasks, for their role and site only | Sign, submit, pass a Quality Check, send a query, approve a narrative: these stay in the app behind a password ([ADR 0001](adr/0001-ai-is-decision-support-at-quality-check.md)) |
| Explains a case's status, what's next and who it waits for | Show another site's cases |
| Summarises the AI Suggestion and the checks | Read or send anything from the original, unredacted documents |
| Runs the import (a data-manager click today, no medical judgement) | Make a decision of any kind |
| Gives the link to the right page for every manual step | Replace the e-mails (they still go out, with the same links) |

## 2. What we would build

| Part | What it is |
|---|---|
| **LibreChat, self-hosted** | Its own Docker stack: the LibreChat app plus **MongoDB** (required, its own chat store). Search and file-search services turned off. Pinned release, registration off, four demo logins matching our demo people, Tech Observer branding, one locked assistant with instructions and starter buttons. Chat model: a small one (Haiku 5.5), because it only phrases our tools' answers |
| **Our tool server (MCP)** | A small Python service over `adjud/`, about 5 tools: `my_tasks`, `case_status`, `import_records`, `explain_eligibility`, `quality_check_summary`. Each returns plain facts and a link. It knows who is asking from the login (e-mail sent by LibreChat with a shared secret) and applies the same role and site rules as the app |
| **Audit** | Every tool call written to a hash-chained log; the import done from chat recorded as an event by that person, like the button today |
| **Docs and tests** | Decision P-40 in the plan, run-sheet scenes, tests for each tool without calling the API |

### Three rule conflicts that need a decision (P-40)

1. **MongoDB.** Our rules say no database server. Proposal: accept it as LibreChat's own store only, internal, holding no case data, with conversations not kept.
2. **AI calls outside our gateway.** LibreChat calls Claude itself, so chat turns miss our cost ledger, budget cap and recorded replays. Proposal: accept, because the chat model only phrases tool results and makes no case judgement. Every tool call is still logged by us.
3. **Chat history outside the audit trail.** Proposal: accept, because no decision happens in the chat; every action still lands as a case event.

## 3. Effort

| Work | Estimate |
|---|---|
| Check the unknowns first: LibreChat slash commands, the tool server's network check inside Docker, whether links come through clickable | 1–2 h |
| LibreChat running in Docker: pinned version, Claude key, registration off, four logins | 2–3 h |
| Assistant set-up: instructions, starter buttons, slash actions, branding, everything else hidden | 2–3 h |
| Tool layer: the 5 tools over the case store, role and site rules, links | 4–5 h |
| Tool server: HTTP service, login mapping, shared secret, Docker wiring | 2–3 h |
| Audit log for tool calls; import attributed to the person | 1–2 h |
| Tests for the tools (no API calls) | 2–3 h |
| Docs: P-40, run sheet, README | 1–2 h |
| Rehearsal in the browser and fixes | 2–3 h |
| **Total** | **≈ 17–26 hours: 2½–3 working days** |

**Against Friday:** Wednesday and Thursday are two working days, so it's tight. The earlier one-line estimate of 1½–2 days left out the assistant set-up, the audit log and the rehearsal.

**After the demo:** LibreChat releases every 1–2 weeks with config changes, so someone has to own upgrades. Real logins would need the company's sign-in (OIDC) instead of demo accounts: about +1 day. Real patient data would need IT and a restricted setup, as for the rest of the app.

**Risks, most likely first:** the assistant rewriting or dropping links; the tool server refusing LibreChat's calls inside Docker; LibreChat config changes between versions; the chat model's cost not being in our ledger.

## 4. Options without a new server

LibreChat always needs its own backend (the Node app and MongoDB). These options need **no new server** beyond the app we already run:

| Option | How it looks | Effort | Fits our rules? | Main drawback |
|---|---|---|---|---|
| **A. Chat page inside our app** | An *Assistant* page in the current app, built with Streamlit's chat components: ChatGPT-style, the same starter buttons and slash actions, links that switch to the right page. Claude is called through our own gateway | **≈ 1–1½ days** | Fully: our gateway, cost ledger, budget cap, recorded replays for the demo, audit log, existing roles | It's inside "the software", so less of a separate AI product. It can still be made the first screen everyone lands on |
| **B. Claude Desktop with our tools** | People chat in the Claude Desktop app; it starts our tool server itself on the same laptop (no hosting). Same tools as the LibreChat plan | **≈ 1 day** | Partly: Claude Desktop calls Claude itself, outside our ledger | Each user needs Claude Desktop and an account; it's Claude-branded; who is asking is set per laptop, not by login |
| **C. Teams messages via a Workflows webhook** | The runner posts each notification to a Teams channel or chat as a card with an "Open case" button | ≈ ½ day once IT allows it | Yes | One-way only, not a chat. Needs IT to allow the Workflows app; hospital users outside the company can't be reached ([research/teams-notifications.md](research/teams-notifications.md)) |
| **D. Sites reply to the e-mail with documents** | The runner reads replies in the mailbox and imports the attachments | ≈ 1 day for the demo (Mailpit) | Yes | Real mailbox access needs IT; still not a chat |

**The tool layer is the same in A, B and LibreChat.** `my_tasks`, `case_status` and the rest are plain Python over the case store. Build them once, and they can sit behind an in-app chat now and behind LibreChat (or Teams) later, without rework.

## 5. Recommendation

- **For Friday: option A, a chat page inside our app.** It gives the same guided-chat experience (starter buttons, slash actions, progress messages, links to manual steps) in about a day. It breaks none of our rules, and it can run on recorded answers so the demo is safe. It also leaves time to rehearse.
- **After Friday: LibreChat**, if the team wants a separate chat product. The tools built for A move behind an MCP server, and the three P-40 decisions are taken properly.
- **If management specifically wants to see LibreChat itself on Friday:** the plan in sections 1–3 is doable but leaves no slack. Start today, and keep option A or B as the fallback.
