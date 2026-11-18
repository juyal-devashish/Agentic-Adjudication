# Clinical Content never leaves the app

**Status:** accepted.

Nothing sent outside the app carries Clinical Content: no Slack message, App Home, Slack form, command reply or e-mail. Allowed: case ID, site name, workflow status, the Task in words, people's names (only in their own messages), counts, the one-word outcome code suggests (pass, query or none), dates of workflow steps, and buttons and links into the app. Never: the AE term or its dates, subject or AE ID, seriousness, outcome or relatedness, any AI-written text (the AI Suggestion's summary included), query or more-data text, the Narrative, adjudication answers or committee decisions, document names or pages, identifiers even masked. No document is ever uploaded to Slack, redacted or not. Anything sent to Slack is stored by Slack and can come back to the app through it; Slack supports health data only on Enterprise plans under a BAA, which this project does not have; and a case ID is enough to open the case in the app, where access is controlled. The only free text typed into Slack is a reassignment reason, and its form says "No clinical details".

## Consequences

- Tests check every outgoing Slack payload against the forbidden list.
- The Safety Specialist's App Home shows the suggested outcome as one word; the AI Suggestion itself stays on the Quality check page.
