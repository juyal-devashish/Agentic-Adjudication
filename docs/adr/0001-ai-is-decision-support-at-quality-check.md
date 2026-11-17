# AI is decision support at the Quality Check; a person decides and signs every outcome

At the Quality Check the AI drafts the Narrative and prepares an AI Suggestion (which documents are still needed, whether the documents match the EDC event, and why), but the Safety Specialist decides every outcome, pass or Site Query, and signs it, on every case. No AI answer moves a case forward on its own. We chose this over letting a gated AI answer pass or query a case automatically because relying on AI alone would require full SDLC validation and UAT, with about 99.99% correct results over about 1,000 repeated runs, which is too costly for now (Nirav Darji, demo review of 2026-11-16, agreed by all). Conventional code still does the deterministic steps (import, eligibility, notifications), AI plus rules do redaction, and AI drafts; a person signs. This makes the plan's P-37 binding for the Quality Check and records why.

## Consequences

- Automating a Quality Check outcome later means reopening this decision and paying for that validation first.
- The workflow must still complete with AI off: the Safety Specialist then reviews the Checks and writes the Narrative from the template.
