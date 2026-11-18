# Slack as the main interface: what the platform allows

Checked on 2026-11-17. Sources are docs.slack.dev, the slackapi/bolt-python repository, and slack.com Help Center, legal and resource pages for questions 7 and 9. Each claim links to the page it comes from. Statements marked **(inference)** are ours, not Slack's. Where a page is silent, we say "the page does not state", not "there is no limit".

How the pages were read:
- **Verbatim against the raw text:** the key quotes (password guidance, Request URL rule, button `url`, signature base string, `token_verification_enabled`). Source: docs.slack.dev's Markdown sources (the page URL plus `.md`) and raw GitHub files.
- **Through a summarising fetch tool:** the rest. Short quoted fragments are as the tool returned them.

**Summary.**
- **Laptop demo.** A Bolt for Python app in **Socket Mode** fits. It needs an app-level token and a bot token, and no public URL or tunnel.
- **Passwords.** Slack tells apps not to ask for passwords in modals or on any app surface, and Block Kit has no masked input. A typed value comes back to the app in clear text in the view state. So e-signatures with password re-entry should stay on our own page, opened from a Slack button.
- **Files.** Slack supports PHI only on Enterprise plans with a signed BAA, and its default terms say customers must not process PHI. Uploading redacted PDFs is ruled out for the demo by our own no-clinical-content rule anyway.
- **What travels through Slack.** Every value the app puts in a view or button comes back through Slack: `private_metadata`, button `value`, typed input. So does any free text a user types into the app's DM or after a slash command. All of these must stay free of clinical content.

## Question

Can a Slack app (Bolt for Python) become the main interface for the adjudication POC, with Streamlit as the fallback? What do Slack's modals, App Home, slash commands, buttons, user identity, files, rate limits, free plan and manifest allow, and what do they rule out?

## Our constraints

- Today `adjud/slack.py` only posts notifications through an incoming webhook ([ADR 0002](../adr/0002-slack-runs-alongside-email-until-reliable.md)).
- **No clinical content may leave the app into Slack.** `.scratch/slack-workflow/ui-inventory.md` lists the actions; many show the AE term, onset, documents, AI findings, the narrative or adjudication answers.
- **Every medical decision stays a person's signed action.** Several actions are e-signatures: the person re-enters their password, and the signature is recorded in the case log.
- **Tests never hit the network.**
- **Demo runs on a presenter's laptop**, which is not reachable from the internet.

## Short answer

1. **Socket Mode fits the laptop demo.** It needs no public Request URL and no tunnel. It needs an app-level token (`xapp-`, scope `connections:write`) and a bot token. Bolt runs it with `SocketModeHandler`. Socket Mode apps are not allowed in the public Slack Marketplace, which doesn't matter for an internal app.
2. **Slack says not to collect passwords in modals, and Block Kit has no masked input.** The modals guide says: "Users should not be prompted for confidential information like passwords within modals (or any app surface area, for that matter)". It tells apps to "direct them to your login". No password or masked element appears in the block element list. Text typed into a plain-text input comes back to the app in `view.state.values`. So an e-signature that needs a password can't follow Slack's guidance inside Slack. The signature step has to happen on our own page (Streamlit) **(inference from the cited guidance)**.
3. **Don't upload documents to Slack, including redacted PDFs.** Slack supports PHI only on Enterprise plans with a signed BAA. It says it has no BAA with third-party app providers. Its standard Customer-Specific Supplement says Slack is not a Business Associate and customers must not process PHI, "unless as otherwise set forth in Customer's Contract". Our own rule rules out upload even for synthetic data, because a redacted PDF is still clinical content **(inference: project rule, not a Slack rule)**.
4. **Everything Slack sends back to the app passes through Slack.** That includes `private_metadata`, button `value` and typed input values. Those fields must carry only case IDs, task keys and similar, never clinical text **(inference)**.
5. **The 3-second window covers every interaction.** Slash commands, buttons and view submissions must be acknowledged within 3 seconds, and a `trigger_id` expires 3 seconds after issue. With `process_before_response=False` (the default), Bolt for Python runs listeners in a thread pool and replies to Slack as soon as `ack()` is called. Slow work can follow in the same function.
6. **Tests:** `App(token=...)` calls `auth.test` at construction unless `token_verification_enabled=False`. Tests must turn that off or inject a client pointed at a local stub.

---

## 1. Socket Mode vs a public Request URL

**What Socket Mode needs.**

- **A public URL?** None. "Socket Mode allows your app to use the Events API and interactive features—*without* exposing a public HTTP Request URL." With it enabled, "your app settings doesn't require or even allow you to enter a Request URL". ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode))
- **App-level token.** The app calls `apps.connections.open` with an app-level token in the `Authorization` header to get a WebSocket URL. Passing the token as a POST parameter gives an error. ([apps.connections.open](https://docs.slack.dev/reference/methods/apps.connections.open))
- **The token's scope: the sources disagree on the wording.**
  - The method page says "No scopes required" ([apps.connections.open](https://docs.slack.dev/reference/methods/apps.connections.open)).
  - The scope page lists `connections:write`, "Grants permission to generate websocket URIs and connect to Socket Mode", with token type "App-level" and compatible method `apps.connections.open` ([connections:write](https://docs.slack.dev/reference/scopes/connections.write)).
  - The Bolt README says the app-level token needs `connections:write` ([bolt-python README](https://github.com/slackapi/bolt-python/blob/main/README.md)).
  - In practice, give the token `connections:write`.
- **Creating the token:** "Under Basic Information, scroll to the App-level tokens section and click the button to generate an app-level token." ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode))
- **Bot token:** for Web API calls such as `chat.postMessage` and `views.open`. The Bolt example passes it as `App(token=os.environ["SLACK_BOT_TOKEN"])`. ([Bolt Python: Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode))
- **Signing secret:** HTTP mode only. In Bolt's request-verification middleware, `_can_skip` returns true when `mode == "socket_mode"`, so Socket Mode requests are not signature-checked ([request_verification.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/middleware/request_verification/request_verification.py)). Slack's page says that with Socket Mode, "there's no need to verify or validate inbound events" ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)).
- **App type:** Socket Mode is "**only** available for apps using granular permissions" ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)).

**Limits and caveats** (all from [Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)):

- **Connections.** An app can keep "*up to 10* open WebSocket connections at the same time", and "each payload may be sent to *any* of the connections". If two of our processes (for example the UI and the runner) each open a handler, either one may receive a given click. Only one process should run the Slack handler **(inference)**.
- **Distribution.** "Apps using Socket Mode are *not* currently allowed in the public Slack Marketplace."
- **Refresh and reconnect.**
  - Connections refresh "once every few hours".
  - A warning may arrive "about 10 seconds before the disconnect", and the app should expect a `refresh_requested` message.
  - Events can be lost while no connection is open.
  - The SDKs handle reconnecting.
- **Acknowledgement.** Each envelope must still be acknowledged by sending back its `envelope_id`.
- **Envelope types.**
  - Slash commands arrive as `"type": "slash_commands"`.
  - Buttons, modals and menus arrive as `"type": "interactive"`.
  - App Home events arrive as `"type": "events_api"`.

**Request URL (HTTP) mode** needs a public HTTPS endpoint and signing-secret verification. Slack signs each request with `X-Slack-Signature`. The value is `v0=` plus the HMAC-SHA256 hex digest of `'v0:' + timestamp + ':' + request_body`, and the timestamp must be within five minutes of local time. Bolt for Python verifies it automatically. ([Verifying requests from Slack](https://docs.slack.dev/authentication/verifying-requests-from-slack)) For a slash command's Request URL: "If public distribution is active for your app, this needs to be an HTTPS URL (and self-signed certificates are not allowed). If you're building an app solely for your own workspace, it should also be HTTPS." ([Implementing slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands)) On a laptop, that means a public tunnel **(inference)**.

**Running it in Bolt** ([Bolt Python: Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode)):

```python
import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
```

The default adapter uses `slack_sdk`. The alternatives are `websocket_client`, `aiohttp` and `websockets`, plus `AsyncApp` with `AsyncSocketModeHandler`. Same page.

## 2. Modals

**Opening a modal.**

- **`trigger_id` expiry:** "A `trigger_id` will expire 3 seconds after it's sent to your app, so you'll want to use it quickly" ([Modals, "Opening modals"](https://docs.slack.dev/surfaces/modals)).
- **Single use:** each trigger can be used once. An expired one returns `expired_trigger_id` and a reused one `exchanged_trigger_id` ([views.open](https://docs.slack.dev/reference/methods/views.open)). The interactions page names them `trigger_expired` / `trigger_exchanged` ([Handling user interaction](https://docs.slack.dev/interactivity/handling-user-interaction)). Trust the method page's error codes **(inference)**.
- **Scopes and rate limits:**
  - [`views.open`](https://docs.slack.dev/reference/methods/views.open): no scopes required, Tier 4.
  - [`views.update`](https://docs.slack.dev/reference/methods/views.update): no scopes required, Tier 4. Takes `view_id` or `external_id` plus an optional `hash`; returns `hash_conflict` if the view changed in between.
  - [`views.push`](https://docs.slack.dev/reference/methods/views.push): no scopes required, Tier 4.
- **Size:** each method rejects a view "greater than 250kb" with `view_too_large`.
- **View stack:** "A modal can hold up to 3 views at a time in a view stack" ([Modals](https://docs.slack.dev/surfaces/modals)). Once a modal is open, the app can push 2 more views ([views.push](https://docs.slack.dev/reference/methods/views.push)).

**Submission.**

- Submitting sends a `view_submission` payload. The app has 3 seconds to respond. Its options:
  - `response_action: "errors"` with an `errors` object of messages keyed by block ID
  - `"update"`
  - `"push"`
  - `"clear"`
  
  "Your app cannot use any other method to close views." ([Modals](https://docs.slack.dev/surfaces/modals))
- In Bolt: `@app.view("<callback_id>")`, then `ack(response_action="errors", errors=errors)`. Values are read from `view["state"]["values"][block_id][action_id]` and the user from `body["user"]["id"]`. ([Bolt Python: Listening to views](https://docs.slack.dev/tools/bolt-python/concepts/view-submissions))
- With `notify_on_close: true`, Cancel or close sends a `view_closed` payload, which "will include the full state of the view, excluding any input blocks" ([View interactions payload](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload)).

**Size limits** ([Modal views](https://docs.slack.dev/reference/views/modal-views); [Block Kit](https://docs.slack.dev/block-kit)):

| Field | Limit |
|---|---|
| `blocks` | 100 per modal |
| `title`, `submit`, `close` text | 24 characters |
| `private_metadata` | 3000 characters |
| `callback_id` | 255 characters |
| `external_id` | unique per team |
| Plain-text input `max_length` | 1 to 3000 ([Plain-text input](https://docs.slack.dev/reference/block-kit/block-elements/plain-text-input-element)) |
| Plain-text input `action_id` | 255 characters (same page) |

**`private_metadata` goes through Slack.** It is "not shown to users, but is returned to your app in `view_submission` and `block_actions` events" ([Modals](https://docs.slack.dev/surfaces/modals)). The Home-tab view reference adds, about `callback_id`: "Don't use this to store sensitive information (use `private_metadata` instead)" ([Home tab views](https://docs.slack.dev/reference/views/home-tab-views)). Slack treats `private_metadata` as hidden from users, but it is still part of the view Slack stores and returns. Keep it to IDs **(inference)**.

**Password inputs: none, and Slack advises against them.**

- **Guidance.** Under the heading "Don't prompt for login information": "Users should not be prompted for confidential information like passwords within modals (or any app surface area, for that matter). When your app needs access to a user's credentials, you should direct them to your login and store any necessary information on your app's backend." ([Modals](https://docs.slack.dev/surfaces/modals))
- **No masked element.** None of the elements in the block element index is a password, masked or secret input (its raw text doesn't contain "password", "mask" or "secret") ([Block elements](https://docs.slack.dev/reference/block-kit/block-elements)). The plain-text input's fields are `action_id`, `initial_value`, `multiline`, `min_length`, `max_length`, `dispatch_action_config`, `focus_on_load` and `placeholder`; none masks input ([Plain-text input](https://docs.slack.dev/reference/block-kit/block-elements/plain-text-input-element)).
- **Where typed text goes.** On submit, the typed value is sent to the app in `view.state.values` as `{"type": "plain_text_input", "value": "…"}` ([View interactions payload](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload)).
- **Not stated.** None of the pages we read says whether Slack stores or logs view state after submission. See Open questions.
- **Consequence (inference).** A password re-entered in a modal would show on screen in clear text and travel through Slack's servers to our app. That contradicts Slack's own guidance. The password check for an e-signature needs to stay on our page; a Slack button can link to it.

## 3. App Home tab

- **What it is.** "The App Home is a private, one-to-one space in Slack shared by a user and an app", with Home, Messages and About tabs. It is available only to granular-permission apps. ([App Home](https://docs.slack.dev/surfaces/app-home))
- **Publishing.** `views.publish` takes `user_id` and `view`, plus an optional `hash`. It needs no scopes, is Tier 4 ("100+ per minute"), and returns `not_enabled` if the Home tab isn't switched on and `view_too_large` above 250 kB. ([views.publish](https://docs.slack.dev/reference/methods/views.publish))
- **Per user.** Each publish targets one user, so content can differ per user, and per role in our mapping. The guide advises apps to "store the `user_id` of the Home tab user, along with any relevant customizations for individual users". ([App Home](https://docs.slack.dev/surfaces/app-home))
- **`app_home_opened` event.**
  - Needs no scopes ("No scopes required!").
  - Includes `user`, `channel`, `tab` (`"home"` or `"messages"`) and, if a view was published before, `view`.
  
  ([app_home_opened](https://docs.slack.dev/reference/events/app_home_opened))
- **Limits.** Up to 100 blocks, `private_metadata` 3000 characters, `callback_id` 255 characters ([Home tab views](https://docs.slack.dev/reference/views/home-tab-views)).
- **Refresh.** Calling `views.publish` again replaces the view. The guide notes that updates "can happen when a user isn't interacting with Slack or the app", so the runner can republish after a state change. ([App Home](https://docs.slack.dev/surfaces/app-home)) Its examples publish inside the `app_home_opened` handler.
- **Not stated.** Whether a Home tab can be published to a user who has never opened it.
- **Messages tab.** Needs `chat:write`, optionally `im:history`, and the `message.im` event ([App Home](https://docs.slack.dev/surfaces/app-home)). `im:history` grants `message.im` ([im:history](https://docs.slack.dev/reference/scopes/im.history)). The manifest flag `messages_tab_read_only_enabled` controls whether users can type to the app ([App manifest](https://docs.slack.dev/reference/app-manifest)).
- **Interactivity.** Interactive components on the Home tab need interactivity switched on ([App Home](https://docs.slack.dev/surfaces/app-home)).

## 4. Slash commands

- **Manifest.** `features.slash_commands[]` with these fields ([App manifest](https://docs.slack.dev/reference/app-manifest)):
  - `command`: up to 32 characters, with the leading `/`
  - `description`: required, up to 2000 characters
  - `usage_hint`: optional, up to 1000 characters
  - `should_escape`: optional, default `false`
  - `url`: optional, "full `https` URL"
- **Scope:** `commands`, "Add shortcuts and slash commands that people can use" (Bot token) ([commands scope](https://docs.slack.dev/reference/scopes/commands)).
- **Payload** ([Implementing slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands)):
  - `team_id`, `enterprise_id`, `channel_id`, `channel_name`
  - `user_id`; `user_name` is deprecated: "Use the `user_id` instead."
  - `command`, `text` ("can contain absolutely anything the user might decide to type")
  - `response_url`, `trigger_id`, `api_app_id`
  - `token`: a deprecated verification token
  
  "Private channels will only include the channel ID." (Same page.)
- **Acknowledgement.** It must arrive "within 3000 milliseconds of the original request being sent", otherwise the user sees `operation_timeout` (same page).
- **Visibility.** The default `response_type` is `ephemeral`, so only the invoking user sees it. `in_channel` posts to the channel and shares the original command there too (same page).
- **`response_url` lifetime: not stated on the slash-command page.** The interactions page says `response_url` responses "can be sent up to 5 times within 30 minutes of receiving the payload" ([Handling user interaction](https://docs.slack.dev/interactivity/handling-user-interaction)). Whether this applies to slash commands is not stated on the slash-command page.
- **Socket Mode.** Supported. Slash commands are listed among the interactive payloads delivered over the WebSocket, as envelope type `slash_commands` ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)). Whether the manifest's slash-command `url` must be set under Socket Mode is not stated. The manifest page says "Not needed for Socket Mode" only for a different field (`features.code_channels.slash_command_url`) ([App manifest](https://docs.slack.dev/reference/app-manifest)).
- **Where they work.** "your app can be summoned by users from any conversation in Slack". They don't work in message threads and "are not supported in the split view container" ([Implementing slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands)). A DM with the app is a conversation, so commands should work there **(inference)**. No page we read confirms this, nor how `messages_tab_read_only_enabled` affects it.
- **Namespacing.** "Slash commands are not namespaced": if two apps share a name, the most recently installed one wins (same page).
- **Bolt.** `@app.command("/echo")`, then `ack()`, then `respond(...)` (through `response_url`) or `say(...)` ([Bolt Python: Commands](https://docs.slack.dev/tools/bolt-python/concepts/commands)).

## 5. Buttons and interactivity

**`block_actions` payload** ([block_actions payload](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload)):

- **Always present:**
  - `type`, `trigger_id`, `api_app_id`, `hash`
  - `user` (`id`, `username`, `team_id`)
  - `team` (null if org-installed)
  - `container` (`message…` with `channel_id`/`message_ts`, or `view` with `view_id`)
- **Optional:**
  - `actions[]`, each with `action_id`, `block_id` and `value`
  - `view`, `message`, `channel`, `state`
  - `response_url`
- **`response_url` from views.** The message example has `response_url` and the Home-tab example doesn't. Don't rely on it for actions from a view.

**Button limits** ([Button element](https://docs.slack.dev/reference/block-kit/block-elements/button-element)):

| Field | Limit |
|---|---|
| `text` | 75 characters |
| `action_id` | 255 characters |
| `value` | 2000 characters, sent back in the payload |
| `url` | 3000 characters |
| `confirm` | optional confirmation dialog |
| `style` | `primary` or `danger` |

For a button with `url`: "you'll still receive an interaction payload and will need to send an acknowledgement response" (same page). So a "Open in the app" link button still needs an `app.action` handler that calls `ack()` **(inference)**. `value` passes through Slack, so it should hold only IDs **(inference)**.

**Message limits.** Up to 50 blocks per message, and 100 in modals or Home tabs ([Block Kit](https://docs.slack.dev/block-kit)).

**Bolt routing** ([bolt-python README](https://github.com/slackapi/bolt-python/blob/main/README.md)):

| Decorator | Listens for |
|---|---|
| `app.action(action_id)` | Block Kit element actions |
| `app.view(callback_id)` | `view_submission` (`app.view_closed` for close) |
| `app.command(name)` | Slash commands |
| `app.event(type)` | Events API events, e.g. `app_home_opened` |
| `app.message`, `app.shortcut`, `app.options` | Messages, shortcuts, external select options |

Listener arguments include `ack`, `body`, `client`, `respond`, `say`, `logger` and `context` (same README).

**`ack()`.**

- "Actions, commands, shortcuts, options requests, and view submissions must always be acknowledged using the ack() function", because "you only have 3 seconds to respond before Slack registers a timeout error".
- "We recommend calling ack() right away before initiating any time-consuming processes".

([Bolt Python: Acknowledging requests](https://docs.slack.dev/tools/bolt-python/concepts/acknowledge))

**Slow work after `ack()`.**

- **Lazy listeners** (`ack=…, lazy=[…]`) are described as "a feature which make it easier to deploy Slack apps to FaaS" ([Bolt Python: Lazy listeners](https://docs.slack.dev/tools/bolt-python/concepts/lazy-listeners)). The page does not discuss long-running processes.
- **For a long-running process,** read from source:
  - `App` defaults to `process_before_response=False` ("True if this app runs on Function as a Service").
  - Its `listener_executor` defaults to `ThreadPoolExecutor(max_workers=5)` ([app.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/app/app.py)).
  - `ThreadListenerRunner.run` submits the listener to that executor. It then polls every 10 ms until `ack.response` is set and returns it, while the listener may still be running. Event listeners are acked immediately. ([thread_runner.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/listener/thread_runner.py))
  - So calling `ack()` first and then doing slow work in the same function is enough, with at most 5 listeners running at once by default **(inference from source)**.

**Tests and the network.**

- `App.__init__` calls `self._client.auth_test(token=…)` when `token_verification_enabled` is true, its default ([app.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/app/app.py)). Tests must pass `token_verification_enabled=False`, or a `client=` pointed at a stub.
- `App` also accepts `client` ("The singleton `slack_sdk.WebClient` instance for this app") (same file).

## 6. Identifying users

- **Which ID arrives where:**
  - Slash commands: `user_id` ([Implementing slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands))
  - `block_actions`: `user.id` ([block_actions payload](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload))
  - `view_submission`: `user.id` ([View interactions payload](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload))
  - `app_home_opened`: `event.user` ([app_home_opened](https://docs.slack.dev/reference/events/app_home_opened))
  
  These are Slack user IDs (`U…`), not our user IDs.
- **`users.info`.** Needs `users:read`, Tier 4. "The `users:read.email` OAuth scope is now required to access the `email` field." ([users.info](https://docs.slack.dev/reference/methods/users.info))
- **`users.lookupByEmail`.**
  - Needs `users:read.email`, Tier 3 ("50+ per minute").
  - Takes "An email address belonging to a user in the workspace".
  - Returns `users_not_found` for deactivated users.
  - "Custom bot users cannot use this method".
  - `users:read` alone does not grant email access.
  
  ([users.lookupByEmail](https://docs.slack.dev/reference/methods/users.lookupByEmail))
- **Mapping (inference).** Either keep a static map from Slack user ID to our user ID in config, or resolve once by e-mail and store the result. Both are deterministic. The static map needs no extra scope.
- **Trust in HTTP mode.** Requests are signed with the signing secret, and Bolt verifies them by default (`request_verification_enabled=True`) ([Verifying requests](https://docs.slack.dev/authentication/verifying-requests-from-slack); [app.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/app/app.py)).
- **Trust in Socket Mode.** The WebSocket URL is obtained with the app-level token, and Slack says "there's no need to verify or validate inbound events" ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)). Bolt skips signature checks for `socket_mode` ([request_verification.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/middleware/request_verification/request_verification.py)).
- **What the ID proves (inference).** Either way, the user ID shows who Slack says acted. It does not re-authenticate the person, so it is not an e-signature.

## 7. Files

**Upload method.**

- **Old method.** "The `files.upload` API method has been deprecated. It will stop functioning and be officially sunset on November 12, 2025." ([files.upload](https://docs.slack.dev/reference/methods/files.upload)) That date has passed.
- **Current flow** ([Working with files](https://docs.slack.dev/messaging/working-with-files)):
  1. `files.getUploadURLExternal`
  2. POST the bytes to the returned URL
  3. `files.completeUploadExternal`
  
  The SDKs wrap the three steps as "uploadV2". The Python SDK examples use `files_upload_v2` ([Python SDK: Web client](https://docs.slack.dev/tools/python-slack-sdk/web)).
- **Scope and tier.** Both methods need `files:write` and are Tier 4 ([getUploadURLExternal](https://docs.slack.dev/reference/methods/files.getUploadURLExternal); [completeUploadExternal](https://docs.slack.dev/reference/methods/files.completeUploadExternal)). `files:write` means "Upload, edit, and delete files as your Slack app" ([files:write](https://docs.slack.dev/reference/scopes/files.write)).

**Visibility and storage.**

- "If the `channel_id` is not specified, the file will remain private" ([completeUploadExternal](https://docs.slack.dev/reference/methods/files.completeUploadExternal)). Otherwise the file is shared to the given channels or users.
- Slack scans files for malware.
- Downloading `url_private` needs a token with `files:read`.
- `files.delete` removes a file.

([Working with files](https://docs.slack.dev/messaging/working-with-files))

**Retention.**

- Paid plans keep data "for the lifetime of your workspace by default".
- Free plans choose 90 days or one year.
- Files have their own retention setting, set by Workspace Owners.

([Customize data retention](https://slack.com/help/articles/203457187-Customize-data-retention-in-Slack)) On free plans, data older than one year is deleted ([Free plan limitations](https://slack.com/help/articles/27204752526611-Feature-limitations-on-the-free-version-of-Slack)).

**Data residency.**

- Available on Business+ and Enterprise plans, and enabled by the Primary Owner.
- Covers messages, uploaded files, and "App- or bot-generated messages and files".
- Member profiles and membership information may be stored outside the region.

([Data residency](https://slack.com/help/articles/360035633934-Data-residency-for-Slack))

**DLP.**

- Native DLP is "Available on **Enterprise** plans".
- It scans "messages, text-based files, and canvases sent by members of your organization".
- Not scanned: non-text files (images, video, audio), file names and files over 100 MB.
- The page does not say whether PDFs count as text-based, or whether app or bot content is scanned.

([Slack DLP](https://slack.com/help/articles/12914005852819-Slack-data-loss-prevention))

**HIPAA and PHI.**

- **Plans and BAA.** "On Enterprise plans, Slack can be configured to support HIPAA-compliant message and file collaboration". The customer must use an Enterprise plan and execute a BAA. ([Slack and HIPAA](https://slack.com/help/articles/360020685594-Slack-and-HIPAA))
- **Where PHI may appear.** Members "may not include PHI when using Slack features, excluding messages, files and file names", and names of private channels or DMs. (Same page.)
- **Third-party apps.** "Slack does not have a business associate agreement with any third-party application providers". (Same page.)
- **Patients.** Slack may not be used to communicate with patients, plan members, or their families or employers. (Same page.)
- **Enterprise Grid setup.** The resource page names Enterprise Grid as the supported plan. It lists the steps: sign the BAA, follow the "Requirements for HIPAA Entities" guide, and give Slack the list of orgs or workspaces using PHI. The customer needs "DLP solution, SSO solution, backup/archival". ([HIPAA-compliant collaboration](https://slack.com/resources/why-use-slack/hipaa-compliant-collaboration-with-slack))
- **Plan names differ.** The Help Center says "Enterprise plans"; the resource page says "Enterprise Grid".
- **Default terms.** The Customer-Specific Supplement (effective October 1, 2021), section "III. Healthcare Customers", starts: "Unless as otherwise set forth in Customer's Contract, Customer acknowledges that Slack is not a 'Business Associate'". It goes on to say that the Services aren't HIPAA compliant and the customer must not process PHI through them. ([Customer-Specific Supplement](https://slack.com/terms-of-service/supplement))

**Conclusion.**

- **What Slack's sources say.**
  - Uploading health documents is supported only on an Enterprise plan with an executed BAA.
  - It is otherwise excluded by the default supplement.
  - Slack's BAA does not extend to third-party apps.
  - Our app would be a custom app. Whether Slack counts a customer's own internal app as "third-party" for this sentence is not stated.
- **Synthetic files, from Slack's sources only.** Slack's HIPAA terms concern PHI. Nothing we read restricts uploading synthetic documents.
  - On a free workspace, normal retention applies: 90-day visibility and deletion after one year.
  - A file uploaded without a channel stays private; shared files are visible in the channels they are shared to.
- **For this demo: the project rule.** Our rule (no clinical content into Slack) excludes uploading redacted PDFs, whatever the plan. The UI inventory counts "document pages" as clinical content (`.scratch/slack-workflow/ui-inventory.md`). Using synthetic data doesn't change what the demo would show **(inference, project rule)**. Send a link to the document in our UI instead.
- **For a real deployment.** Upload would need at least an Enterprise plan, a signed BAA, the customer's own DLP/SSO/archiving, and a decision on whether our custom app is covered. With the hard rule in place, the question doesn't arise.

## 8. Rate limits

**Tiers** are per method, per workspace, per app ([Rate limits](https://docs.slack.dev/apis/web-api/rate-limits)):

| Tier | Rate |
|---|---|
| Tier 1 | 1+ per minute |
| Tier 2 | 20+ per minute |
| Tier 3 | 50+ per minute |
| Tier 4 | 100+ per minute |
| Special | Varies by method |

Slack recommends designing for "1 request per second for any given API call". (Same page.)

| Method | Tier | Source |
|---|---|---|
| `chat.postMessage` | Special: about 1 message per second per channel, plus a workspace-wide limit; short bursts tolerated | [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage); [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits) |
| `views.open` | Tier 4 | [views.open](https://docs.slack.dev/reference/methods/views.open) |
| `views.update` | Tier 4 | [views.update](https://docs.slack.dev/reference/methods/views.update) |
| `views.push` | Tier 4 | [views.push](https://docs.slack.dev/reference/methods/views.push) |
| `views.publish` | Tier 4 | [views.publish](https://docs.slack.dev/reference/methods/views.publish) |
| `files.getUploadURLExternal` / `files.completeUploadExternal` | Tier 4 | [getUploadURLExternal](https://docs.slack.dev/reference/methods/files.getUploadURLExternal); [completeUploadExternal](https://docs.slack.dev/reference/methods/files.completeUploadExternal) |
| `users.lookupByEmail` | Tier 3 | [users.lookupByEmail](https://docs.slack.dev/reference/methods/users.lookupByEmail) |
| `users.info` | Tier 4 | [users.info](https://docs.slack.dev/reference/methods/users.info) |
| `apps.connections.open` | Tier 3 | [apps.connections.open](https://docs.slack.dev/reference/methods/apps.connections.open) |
| Incoming webhooks | 1 per second, short bursts allowed | [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits) |
| Events API deliveries | 30,000 per workspace per app per 60 minutes; beyond that, `app_rate_limited` | [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits) |

**What a rate limit looks like.** An over-limit call returns "HTTP 429 Too Many Requests" with a `Retry-After` header giving seconds to wait. Other methods and workspaces are unaffected. ([Rate limits](https://docs.slack.dev/apis/web-api/rate-limits))

**Retries in the SDK and Bolt.**

- **Python SDK.** By default only `ConnectionErrorRetryHandler` is enabled. `RateLimitErrorRetryHandler` (since v3.9.0) "does retries when HTTP status 429 is returned" and is opt-in: `client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=1))`. Manually: catch `SlackApiError`, check `status_code == 429`, read `Retry-After`. ([Python SDK: Web client](https://docs.slack.dev/tools/python-slack-sdk/web))
- **Bolt.** It adds no retry handler of its own. `App` builds its client with `create_web_client(token=…, logger=…)` ([app.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/app/app.py)). That function sets only `token`, `logger` and `user_agent_prefix` ([utils.py](https://raw.githubusercontent.com/slackapi/bolt-python/main/slack_bolt/util/utils.py)). Rate-limit retries must be added by passing our own `client=` **(inference from source)**.

## 9. Free plan

**Limits that apply** ([Free plan limitations](https://slack.com/help/articles/27204752526611-Feature-limitations-on-the-free-version-of-Slack); [Message, file and app limits](https://slack.com/intl/en-ca/help/articles/115002422943-Message-file-and-app-limits-on-the-free-version-of-Slack)):

- **History:** "you'll be limited to the most recent 90 days of message and file history".
- **Deletion:** data older than one year is deleted, starting August 26, 2024.
- **Apps:** "You can add up to 10 third-party or custom apps". Our custom app counts toward that.
- **Workflows:** "you won't be able to create new ones" (Workflow Builder). This doesn't affect a Bolt app **(inference)**.
- **High volume:** "you may be prevented from sending too many messages or files in a short period of time".

**Not stated.** Neither free-plan page mentions Socket Mode, modals, App Home or slash commands. We found no Slack statement restricting them on free workspaces, but none confirming them either.

**Other plan-gated features.** Data residency (Business+ and Enterprise), DLP (Enterprise) and HIPAA (Enterprise plus BAA) are all unavailable on free (§7).

## 10. Draft app manifest

**Draft, built from the cited docs; not tested against Slack's manifest validator.** The structure follows the Socket Mode page's example (`_metadata`, `socket_mode_enabled: true`, no `request_url`) ([Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)). Field names and limits come from the manifest reference ([App manifest](https://docs.slack.dev/reference/app-manifest)).

```yaml
# DRAFT from docs.slack.dev (2026-11-17). Not validated.
_metadata:
  major_version: 1
display_information:
  name: Adjudication Demo            # max 35 chars
features:
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false   # reference: "whether or not the users can send messages to your app"; read-only when true (inference)
  bot_user:
    display_name: Adjudication Demo
    always_online: false
  slash_commands:
    - command: /adj                  # max 32 chars incl. "/"
      description: Show my adjudication tasks   # required
      usage_hint: "tasks | status <case>"
      should_escape: false
      # url omitted: Socket Mode; whether Slack requires it is not confirmed
oauth_config:
  scopes:
    bot:
      - chat:write
      - commands
      - im:history
      - users:read
      # - users:read.email   # only if mapping users by e-mail
      # files:write deliberately omitted: no uploads (see §7)
settings:
  event_subscriptions:
    bot_events:
      - app_home_opened
      - message.im
  interactivity:
    is_enabled: true
    # request_url omitted: Socket Mode; not confirmed whether Slack ignores or requires it
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

**What each line is for:**

| Item | Why | Source |
|---|---|---|
| `chat:write` | `chat.postMessage` to a user ID (opens the DM) or channel; also needed for the Messages tab | [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage); [App Home](https://docs.slack.dev/surfaces/app-home) |
| `commands` | Slash commands | [commands scope](https://docs.slack.dev/reference/scopes/commands) |
| `im:history` | Receive `message.im` when users type to the app in the Messages tab; optional per the App Home guide. Drop it, and the event, if the tab is read-only | [im:history](https://docs.slack.dev/reference/scopes/im.history); [App Home](https://docs.slack.dev/surfaces/app-home) |
| `users:read` | `users.info` to show names | [users.info](https://docs.slack.dev/reference/methods/users.info) |
| `users:read.email` (optional) | `users.lookupByEmail` for mapping to our user IDs | [users.lookupByEmail](https://docs.slack.dev/reference/methods/users.lookupByEmail) |
| No scope for `views.open/update/push/publish` | Each method page says "No scopes required" | [views.open](https://docs.slack.dev/reference/methods/views.open); [views.publish](https://docs.slack.dev/reference/methods/views.publish) |
| `app_home_opened` | Publish the Home tab on open; no scope required | [app_home_opened](https://docs.slack.dev/reference/events/app_home_opened) |
| `interactivity.is_enabled` | Buttons and modals; the Home tab's interactive components need it | [App Home](https://docs.slack.dev/surfaces/app-home); [App manifest](https://docs.slack.dev/reference/app-manifest) |
| App-level token with `connections:write` | Not in the manifest. Generate it under Basic Information > App-level tokens | [connections:write](https://docs.slack.dev/reference/scopes/connections.write); [Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode) |
| `chat:write.public` not requested | Only needed to post in public channels the bot hasn't joined | [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage) |

**Environment variables.** `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`, as named in the Bolt examples ([Bolt Python: Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode)). The `xoxb-`/`xapp-` prefixes are common knowledge, not verified on the pages cited here. No signing secret is needed in Socket Mode.

**Free text typed into the app's DM.** With a writable Messages tab and `message.im`, anything a user types to the app is stored in Slack as an ordinary DM message. So is the `text` of a slash command, which "can contain absolutely anything the user might decide to type". A read-only Messages tab and slash commands that take only case IDs keep that surface small **(inference)**.

## Open questions / not confirmed

- **Typed modal input.** Whether Slack stores or logs it (`view.state.values`) after submission, and for how long. No page we read says.
- **Slash commands in the app's own DM.** Whether they work in the Messages tab, and how `messages_tab_read_only_enabled` affects them. "Any conversation" suggests yes; not stated explicitly.
- **Socket Mode URLs.** Whether the manifest's slash-command `url` and `interactivity.request_url` must be set, or are ignored. The Socket Mode page says app settings don't "require or even allow" a Request URL. The manifest reference doesn't say per field.
- **`response_url` for slash commands.** Lifetime and use count: the "5 times within 30 minutes" figure is stated only on the general interactions page.
- **Home tab before first open.** Whether `views.publish` works for a user who has never opened App Home.
- **Free plan features.** Whether any restriction applies to Socket Mode, modals, App Home or slash commands on free workspaces. Slack's free-plan pages are silent.
- **`connections:write`.** The `apps.connections.open` page says "No scopes required"; the scope page and Bolt README say the app-level token needs `connections:write`.
- **BAA and custom apps.** Whether a customer's own internal custom app counts as a "third-party application provider" under Slack's HIPAA BAA statement.
- **DLP coverage.** Whether Slack DLP scans PDFs (as "text-based files") and content posted by apps or bots.
- **HIPAA plan naming.** "Enterprise plans" (Help Center) vs "Enterprise Grid" (resource page); and how the 2021 supplement interacts with a signed BAA. The supplement says only "Customer's Contract".
- **Trigger error codes.** `expired_trigger_id`/`exchanged_trigger_id` (method page) vs `trigger_expired`/`trigger_exchanged` (interactions page).
- **Input blocks per modal.** We found no cap other than the 100-block limit per view.
- **File storage.** Where uploaded files are stored by default, without data residency. Not checked.
- **Access to app DMs and files.** Whether workspace owners, or exports on the free plan, can read files and DM messages exchanged with the app. Not checked.
- **Lazy listeners.** Slack's docs say nothing about lazy listeners under Socket Mode. Our "ack then work in the same thread" reading comes from Bolt source (`thread_runner.py`), not from documentation.
