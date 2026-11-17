# LibreChat as a guided workflow chat over `adjud/`

**Checked on:** 2026-11-17.
**LibreChat versions read:** the source at `main` HEAD [`9529c99`](https://github.com/LibreChat-AI/LibreChat/tree/9529c99f5ebd5469942feb2c83a785894f48522b) (2026-11-16, `package.json` says `v0.8.9-rc1`, config schema `1.3.18`), and the latest stable tag `v0.8.8` (2026-09-30, config `1.3.17`). Every feature this note relies on (MCP `requestHeaders`, user placeholders, `toolApproval`, model specs with `mcpServers`, `conversation_starters`, `showInMenu` and `softDefault`, and the `provider: anthropic` custom endpoint) is present in `v0.8.8` as well as HEAD (checked with `git grep` on the tag). Release dates come from the [changelog](https://www.librechat.ai/changelog).
**Other versions:** MCP spec `2025-06-18` transports page, MCP Python SDK `mcp` 2.3.0 on PyPI (v2 API, `MCPServer`), FastAPI 0.163.1 on PyPI.
**Repository:** `github.com/danny-avila/LibreChat` now returns 301 to [`github.com/LibreChat-AI/LibreChat`](https://github.com/LibreChat-AI/LibreChat), and the docs clone from there ([Docker guide](https://www.librechat.ai/docs/local/docker)). Links below use the canonical path pinned to commit `9529c99` (shortened to **`LC@9529c99`**).

Nothing here has been run. It is a reading of docs and source. The time estimates in §5 are my judgement and have no source.

---

## Short answer

**Yes, technically, and MCP is the right integration, not Actions.** A small Python MCP server (streamable HTTP), mounted next to a thin FastAPI over `adjud/`, can expose 2–3 workflow tools. LibreChat sends it the logged-in user through header placeholders such as `{{LIBRECHAT_USER_EMAIL}}`. A YAML model spec pins users to one "Workflow assistant" with fixed instructions, that MCP server and conversation starters. Links come back as plain text in the tool result. They become clickable only when the model repeats them as Markdown in its reply, which LibreChat renders as `target="_blank"` anchors.

**It does conflict with three of our rules**, and each needs a plan decision before a demo:
1. LibreChat makes its own Anthropic calls, outside `adjud/ai/gateway.py`, so they have no ledger, budget cap, replay mode or per-case AI log.
2. LibreChat requires MongoDB, a database server.
3. Chat transcripts live in Mongo, outside the hash chain.

Signing stays human only if the MCP server simply has no signing tools and returns deep links to the Streamlit sign pages.

**The chat would mostly report status and navigate, not trigger steps.** Our transition table ([`adjud/workflow.py`](../../adjud/workflow.py) `TRANSITIONS`) leaves little for a chat to trigger:
- The runner already drives every automatic state, including `qc.started`, `qc.passed`, `dossier.built` and `narrative.approved`.
- Most remaining human transitions are signatures (`submission.signed`, `qc.signed_pass`, `narrative.signed`, `query.opened` after review) or need a visual UI (`documents.uploaded`, `redactions.changed`).
- The few human, non-signature writes a chat could reasonably do are `document.labelled` (the site corrects a document type), `document.deleted`, `case.flagged` and possibly `case.withdrawn`. Withdrawal is a business decision, so it would need a plan decision.

So the realistic value is "tell me what's waiting for me, explain why, take me there".

A demo is realistic in **about 1.5–2 working days** (my estimate).

---

## 1. Actions vs MCP vs Agents

### What each is and where it is configured

| | Configured in | Notes |
|---|---|---|
| **Actions** (OpenAPI) | **UI only**, inside an agent: Agent Builder → Tools → Create new → Actions, then paste an OpenAPI schema. Each operation becomes a tool ([Agents docs](https://www.librechat.ai/docs/features/agents)) | Stored per agent in MongoDB. No YAML way to define one was found. Domain allow-listing is in YAML (`actions.allowedDomains` / `allowedAddresses`, [LC@9529c99 `librechat.example.yaml` L636–662](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L636-L662)) |
| **MCP servers** | **YAML** `mcpServers:` (admin, needs a restart), or the UI "Add MCP" dialog for users with permission ([MCP docs](https://www.librechat.ai/docs/features/mcp)) | UI-created servers are limited to `streamable-http`/`sse`, and user and env placeholders are stripped from their headers ([mcp_servers docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers)). **Use YAML.** |
| **Agents** | **UI** (Agent Builder), stored in Mongo. They can be shared with users, groups or roles as Viewer, Editor or Owner ([Agents docs](https://www.librechat.ai/docs/features/agents)) | An agent bundles a model, instructions, tools (MCP and/or Actions) and up to 4 conversation starters |
| **Model specs** | **YAML** `modelSpecs:` ([model_specs docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs)) | Config-as-code alternative to an agent: a preset with an endpoint, model and system prompt, plus `mcpServers: [...]` and `conversation_starters`. A spec can also point at a UI-built agent with `preset.agent_id` |

### MCP transports

- The LibreChat schema accepts `stdio`, `websocket`, `sse` and `streamable-http`, with `http` as an alias ([LC@9529c99 `packages/data-provider/src/mcp.ts` L427, L474, L492, L527](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/mcp.ts#L527)). The docs recommend `streamable-http` for multi-user deployments ([mcp_servers docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers)).
- The MCP spec defines only stdio and Streamable HTTP. It says Streamable HTTP replaces the old HTTP+SSE transport. Streamable HTTP servers MUST validate `Origin`, and SHOULD bind to localhost when local and SHOULD authenticate connections ([MCP transports 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)).
- `stdio` would mean LibreChat's Node container launching our Python process. This is not practical without a custom image (see Unconfirmed), so use **streamable HTTP**.
- **SSRF block:** remote MCP and Action targets on private IPs, localhost and `.internal` hosts are blocked by default. A Docker service name such as `adjud-mcp:8000` must be listed in `mcpSettings.allowedAddresses` (`host:port`) ([LC@9529c99 `librechat.example.yaml` L686–715](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L686-L715)).

### Do tool results render as clickable links?

**Indirectly.**
- Raw tool output is shown in a collapsible tool-call panel as `<pre>`/`<code>` text, not as rendered Markdown ([LC@9529c99 `client/.../ToolOutput/OutputRenderer.tsx` L81–108](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/client/src/components/Chat/Messages/Content/ToolOutput/OutputRenderer.tsx#L81-L108)).
- The assistant's own reply is Markdown. Its `<a>` renderer opens links with `target="_blank"` ([LC@9529c99 `MarkdownComponents.tsx` L117–147](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/client/src/components/Chat/Messages/Content/MarkdownComponents.tsx#L117-L147)). I found no custom `urlTransform` in the chat `Markdown.tsx`/`MarkdownLite.tsx` (grep at `9529c99`), so react-markdown's default applies. A code comment in the client says that default allows only http, https, irc, mailto and xmpp ([LC@9529c99 `client/src/utils/media.ts` L140](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/client/src/utils/media.ts#L140)), so `http://localhost:8501/...` links should survive.
- So a link such as `http://localhost:8501/qc?role=…&case=DEMO01-0001` is clickable **only if the model copies it verbatim**. Put the instruction "always repeat the `link` field as a Markdown link, unchanged" in the system prompt, and return the link in a dedicated field.
- MCP Apps / UI resources (`mcpSettings.apps`, a sandbox origin) are a richer option, but they are opt-in and new ([LC@9529c99 `librechat.example.yaml` L692–724](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L692-L724)). Not needed for a demo.

### Passing "who is logged in" to our backend

- **MCP (good fit).**
  - `headers` and `requestHeaders` on a YAML-defined server resolve `{{LIBRECHAT_USER_ID}}`, `{{LIBRECHAT_USER_EMAIL}}`, `{{LIBRECHAT_USER_USERNAME}}`, `{{LIBRECHAT_USER_ROLE}}` and others, plus `{{LIBRECHAT_BODY_CONVERSATIONID}}`, `{{LIBRECHAT_AGENT_ID}}`, `{{LIBRECHAT_OPENID_*}}` tokens, `${ENV_VAR}` and per-user `customUserVars` ([mcp_servers docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers); allowed user fields in [LC@9529c99 `packages/api/src/utils/env.ts` L39–58](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/api/src/utils/env.ts#L39-L58); example at [`librechat.example.yaml` L757–774](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L757-L774)).
  - `requestHeaders` are sent only during a chat turn, not during tool discovery (same lines).
  - Per-user OAuth for MCP is supported (`requiresOAuth`, an `oauth` block, Dynamic Client Registration), with tokens stored per user ([MCP docs](https://www.librechat.ai/docs/features/mcp)).
  - On our side, the MCP Python SDK exposes `ctx.headers` in a tool. The SDK docs warn that headers are client-supplied and suit things like a locale, **not identity** ([py SDK, The Context](https://py.sdk.modelcontextprotocol.io/handlers/context/)). See §3 for how to make the header trustworthy.
- **Actions (poor fit for identity).** Action auth is one of none, service API key (Basic, Bearer or a custom header), or per-user OAuth ([LC@9529c99 `packages/data-provider/src/actions.ts` L220–275](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/actions.ts#L220-L275); OAuth flow in [`api/server/services/ActionService.js` L212–330](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/api/server/services/ActionService.js#L212-L330)). I found **no** `{{LIBRECHAT_USER_*}}` resolution on the Action request path (grep of `ActionService.js`, `packages/api/src/actions/` and `actions.ts` at `9529c99`). Without an OAuth server, the backend would see only a shared API key.
- **FastAPI → Actions gotchas.**
  - LibreChat rejects a spec with no `servers[0].url` ([`actions.ts` L835–846](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/actions.ts#L835-L846)).
  - FastAPI generates `/openapi.json` automatically ([FastAPI, Extending OpenAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)), but you add the server with `FastAPI(servers=[...])` ([FastAPI, Behind a Proxy → Additional servers](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)).
  - FastAPI emits OpenAPI 3.1.0 by default (same Extending page). LibreChat's converter is typed against `OpenAPIV3`, and its schema-to-zod step maps a schema with no `type` (for example an `anyOf` for an `Optional` field) to `z.unknown()` ([`actions.ts` L109–116](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/actions.ts#L109-L116)). That probably works, but it is untested (see Unconfirmed).

**Conclusion for §1:** use a YAML-defined **streamable-HTTP MCP server**. Skip Actions: they are UI-only, carry no per-user identity without OAuth, and have 3.1 schema friction.

---

## 2. Making it "guided"

- **Pin users to one assistant with a model spec.**
  - Define one spec with `default: true` (it is auto-selected on every new chat), `preset: {endpoint: anthropic, model: …, promptPrefix: "<workflow instructions>"}`, `mcpServers: ["adjud"]` and `conversation_starters` (up to 4 shown) ([model_specs docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs); fields in [LC@9529c99 `packages/data-provider/src/models.ts` L41–60, L94, L411–416](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/models.ts#L41-L60)).
  - For the `anthropic` endpoint, the system-prompt field in the preset schema is `promptPrefix` ([LC@9529c99 `schemas.ts` L1777–1781](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/schemas.ts#L1777-L1781)). The YAML example writes `instructions:` for an anthropic spec ([`librechat.example.yaml` ~L1497](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L1490-L1500)), which looks inconsistent (Unconfirmed).
  - Alternatively, build the assistant in the Agent Builder and reference it with `preset.agent_id`. That is less config-as-code, because the agent lives in Mongo.
- **Hide everything else.**
  - When a spec list exists, `modelSelect`, `parameters` and `presets` are disabled unless set to `true` in `interface`.
  - `enforce: true` makes specs strictly override other configuration.
  - `hideBadgeRow: true` hides the composer's Tools, Skills and MCP controls for the spec ([model_specs docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs)).
  - The example YAML ships with `modelSelect: true` and `agents: {use: true, create: true}`, so set these deliberately ([`librechat.example.yaml` L299–319](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L299-L319)).
  - The `interface.agents` and `interface.mcpServers` permission keys only *seed* USER-role permissions at startup. After that, the Admin Panel owns them ([interface docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/interface)).
- **MCP server instructions.** `serverInstructions: true | "<string>"` injects the server's own usage instructions into the prompt ([mcp_servers docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers)). `chatMenu: false` hides the server from the composer's MCP picker while agents can still use it (same page).
- **Confirm before a state-changing tool.** `endpoints.agents.toolApproval` can force an approval card before matching tools run, for example `ask: ["mcp:adjud:start_*"]` ([LC@9529c99 `librechat.example.yaml` L1108–1125](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L1108-L1125); schema in [`config.ts` L1422–1490](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/packages/data-provider/src/config.ts#L1422-L1490)). **This card is a UI confirmation, not an electronic signature**, and must not be presented as one.
- **Branding/welcome:** `interface.customWelcome` and the landing options are at [`librechat.example.yaml` L203–216](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L203-L216).

---

## 3. Authentication

- **Login methods:** email and password (`ALLOW_EMAIL_LOGIN`), OpenID Connect/OAuth2, SAML, LDAP, social logins (GitHub, Google, Discord, Facebook, Apple) and passkeys ([Authentication docs](https://www.librechat.ai/docs/configuration/authentication); env names in [LC@9529c99 `.env.example` L974–987, L1045, L1061–1063, L1159, L1211](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L974-L987); `registration.socialLogins` at [`librechat.example.yaml` L488–489](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L488-L489)).
- **Disabling registration:** `ALLOW_REGISTRATION=false` and `ALLOW_SOCIAL_REGISTRATION=false`. Users can still be added with `npm run create-user` and removed with the delete-user script. OpenID and SAML **cannot** disable only registration ([Authentication docs](https://www.librechat.ai/docs/configuration/authentication)). Note that `.env.example` defaults to `ALLOW_REGISTRATION=true` ([L976](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L976)).
- **Roles:** in a single-tenant deployment, the first registered account becomes `ADMIN` (same docs). Role permissions, including custom roles, are edited in the separate Admin Panel ([interface docs](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/interface)). The Admin Panel is its own container and starts only once `ADMIN_PANEL_SESSION_SECRET` is set ([LC@9529c99 `docker-compose.yml` L40–55](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.yml#L40-L55); [Docker guide](https://www.librechat.ai/docs/local/docker)).
- **How our backend can trust and identify the user.** Two options:
  1. **Demo option, cheap.**
     - LibreChat sends `X-Adjud-User: {{LIBRECHAT_USER_EMAIL}}` plus a static `X-Adjud-Key: ${ADJUD_MCP_KEY}`, taken from an env var, in the MCP `headers`.
     - The MCP/FastAPI container publishes **no host port**. It sits only on the compose network that LibreChat shares.
     - The backend rejects calls without the key, then maps the email to a user id in `data/config/users.yaml`. Our deep links carry `role` and `user`, so the mapping is needed anyway.
     - This trusts LibreChat as the authenticator, which is acceptable for synthetic data. The SDK's warning that headers aren't identity ([py SDK Context](https://py.sdk.modelcontextprotocol.io/handlers/context/)) is answered by the network isolation and shared secret, not by the header itself.
  2. **Proper option.**
     - Run a self-hosted OIDC provider (for example Keycloak in the same compose) and set `OPENID_REUSE_TOKENS=true` ([`.env.example` L1110](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L1110)).
     - Send `Authorization: Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}` to the MCP server ([`librechat.example.yaml` L757–766](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L757-L766)).
     - The backend verifies the JWT. The same IdP could later protect Streamlit or FastAPI.
     - Costs about +1 day.
- **Streamlit is unchanged:** today's deep links select role and user by query string, so "roles are shown, not enforced" (AGENTS.md) still holds after a LibreChat login. LibreChat adds a real login in front of the chat only.

---

## 4. Self-hosting effort

- **Services in the stock `docker-compose.yml`** ([LC@9529c99 L4–99](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.yml#L4-L99)):
  - `api`: LibreChat (Node)
  - `mongodb`: `mongo:8.0.20`, started with `--noauth`
  - `meilisearch`: v1.35.1
  - `vectordb`: pgvector/Postgres 15
  - `rag_api`
  - `admin-panel`
  - `api` has `depends_on: [mongodb, rag_api]`.
- **What's required:**
  - **MongoDB is required**: it is LibreChat's datastore (`MONGO_URI`, same file L19).
  - **Meilisearch is optional.** `SEARCH=false` is the `.env.example` default ([L790](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L786-L794)), and the override example shows disabling the container with `profiles: [donotstart]` ([`docker-compose.override.yml.example` L95–98](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.override.yml.example#L95-L98)).
  - **RAG API + pgvector** are only for file search. The commented example settings in `.env.example` show OpenAI embeddings (`EMBEDDINGS_PROVIDER=openai`) ([`.env.example` L857–866](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L857-L866)), so turn it off for us. Whether `api` runs cleanly with `rag_api` disabled is Unconfirmed.
- **Resources:** the LibreChat containers need ≥1 GiB RAM and 1 vCPU, and Docker needs ≥2 GB with everything running. No GPU is needed ([Docker guide → System Requirements](https://www.librechat.ai/docs/local/docker)).
- **Steps** ([Docker guide](https://www.librechat.ai/docs/local/docker)):
  1. Clone the repo and run `cp .env.example .env`.
  2. Copy `docker-compose.override.yml.example` to `docker-compose.override.yml` and uncomment the `librechat.yaml` mount.
  3. Run `docker compose up -d`.
  4. Open `http://localhost:3080` and register the first account, which becomes admin.
- **Files touched:** `.env` (secrets such as `CREDS_KEY` and `JWT_SECRET`, the `ALLOW_*` flags, `ANTHROPIC_API_KEY`), `librechat.yaml` (`mcpServers`, `mcpSettings`, `modelSpecs`, `interface`, `endpoints`) and `docker-compose.override.yml`.
- **Anthropic:**
  - Set `ANTHROPIC_API_KEY` to the key, or to `user_provided` (the `.env.example` default, which makes each user paste their own: change it) ([`.env.example` L531](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/.env.example#L531)).
  - Restrict models with `ANTHROPIC_MODELS` or `endpoints.anthropic.models` ([Anthropic docs](https://www.librechat.ai/docs/configuration/pre_configured_ai/anthropic)).
  - A custom endpoint with `provider: anthropic` and its own `baseURL` is supported ([`librechat.example.yaml` L1280–1300](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L1280-L1300)). That is the hook for routing LibreChat's model calls through a proxy that logs into our usage ledger (see §7).
- **Images and updates:**
  - The stock compose pulls **`librechat-dev:latest`**, an unpinned dev build ([`docker-compose.yml` L12](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.yml#L12)). The override example offers `librechat:latest` labelled "NUMBERED RELEASE" ([`docker-compose.override.yml.example` L44–46](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.override.yml.example#L44-L46)). **Pin a release.**
  - Releases are frequent: v0.8.8-rc3 on 2026-09-14, rc4 on 09-20, v0.8.8 on 09-30, v0.8.9-rc1 on 10-26. Config schema versions 1.3.15 → 1.3.18 shipped in about 7 weeks ([changelog](https://www.librechat.ai/changelog)).
  - `UPGRADING.md` documents permission-semantics changes between RCs, for example web search after v0.8.8-rc2 ([LC@9529c99 `UPGRADING.md`](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/UPGRADING.md)).
  - Updating means `docker compose down`, removing the images, `git pull`, `docker compose pull`, then `up` ([Docker guide → Updating](https://www.librechat.ai/docs/local/docker)).
  - Expect config churn: `librechat.example.yaml` is 1,863 lines at HEAD.

---

## 5. Demo speed (my estimate, not sourced)

| Step | Estimate | Notes |
|---|---|---|
| LibreChat up in Docker with Anthropic and registration off | 1–2 h | Pinned image; Meilisearch and RAG disabled; admin and demo users created with `create-user` |
| MCP server over `adjud/` with 3 tools | 4–6 h | Python `MCPServer` (`mcp` 2.x) with `@mcp.tool()` over streamable HTTP ([py SDK](https://github.com/modelcontextprotocol/python-sdk)), mountable in an ASGI/FastAPI app ([py SDK, Add to an existing app](https://py.sdk.modelcontextprotocol.io/run/asgi/)). Suggested tools:
- `list_my_tasks(user)`: read-only, using `CaseStore.list_case_states`, filtered to the non-automatic states the user's role acts in.
- `case_status(case_id)`: state, the next human step and why, and a deep link.
- One real write: `relabel_document(case_id, doc, type)`, which appends `document.labelled` (allowed only in `AWAITING_SITE_CONFIRMATION`) through `CaseStore`. Alternatively `flag_case`.

There is no "run QC" trigger. QC starts automatically once a case is `SUBMITTED`. Each returns `{…, "link": "http://localhost:8501/qc?role=…&user=…&case=…"}` |
| Wiring in `librechat.yaml` | 2–3 h | `mcpServers.adjud` (`type: streamable-http`, `headers`), `mcpSettings.allowedAddresses: ["adjud-mcp:8000"]`, one default model spec with `promptPrefix`, `mcpServers`, `conversation_starters` and `enforce`, interface lockdown, optional `toolApproval.ask` for the trigger tool |
| Identity (header + shared secret + email → `users.yaml` id) | 1–2 h | OIDC/Keycloak instead: +1 day |
| Tests (wrapper only, no LibreChat in CI) and a demo script | 3–4 h | Tests call the tool functions directly; no Claude API calls |
| **Total** | **≈1.5–2 days**; ≈1 day if you cut tests and identity | |

**Riskiest parts, in order:**
1. **The MCP server's host check.** The SDK reportedly accepts only localhost unless `transport_security=` is set, and LibreChat will call it as `adjud-mcp:8000` (see Unconfirmed). Pair it with LibreChat's own SSRF block on private addresses.
2. **The model rewriting or dropping deep links.**
3. **Config drift on an RC-heavy project.**
4. **A third writer to `CaseStore`.** Locking covers it, but actor attribution for chat-triggered events is an open decision.
5. **Cost and audit gaps** from LibreChat's own Anthropic calls.

---

## 6. Alternatives (one line each)

- **Open WebUI** has a similar chat-plus-tools model, but its licence adds a branding clause: Open WebUI branding may not be removed above 50 users in a rolling 30 days without permission ([Open WebUI LICENSE](https://github.com/open-webui/open-webui/blob/main/LICENSE), clause 4). I did not review its tool or MCP support.
- **No new chat UI at all:** expose the same MCP server to an existing MCP client. The MCP server is the reusable part either way.

---

## 7. Verdict, minimal architecture, risks

**Verdict.** LibreChat can be the guided front door. The MCP server is the part worth building, because it is client-agnostic. LibreChat itself costs about a day to stand up and adds MongoDB, a second Anthropic path and a second store of conversation data. Choose it only after recording plan decisions on those three points.

**Minimal demo architecture.** LibreChat's compose lives in its own clone. Either add our `adjud-mcp` service to LibreChat's `docker-compose.override.yml`, or join both compose projects to one shared external Docker network.
```
browser ──► LibreChat api :3080  (login; registration off; one default model spec, enforce)
              │  MongoDB (required, internal only)      [Meilisearch, RAG, pgvector: disabled]
              │  model calls ──► Anthropic (direct, or via a ledger proxy: custom endpoint baseURL)
              │  MCP streamable-http, headers: X-Adjud-User={{LIBRECHAT_USER_EMAIL}}, X-Adjud-Key=${ADJUD_MCP_KEY}
              ▼
          adjud-mcp :8000  (no published port; FastAPI + mounted MCPServer; read tools + document.labelled write)
              │  CaseStore (lock, validate, hash-chain)  ── same ./data volume ──  runner, Streamlit :8501
              ▼
          tool result: {state, next_step, link: "http://localhost:8501/qc?role=…&user=…&case=…"}
              └─► model repeats link as Markdown ─► user clicks ─► signs in Streamlit (human)
```

**Fit with AGENTS.md:**

| Rule | Fit |
|---|---|
| Docker Compose deployment, no cloud service | Fits. Everything is self-hosted. The intended external call is to Anthropic, as today. I did not audit LibreChat's other egress (for example the analytics and OpenTelemetry options are off unless configured, per `.env.example`) |
| "No database server" | **Conflicts.** MongoDB is required ([`docker-compose.yml` L19, L56–63](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.yml#L56-L63)). It is LibreChat's own store, not case data, but it needs a recorded decision |
| All AI calls via `gateway.py`, cost ledger, budget cap, replay, per-case log | **Conflicts.** LibreChat calls Anthropic itself. Mitigation: a `provider: anthropic` custom endpoint whose `baseURL` is our proxy that writes `data/ai/usage.jsonl` and enforces the cap. Replay mode and per-case logs still won't cover chat turns |
| API key only from env | Fits: `ANTHROPIC_API_KEY` in `.env`, untracked. Don't use `user_provided` |
| People sign every medical decision; an automated decision is never an e-signature | Fits **only if** the MCP server exposes no sign, QC-pass, query or narrative-approve tools and returns deep links instead. `toolApproval` is a confirmation, not a signature |
| Agents act only through local tools, never writing files or events | The MCP tools *are* local tools, and every write goes through `CaseStore`. Chat-triggered events need an actor field (for example `actor: chat:<user>`), to be decided in the plan |
| Notifications carry no clinical content | Not affected (LibreChat sends no notifications in this design). But **chat transcripts with case details sit in MongoDB**, outside the hash chain and outside `verify_log`. Consider `interface.retentionMode: "ephemeral"` ([`librechat.example.yaml` L450–458](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/librechat.example.yaml#L450-L458)) |
| Synthetic data only; data leaving the machine | Chat text and tool results go to Anthropic. Tools must return only case IDs, states and Flow 2 manifest-level (redacted) data, never anything from `documents/original/` |

**Licence:** LibreChat is **MIT** ([LC@9529c99 `LICENSE`](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/LICENSE)). The Admin Panel is a separate image from LibreChat's registry (at `v0.8.8` it came from a `clickhouse/` path), and I did not check its licence.

**Other open risks:**
- MongoDB in the stock compose runs `--noauth` ([L63](https://github.com/LibreChat-AI/LibreChat/blob/9529c99f5ebd5469942feb2c83a785894f48522b/docker-compose.yml#L63)), so never publish its port.
- The default compose pulls an unpinned dev image.
- The SSRF allow-lists must be set exactly (`host:port`), or the MCP server won't connect.

---

## Unconfirmed

- **SDK host check:** that the MCP Python SDK's streamable-HTTP app rejects non-localhost `Host` headers by default and needs `transport_security=` to accept `adjud-mcp:8000`. This came from a summary of [py SDK, Add to an existing app](https://py.sdk.modelcontextprotocol.io/run/asgi/), not verbatim text. The FastAPI mount snippet I saw there was a summariser's illustration, not quoted SDK code.
- **Exact Context API:** the py SDK Context page documents `ctx.headers` (None on stdio), and I didn't confirm other request attributes.
- **Running without `rag_api`:** whether LibreChat runs cleanly with `rag_api` (and `vectordb`) disabled, given `api.depends_on: rag_api`.
- **FastAPI 3.1 specs in Actions:** whether a FastAPI-generated OpenAPI 3.1 spec with `anyOf`-nullable fields imports cleanly as an Action. The code suggests these degrade to `z.unknown()`, but I didn't test it.
- **Prompt field in specs:** whether `preset.instructions` (used in the YAML example for an anthropic spec) is honoured, or whether only `promptPrefix` is, for a non-agent model spec.
- **Pinned image tags:** whether version-tagged images (for example `librechat:v0.8.8`) exist in `registry.librechat.ai`. The compose files show only `:latest` tags.
- **stdio:** whether the LibreChat image contains Python. If it doesn't, stdio would need a custom image; I assumed it doesn't and didn't check.
- **Model behaviour:** how reliably the model repeats a link verbatim. This is model behaviour and needs testing.
- **Admin Panel licence:** not checked.
- **Open WebUI:** its tool and MCP support was not reviewed.
- **`toolApproval` scope:** it is configured under `endpoints.agents`. Whether it applies to a plain model spec with `endpoint: anthropic` and `mcpServers`, or only to agent-endpoint runs, is unverified. If only the latter, the guided assistant should be a UI-built agent referenced by `preset.agent_id`.
- **Access Control docs:** I didn't read LibreChat's Access Control docs page. The role model above rests on the Authentication and interface pages only.
- **Outbound traffic:** LibreChat's full outbound traffic (telemetry, update checks, CDN assets) was not audited.
- **My time estimates** in §5.
