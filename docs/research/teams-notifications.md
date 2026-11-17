# Teams notifications: options, permissions and IT asks

Checked on 2026-11-17. Sources are Microsoft Learn and the Microsoft 365 Developer Blog only. Each claim links to the page (and section) it comes from. Statements marked **(inference)** are ours, not Microsoft's.

## Question

How can a self-hosted Python app post workflow notifications with a deep link into Microsoft Teams, in a channel and/or a 1:1 chat? And what does IT have to grant for each option: Entra app registration, API permissions, admin consent, Teams admin center policies and licences?

## Our constraints

- `adjud/mailer.py` currently e-mails each notification over SMTP. A notification holds only a case ID, a task and a link such as `http://host:8501/qc?role=safety_specialist&case=DEMO01-0001`. AGENTS.md forbids clinical content.
- Recipients: a data manager and a Safety Specialist (internal), and **hospital site users (external to our tenant)**.
- The app runs in Docker Compose on a laptop or internal host. It is **not necessarily reachable from the internet**, and the UI link is plain `http` to a non-public host.

## Short answer

1. **Office 365 Connectors and connector-based Incoming Webhooks are gone.** Microsoft scheduled the shutdown from May 18 to May 22, 2026, and Learn now says they "were retired in May 2026". Don't build on them.
2. **Quickest demo: a Workflows (Power Automate) webhook.** Our runner POSTs an Adaptive Card to a URL, and the flow posts it to a channel or chat. It needs no inbound endpoint, no app registration and no admin consent. It does need the Workflows app allowed in Teams, and a person who owns the flow.
3. **Best production path for 1:1 notifications: a Teams bot (a "notification agent") sending proactive messages** with the Teams SDK for Python. Microsoft now offers a *Teams-managed* registration that needs no Azure subscription. It also offers a *socket mode* that needs no public endpoint, but socket mode is in preview and Microsoft recommends HTTPS endpoints for production.
4. **Microsoft Graph can't send chat or channel messages as an application** except for data migration. A Graph sender would have to act as a signed-in user. Graph *activity feed* notifications work app-only, but their link must point into Teams (a tab), not to our Streamlit URL.
5. **External site users are the hard part.** Teams apps don't work in 1:1 chats with external (federated) users. Guests reach apps only in limited ways. **E-mail likely stays the channel for site users** (inference). Teams then covers internal staff, or a channel that includes guests.

---

## 1. Incoming Webhook (Office 365 connector): retired

- **Status.** The schedule as Microsoft announced it, in the Update of 4/14/2026: "Office 365 Connectors will be progressively disabled on the following schedule: Rollout begins: May 18, 2026. Rollout completes: May 22, 2026. After these dates, Office 365 Connectors will no longer function. All connector-based webhooks must be migrated to Power Automate-based workflows before the rollout begins." ([devblog, Update 4/14/2026](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/))
- Earlier deadlines, all superseded by the dates above: the end of 2025 (Update 07/23/2024 and 10/16/2024), March 31, 2026 (Update 10/28/2025) and April 30, 2026 (Update 2/5/2026). Same post.
- Teams platform docs now say: "Office 365 Connectors in Teams, including connector-based Incoming Webhooks, were retired in May 2026. To receive webhook requests and post messages in Teams, use the Workflows app." ([Build webhooks and connectors](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors))
- New connector registration in the Connector developer portal is disabled ([Register your connector](https://learn.microsoft.com/en-us/outlook/actionable-messages/connectors-dev-dashboard)).
- **Verdict:** not an option.

## 2. Workflows app / Power Automate webhook

**What it is.** A Workflows flow whose trigger is "When a Teams webhook request is received". When the flow is saved, the trigger exposes a callback URL. Our code POSTs JSON to it, and the flow posts a message or card to a channel or chat. You can start from the template "Send webhook alerts to a channel" (channel menu, **Workflows**). ([Create Incoming Webhooks, "Create Webhooks using Workflows"](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook#create-webhooks-using-workflows); [Teams connector reference, "Microsoft Teams Webhook"](https://learn.microsoft.com/en-us/connectors/teams/#microsoft-teams-webhook))

**Payload.** `{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","contentUrl":null,"content":{…AdaptiveCard…}}]}`, or a legacy MessageCard. Only POST is accepted. ([Teams connector reference, "Request Body Schema"](https://learn.microsoft.com/en-us/connectors/teams/#microsoft-teams-webhook)) MessageCards render but their HttpPost buttons don't work; use Adaptive Cards for anything interactive ([Build webhooks and connectors, "Known issues"](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors)). Our only interaction is a link, so an Adaptive Card with `Action.OpenUrl` is enough.

**Channel vs chat.** The actions "Post message in a chat or channel" and "Post card in a chat or channel" can post as **User**, **Flow bot** or a Copilot Studio agent, into a channel or a group chat. As Flow bot, "Post in: **Chat with Flow bot**" with a **Recipient** posts "directly to a user". ([Send a message in Teams using Power Automate, "Post a message as the Flow bot directly to a user"](https://learn.microsoft.com/en-us/power-automate/teams/send-a-message-in-teams#post-a-message-as-the-flow-bot-directly-to-a-user); [Teams connector reference, "Post card in a chat or channel"](https://learn.microsoft.com/en-us/connectors/teams/#post-card-in-a-chat-or-channel)) The older "Post … as the Flow bot to a user / channel" actions are marked **[DEPRECATED]** in the connector reference, so don't use them.
- **Private channels: the sources conflict.** The platform page says "Workflows webhooks support private channels" ([Build webhooks and connectors, "Known issues"](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors)). The blog says private-channel selection in the Workflows app would be rolled out fully by April 20, 2026 ([devblog, Update 4/14/2026](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/)). But two other pages say the opposite:
  - The connector reference: "Posting a message or adaptive card to a private channel currently isn't supported" ([Teams connector reference, "General known issues and limitations"](https://learn.microsoft.com/en-us/connectors/teams/)).
  - The Power Automate page: "Sending a message in private channels isn't supported" ([Send a message in Teams, "Known issues and limitations"](https://learn.microsoft.com/en-us/power-automate/teams/send-a-message-in-teams#known-issues-and-limitations)).

  Use a standard channel, or test first.
- Messages show the default Workflows/Flow bot identity. The name and icon can't be customised. ([devblog, Update 4/14/2026, "Known Limitations"](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/))

**Inbound endpoint.** None on our side. Our runner calls out over HTTPS to `*.api.powerplatform.com`, or the legacy `*.logic.azure.com` ([IP address configuration for Power Automate](https://learn.microsoft.com/en-us/power-automate/ip-address-configuration#allow-users-on-your-network-to-use-power-automate)). This fits a laptop or internal host.

**Authentication of the trigger.** The trigger can allow "Anyone", "Any user in my tenant" or "Specific users in my tenant". With "Anyone", the URL itself is the secret, and you must *not* send an auth header. The other two options need an OAuth bearer token. ([Teams connector reference, "Parameters"](https://learn.microsoft.com/en-us/connectors/teams/#microsoft-teams-webhook); [Add OAuth authentication for HTTP request triggers](https://learn.microsoft.com/en-us/power-automate/oauth-authentication)) Getting that token from a headless runner would need its own Entra app registration (inference).

**URL churn.** Trigger URLs on `logic.azure.com` stopped working on November 30, 2025 and moved to `*.environment.api.powerplatform.com`. Since June 2, 2026, a newer URL form (`/direct/cu/<n>/workflows/...`) is also issued, and the old form keeps working. New URLs can be longer than 255 characters. ([Troubleshoot Power Automate triggers, "Changes to HTTP or Teams Webhook trigger flows…"](https://learn.microsoft.com/en-us/troubleshoot/power-platform/power-automate/flow-run-issues/triggers-troubleshoot#changes-to-http-or-teams-webhook-trigger-flows-on-logic-apps-environment-architecture)) So keep the URL in an environment variable, never in code.

**Ownership.** A workflow belongs to the user who created it, not to a team or channel. It becomes orphaned if that owner leaves and no co-owner is set. ([Build webhooks and connectors, "Limitations"](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors))

**Admin and policy needs.**
- The Workflows app must be available and set to "allow" in the Teams admin center. ([Teams connector reference, "Requires the Workflow app"](https://learn.microsoft.com/en-us/connectors/teams/))
- No Entra app registration and no admin consent, because the flow uses the owner's own Teams connection.
- **Power Platform data (DLP) policies can't block the Microsoft Teams connector.** It is on the "can't be blocked" list. ([Connector classification](https://learn.microsoft.com/en-us/power-platform/admin/dlp-connector-classification)) Connector *action* control only applies to blockable connectors ([Connector action control](https://learn.microsoft.com/en-us/power-platform/admin/connector-action-control)). Whether a tenant can switch off the "Anyone" option of this trigger is **unconfirmed**.

**Licensing.** The Microsoft Teams connector is classed **Standard** in Power Automate ([Teams connector reference](https://learn.microsoft.com/en-us/connectors/teams/)). Users with a Microsoft 365 licence can use standard connectors ([Power Automate licensing FAQ](https://learn.microsoft.com/en-us/power-platform/admin/power-automate-licensing/faqs)). So no Power Automate Premium licence should be needed **(inference)**: Microsoft doesn't state the licence class of this specific trigger.

**Limits.**
- Trigger: about 1,000 concurrent inbound calls. Invoke calls per 5 minutes: 4,500 for the "Low" performance profile, 45,000 for all others.
- Connector: 25 non-GET Flow bot requests per connection per 300 s, and 100 API calls per connection per 60 s.
- Messages are limited to about 28 KB.

([Teams connector reference, "Throttling Limits" and "Known issues… with actions"](https://learn.microsoft.com/en-us/connectors/teams/)) We expect a few messages a day (our estimate), far below these.

**External and guest reach.** The webhook posts wherever the flow targets. A channel in a team that has guests is visible to those guests (inference from [Guest access in Teams](https://learn.microsoft.com/en-us/microsoftteams/guest-access)). Whether "Chat with Flow bot" can address a guest or an external user is **unconfirmed**. The connector reference says "Create a chat… doesn't have support for guest users" ([Teams connector reference](https://learn.microsoft.com/en-us/connectors/teams/)).

**Demo effort (our estimate).** About an hour, and no IT ticket if the Workflows app is already allowed. The Python side is one `httpx`/`urllib` POST next to `Mailer`.

## 3. Teams bot / notification agent: proactive messages

**What it is.** A Teams app with a bot. The bot sends "proactive" messages (not replies) to a 1:1 chat, a group chat or a channel where the app is installed. Messages can carry Adaptive Cards. ([Proactive messages](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages))

**SDK choice for Python, as of today.**
- The Bot Framework SDK, Python included, is archived. Support tickets stopped being serviced on December 31, 2025. ([Bot Framework additional resources](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-resources-links-help?view=azure-bot-service-4.0))
- Its successors are the **Microsoft 365 Agents SDK** (Python supported) and, for Teams-only agents, the **Teams SDK** (`microsoft_teams.apps`). (Same page; [BF → Agents SDK for Python](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/bf-migration-python))
- In the Teams SDK for Python, proactive sending is `await app.send(conversation_id, activity)`. ([Proactive activities (Python)](https://learn.microsoft.com/en-us/microsoftteams/platform/botbuilder-migration/proactive-activities); [App.send reference](https://learn.microsoft.com/en-us/python/api/microsoft-teams-apps/microsoft_teams.apps.app?view=msteams-sdk-python-latest))

**Prerequisite: the app must be installed for the recipient.** The bot can message a user only if the app is installed for that user personally, or in a team the user belongs to. For a group chat or team, the app must be installed there first. ([Proactive messages](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)) To get the conversation ID, either capture it from the install event, or install via Graph and read `GET /users/{id}/teamwork/installedApps/{installationId}/chat`. ([Send a personal welcome message](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/proactive-bots-and-messages/send-personal-welcome-message))

**Registration: two kinds** ([Choose an ACS registration type, "Compare registration options"](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/choose-agent-registration-type#compare-registration-options)):
- **Teams-managed:** "do not require an Azure subscription". You create it in Teams Developer Portal or with the Teams developer CLI (`teams app create`). Only the creating Microsoft 365 account can manage it, and it doesn't support SSO/OAuth. Microsoft recommends starting here.
- **Azure-managed** (an Azure Bot resource): needs an active Azure subscription. Use it when you need SSO/OAuth or Azure governance. Its pricing wasn't checked here.
- `teams app create` also creates an **Entra ID app registration with a client secret** in our tenant ([Registering an agent, "Registering an agent with Teams developer CLI"](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/registering-agent-teams-platform)). So the developer needs the right to register applications, or IT creates the registration.
- **Ownership risk:** a Teams-managed registration "can be configured and managed only by the Microsoft 365 account used to create it". It is the same single-owner risk as a Workflows flow, so create it with a team-owned account, or migrate it to Azure-managed later (the CLI can migrate between the two).

**App manifest.** It is the JSON file that makes the agent an installable Teams app. It carries the name and description, the developer info, the bot ID (shared by its ACS and Entra registrations), platform configuration for certain features, and any RSC permissions. It is registered in Teams Developer Portal, then distributed through the org catalog or the Store. ([Registering an agent, "App manifest: define and distribute"](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/registering-agent-teams-platform#app-manifest-define-and-distribute))

**Inbound endpoint.**
- *HTTPS endpoint mode* (generally available) needs a public HTTPS endpoint, or a tunnel such as dev tunnels or ngrok for development.
- *Socket mode* (**public preview**, only for newly created bots) uses outbound WebSockets on port 443. It needs "No inbound connectivity, no tunnel, no public URL." It isn't allowed for Teams Store distribution, and Microsoft recommends HTTPS for production. Outbound hosts to allow: `login.microsoftonline.com`, `*.botapi.skype.com`, `*.service.signalr.net` (WebSocket upgrade, long-lived connections) and the `serviceUrl` host (for example `smba.trafficmanager.net`).
- Sources: [Choose how your agent receives activity data](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-connectivity); [Configure the delivery method](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/configure-delivery-methods-for-agents)
- Sending itself is always an outbound HTTPS call to the service, whichever mode you use ([agent-connectivity](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-connectivity)). A *send-only* bot still needs to learn conversation IDs. Graph-based install plus a lookup can avoid relying on inbound install events **(inference; not tested)**.

**App distribution and Teams admin policies.**
- **Sideloading (custom app upload)** is for development. It needs "Upload custom apps" on in the user's app setup policy, plus the org-wide "Let users interact with custom apps in preview". Each user then installs the app themselves. ([Manage custom apps, "Allow users to upload custom apps"](https://learn.microsoft.com/en-us/microsoftteams/teams-custom-app-policies-and-settings#allow-users-to-upload-custom-apps))
- **Org catalog:** an admin uploads the package in Teams admin center, **Teams apps** > **Manage apps**. Access is then governed by app permission policies or app centric management and the app's allow/block status ([same page, "Understand how all the custom app settings work together"](https://learn.microsoft.com/en-us/microsoftteams/teams-custom-app-policies-and-settings#understand-how-all-the-custom-app-settings-work-together); [App centric management](https://learn.microsoft.com/en-us/microsoftteams/app-centric-management)).
- An admin can pre-install the app for users through an app setup policy ([App policies](https://learn.microsoft.com/en-us/microsoftteams/app-policies)).
- **Graph can proactively install only apps in the org catalog or the Teams Store, not sideloaded ones.** That install needs the *application* permission `TeamsAppInstallation.ReadWriteSelfForUser.All` (and `…ForTeam.All` for teams), with **admin consent**, plus `webApplicationInfo` in the manifest. ([Proactively install your bot](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/proactive-bots-and-messages/graph-proactive-bots-and-messages); admin consent "Yes" in [Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference))

**Limits.** Per bot per conversation: 7 sends per second, 60 per 30 s, 1,800 per hour. Per app per tenant: 50 requests per second. Retry 429, 412, 502 and 504 with exponential backoff. ([Rate limiting for agents](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/rate-limit))

**External and guest reach** ([Teams apps for external attendees or guests](https://learn.microsoft.com/en-us/microsoftteams/apps-external-users)):
- *Guests* (B2B accounts in our tenant), quoting the source:
  - "Any bot included in the global default app setup policy is also added for guests."
  - "If you preinstall a bot for your users using app setup policy, guests can't access such a bot."
  - Guests "can use apps added by other org users in personal chats, group chats, and teams and channels", and in a channel they "can mention the bot and interact with Adaptive Cards".
  - The two setup-policy sentences pull against each other. **Our reading:** pre-installing through the *global* policy reaches guests, and custom policies don't.
- *External access (federated) users*: "Apps in one-on-one chat with external users aren't supported." Bots work only in group or meeting chats that an internal user created and added the bot to.
- So a 1:1 bot message to a hospital user looks possible only if that user is a **guest** in our tenant, and the bot is in the global setup policy or added to a shared channel or chat (our reading).

**Demo effort (our estimate).** Half a day to a day with the Teams CLI quickstart ([Quickstart (Python)](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/quickstart-create-agent-teams-sdk)) and socket mode. It requires custom-app upload to be enabled for the demo accounts.

## 4. Microsoft Graph: sending chat or channel messages

- **Application permissions only for migration.** `POST /chats/{id}/messages` and `POST /teams/{id}/channels/{id}/messages` accept `Teamwork.Migrate.All` as their only application permission. The channel page says: "Application permissions are only supported for migration." ([Send message in a chat](https://learn.microsoft.com/en-us/graph/api/chat-post-messages?view=graph-rest-1.0); [Send chatMessage in channel](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0)) So a background runner can't post as itself.
- **Delegated permissions** are `ChatMessage.Send` and `ChannelMessage.Send`. Neither *requires* admin consent ([Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)), but the tenant may have turned user consent off ([Configure user consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent)). The message appears **as the signed-in user**, so the runner would hold a refresh token for a service account **(inference)**.
- The chat API **can't create a chat**: the chat must already exist ([Send message in a chat](https://learn.microsoft.com/en-us/graph/api/chat-post-messages?view=graph-rest-1.0)).
- Microsoft also says: "It's a violation of the terms of use to use Microsoft Teams as a log file. Only send messages that people will read." (same pages)
- Adaptive Card attachments are supported ([Create chatMessage, Example 3](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0)).
- **Limits:** channel POST is 50 rps per app per tenant, 1 rps per channel and 1 rps per user. Chat POST is 20 rps per app per tenant, 1 rps per chat and 1 rps per user. ([Graph throttling limits, "Microsoft Teams service limits"](https://learn.microsoft.com/en-us/graph/throttling-limits#microsoft-teams-service-limits))
- **Inbound endpoint:** none needed. A one-time interactive sign-in (for example device-code flow) is needed to get the delegated token **(inference)**.
- **Verdict:** possible for a demo, but it posts as a person and needs a stored user token. A weak fit for an automated, auditable runner.

## 5. Microsoft Graph: activity feed notifications (`TeamsActivity.Send`)

**What it is.** A Teams Activity feed entry with an OS pop-up, not a chat message. It is sent to a user, a chat or a team, or in bulk to up to 100 users. There are **no Adaptive Cards**. ([Send activity feed notifications](https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications); comparison table in [Build webhooks and connectors](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors))

**Requirements** ([Send activity feed notifications, "Requirements…"](https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications)):
- A Teams app whose manifest lists our Entra app ID under `webApplicationInfo`. Manifest 1.7 or later.
- The app **installed for the recipient**, personally or in a team or chat they belong to.
- Permissions: delegated `TeamsActivity.Send` (no admin consent), or application `TeamsActivity.Send` (**admin consent required**). Microsoft recommends the RSC permission `TeamsActivity.Send.User` declared in the manifest. ([userTeamwork: sendActivityNotification](https://learn.microsoft.com/en-us/graph/api/userteamwork-sendactivitynotification?view=graph-rest-1.0); [Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference))

**Deep link restriction.** With a text topic, `webUrl` "must start with the Microsoft Teams domain (teams.microsoft.com, for example)" ([Send activity feed notifications, Example 3](https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications)). So the notification can't open `http://host:8501/...` directly. It must open a Teams tab (see §6) that hosts the UI.

**Limits.** `POST /users/{id}/teamwork/sendActivityNotification`: 5 rps per app per tenant. The bulk endpoint: 2 rps. ([Graph throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits#microsoft-teams-service-limits))

**Inbound endpoint.** None for sending. But the tab it links to needs HTTPS (§6).

**Mobile.** "To show activity feed notifications on iOS and Android clients, the app must be included in the allow list. Only third-party apps are supported." ([Send activity feed notifications, "Send activity feed notifications to users"](https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications)) What this means for an org-catalog (custom) app is **unconfirmed**. It may show on desktop only.

**External and guest reach.** Not documented for guests or external users: **unconfirmed**.

**Verdict:** a native alert on desktop for internal staff. But it brings the whole Teams-app-plus-HTTPS-tab burden, and mobile delivery for a custom app is uncertain.

## 6. Deep links and Adaptive Card `Action.OpenUrl`

- In a card, use `Action.OpenUrl`. Microsoft recommends it over raw links, because raw links and hyperlinks inside a card open in the browser. ([Configuration and handling of deep links, "Adaptive Card"](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-links-execution-handling#chat-message))
- **Deep link to a tab:** `https://teams.microsoft.com/l/entity/<appId>/<entityId>?webUrl=<fallback>&label=<label>&context={"subEntityId":"<id>"}`. The tab reads `subEntityId` (`page.subPageId` in TeamsJS v2) from its context, which would carry our case ID and task. Links must stay under 2,048 characters on Windows. ([Deep link to an application, "Deep link to browse within your app" and "Best practices"](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-application#deep-link-to-browse-within-your-app))
- **Tab hosting requirements:** pages must be on HTTPS. CSP `frame-ancestors` must allow `teams.microsoft.com`, `*.teams.microsoft.com` and `*.cloud.microsoft`. The page must load TeamsJS and call `app.initialize()`. Domains go in `validDomains`. Sign-in pages don't render in the iframe. ([Tab requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/tab-requirements))
- Whether the Streamlit UI can meet these, given its iframe/CSP headers and the TeamsJS init it would need, is **unconfirmed**. Likely it needs a small wrapper page **(inference)**.
- **Linking straight to `http://host:8501`** from `Action.OpenUrl` should simply open the user's browser, and it resolves only where that host is reachable **(inference: no Microsoft source found on `http`-scheme or private-host URLs in `Action.OpenUrl`)**. Sites outside our network couldn't reach a laptop-hosted UI however they're notified.

---

## Comparison

| | Workflows webhook | Teams bot (Teams SDK, proactive) | Graph chat/channel message | Graph activity feed |
|---|---|---|---|---|
| Status | GA; replaces connectors | GA (HTTPS); socket mode preview | GA | GA |
| Channel post | Yes | Yes (app installed in team) | Yes (delegated only) | No (feed entry, can target team members) |
| 1:1 chat | Yes, "Chat with Flow bot" + Recipient | Yes (app installed for user) | Yes, existing chat only, as the user | Feed, not chat |
| Adaptive Card + `Action.OpenUrl` | Yes | Yes | Yes | No (text + Teams-domain link only) |
| Link straight to `http://host:8501` | Yes (opens browser) | Yes (opens browser) | Yes | **No**, must link to a Teams tab |
| Public inbound endpoint | No | HTTPS mode: yes. Socket mode: no (preview) | No | No (but the tab needs HTTPS hosting) |
| Entra app registration | No | Yes (created by `teams app create`) | Yes | Yes |
| Admin consent | No | Only if Graph proactive install is used (`TeamsAppInstallation.ReadWriteSelfFor*.All`) | No for `ChatMessage.Send`/`ChannelMessage.Send`, unless user consent is off | App-only `TeamsActivity.Send`: yes. RSC/delegated: no |
| Teams admin center | Workflows app allowed | Custom upload (demo) or org-catalog upload + app availability (prod) | None | Custom upload or org catalog (the Teams app is required) |
| Licence / Azure | M365 licence; Teams connector is Standard | None with Teams-managed registration; Azure subscription only for Azure-managed | None | None |
| Guests | Channel with guests: yes. 1:1: unconfirmed | Guests: only if bot in global setup policy or shared chat/channel | Unconfirmed | Unconfirmed |
| External (federated) users | Unconfirmed | **No 1:1**; group chats only | Unconfirmed | Unconfirmed |
| Sends as | Flow bot or flow owner | The bot | A real user | The app |
| Key limits | 4,500/45,000 invokes per 5 min; ~28 KB per message | 7/s, 60/30 s, 1,800/h per conversation; 50 rps per tenant | 1 rps per chat/channel/user | 5 rps per tenant |
| Demo effort (our estimate) | ~1 hour | ~0.5–1 day | ~0.5 day | ~1–2 days (app + HTTPS tab) |

## Recommendation

**(a) Quick demo.** Add a `TeamsWebhookSender` next to `Mailer`. It POSTs an Adaptive Card ("Case DEMO01-0001: QC review ready", one `Action.OpenUrl` button to the existing UI link) to a Workflows webhook URL read from an environment variable. Use one flow that posts to a demo channel (internal staff, plus guests if wanted), or one "Chat with Flow bot" flow per internal recipient. Keep e-mail for site users. Content stays within AGENTS.md: case ID, task and link only.

**(b) Production.** A single-purpose Teams notification bot (Teams SDK for Python), published to the **org catalog** and pre-installed through an app setup policy, sending 1:1 proactive cards to internal users.
- **If IT can expose an HTTPS endpoint** (reverse proxy to the container), use HTTPS delivery. It is GA and Microsoft's production recommendation.
- **If the host can't be reached from the internet,** there are three paths, all with caveats:
  1. A send-only bot: install through Graph, look up the chat ID, then only *send* outbound. This follows from the docs but is untested.
  2. Socket mode: preview, new bots only, and Microsoft doesn't recommend it for production.
  3. Stay on the Workflows webhook, which is fully outbound, with a team-owned flow and named co-owners.
- Start with a Teams-managed registration created by a **team-owned account**, because only the creator can manage it. Move to Azure-managed if IT requires Azure governance, or if SSO is added.
- For site users, keep e-mail, unless the sponsor onboards them as **guests** and puts the bot in the global setup policy. External-access users can't get 1:1 bot messages at all.
- Activity-feed notifications are a later nicety. They need the UI hosted as an HTTPS Teams tab.

## Ask list for IT

**Option A: Workflows webhook (demo)**
1. Confirm the **Workflows** app is allowed in Teams admin center (**Teams apps** > **Manage apps**) and for our users' permission policy, or under app centric management.
2. Confirm the tenant allows a "When a Teams webhook request is received" trigger set to **"Anyone"**, or tell us which auth mode is required.
3. Name a **co-owner** for the flow, so it isn't orphaned.
4. Allow outbound HTTPS from the app host to `*.api.powerplatform.com`. Old `logic.azure.com` trigger URLs stopped working on November 30, 2025, but Microsoft's allow-list still names `*.logic.azure.com` for HTTP triggers.
5. Confirm guests may be added to the demo team, if site users are to see channel posts.

**Option B: Teams bot (production)**
1. Let the developer register an Entra app (or IT creates it), with a client secret or certificate, single tenant.
2. Choose a Teams-managed registration (no Azure), created by a **team-owned or service account**, since only the creating account can manage it. Or provide an Azure subscription and resource group for an Azure Bot resource.
3. For the demo: turn on **"Upload custom apps"** in an app setup policy for the developer and demo users, and **"Let users interact with custom apps in preview"** in org-wide app settings.
4. For production: upload the app package to the **org catalog**, make it available to the target users (permission policy or app centric management), and **pre-install** it through an app setup policy. For site *guests*, it has to be the **global** setup policy.
5. If we install via Graph: grant **admin consent** for application permission `TeamsAppInstallation.ReadWriteSelfForUser.All`.
6. Networking: either a public HTTPS messaging endpoint (reverse proxy to the container), or, for development only, outbound 443 to `login.microsoftonline.com`, `*.botapi.skype.com`, `*.service.signalr.net` (WebSockets) and `smba.trafficmanager.net`.
7. Confirm the guest/external policy for hospital users: will they be **guests** in our tenant, or external-access users?

**Option C: Graph delegated messages**
1. An Entra app registration with delegated `ChatMessage.Send` and/or `ChannelMessage.Send`. User consent must be allowed, or an admin grants consent.
2. A **service account** (with a Teams licence) whose identity the messages appear under. Confirm this is acceptable under the tenant's policies.

**Option D: Graph activity feed**
1. All of Option B's app-catalog items (a Teams app is required), plus `webApplicationInfo` pointing at our Entra app.
2. Either admin consent for application `TeamsActivity.Send`, or approval of RSC `TeamsActivity.Send.User` in the manifest.
3. An HTTPS hostname and TLS certificate for the UI tab, with CSP `frame-ancestors` allowing Teams domains.

## Unconfirmed

- Whether a Workflows "Chat with Flow bot" or "Post card" action can reach **guest** or **external** users in a 1:1 chat.
- Private-channel posting through Workflows. The platform page and the blog say yes; the connector reference and the Power Automate how-to say no.
- The support status of the **Teams SDK for Python** (`microsoft_teams.apps`): GA or preview, and its support policy. The production recommendation rests on it; the M365 Agents SDK for Python is the documented fallback.
- Activity feed notifications on **mobile** for an org-catalog custom app ("Only third-party apps are supported" in the allow list).
- How to read the guest rules for bots in app setup policies (global policy vs custom pre-install). See §3.
- Whether a tenant setting or DLP policy can disable the **"Anyone"** auth option on the Teams webhook trigger. DLP can't block the Teams connector itself.
- The exact licence class of the "When a Teams webhook request is received" trigger. Our "Standard, no Premium needed" rests on the connector-level class.
- How Teams handles `Action.OpenUrl` to a plain-`http`, non-public host (`http://host:8501`). We expect it just opens the browser, but found no Microsoft statement.
- Whether a **send-only** bot can be fully run without receiving the install event, via Graph install plus `installedApps/{id}/chat`. Plausible from the docs; not tested.
- Socket mode general-availability date. It is preview and "only available for newly created bots"; no GA date is published.
- Azure Bot Service pricing for the Azure-managed path. Not checked.
- Whether Streamlit can be embedded as a Teams tab (CSP, TeamsJS init). Not checked.
- Graph activity feed behaviour for guest recipients.
