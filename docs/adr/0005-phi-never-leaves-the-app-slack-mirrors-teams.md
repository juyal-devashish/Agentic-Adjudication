# PHI never leaves the app; Slack stands in for Teams

**Status:** accepted (plan P-58, user, 2026-11-18). Supersedes [ADR 0004](0004-clinical-content-never-leaves-the-app.md). **Basis:** the user's decision for the synthetic-data demo, taken when Slack became a conversation with an agent. It is not an approval from compliance, IT or a sponsor, and it covers synthetic data only.

The line for anything sent to Slack moves from Clinical Content to PHI: **anything without PHI may go to Slack**, and nothing with PHI may. PHI is what de-identification removes under the study's `deid_policy.yaml` (names, dates of birth, record and phone numbers, addresses and the like). Allowed in Slack: the AE term and event dates, the subject ID (a study code, not an identifier), EDC data, Checks, the AI Suggestion, the Narrative, query text, and redacted documents that passed verification. Never: anything from `documents/original/`, and no unverified or unredacted file. A case ID still opens the case in the app, where access is controlled. We chose this over ADR 0004's stricter rule because an agent that cannot say what it is talking about cannot do the work (a site cannot answer a Site Query it is not allowed to read), and because the product will move to Microsoft Teams, which pharma users already have under Microsoft's data-security commitments: Slack here mimics Teams. Real data waits for Teams, or for a Slack plan with a BAA.

One exception, for the demo only: with synthetic data, a site may upload **original** documents by attaching them to its conversation in Slack. The original then also stays in Slack. The upload is recorded, the app tries to delete the file from Slack afterwards and records whether that worked, and a failed delete never stops the upload.

## Consequences

- The rule is enforced in code, not only in the prompt: before the agent's reply is posted, the same detectors de-identification uses run over it, together with the case's known identifiers where there are any. If anything is found the reply is held back, the person gets "That answer may contain identifiers; open it in the app." with an *Open in the app* button, and the hold is recorded.
- The agent's tools read only redacted outputs, EDC snapshots and case records, never `documents/original/`, and only what the acting person can see in the app.
- Every turn with the agent goes to a hash-chained conversation log; anything the agent does is an ordinary case event, recorded as that person.
- Tests check every outgoing Slack payload against the kit's seeded PHI instead of ADR 0004's forbidden list.
- Signatures stay in the app (ADR 0003): Slack advises against asking for passwords in any app surface.
