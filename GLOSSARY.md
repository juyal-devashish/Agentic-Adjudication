# Clinical event adjudication: Flows 1 and 2

Getting an adverse event from the trial database to a package an adjudication committee can review: documents collected from the site, identifiers removed, quality-checked and summarised. Adjudication itself (Flow 3 onwards) is not part of this context.

## Safety review

**Quality Check**:
The Safety Specialist's review of a submitted case's documents: are they complete, and do they describe the adverse event recorded in the EDC? It is not adjudication; no medical judgement on the event itself is made.
_Avoid_: QC review (as a separate step), adjudication, document check

**Check**:
One deterministic test run by code on a submission, ending passed, failed or not sure (e.g. required documents present, subject ID found).
_Avoid_: rule, validation

**AI Suggestion**:
The AI's findings on a case, prepared for the Quality Check, each with its reasoning and quoted passages. It supports the Safety Specialist's review and never decides.
_Avoid_: AI decision, AI verdict, AI proposal

**Finding**:
One part of the AI Suggestion with its own status (OK, problem, not sure): either which further documents are needed, or whether one EDC field matches the documents.
_Avoid_: result, flag

**Required Document**:
A document type the study's checklist demands for this kind of event. A missing Required Document makes the Suggested Outcome a Site Query; a document the AI says "would help" never does.
_Avoid_: mandatory document, missing document (for a "would help" one)

**Suggested Outcome**:
The pass or query pre-selected for the Safety Specialist, derived from the Checks and Findings. There is none when a Check, or the Finding on the event term or onset date, is not sure.
_Avoid_: proposal, recommendation, decision

**Site Query**:
The Safety Specialist's signed request to the site for more documents or a correction, with reasons and a message. It sends the case back to the site.
_Avoid_: query (unqualified), rejection, fail

**Dossier**:
The single, hashed PDF of a case for adjudicators: cover, EDC summary and the redacted documents. Built before the Quality Check; a passed Quality Check fixes its version.
_Avoid_: package, bundle

**Narrative**:
The short, facts-only summary of the case every adjudicator reads, signed by the Safety Specialist. Only the signed Narrative goes to the next phase.
_Avoid_: case summary, narrative draft (for the signed text)

**Narrative Draft**:
The AI's (or the study template's) starting text for the Narrative, each statement citing a Dossier page. Kept alongside the signed Narrative.
_Avoid_: AI narrative (for the signed text)

**Narrative Edit**:
The difference between the Narrative Draft and the signed Narrative, shown as a comparison of the two.
_Avoid_: revision, correction
