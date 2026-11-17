# Clinical event adjudication: Flows 1 to 4

Getting an adverse event from the trial database to a package an adjudication committee can review (documents collected from the site, identifiers removed, quality-checked and summarised), then adjudicating it and reporting the outcome back to the trial database.

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

## Adjudication

**Adjudicator**:
An independent clinician who reviews a case and answers the adjudication form. *Adjudicator 1* and *Adjudicator 2* review every case; *Adjudicator 3* is a third, different person who answers only the Disputed Fields.
_Avoid_: reviewer (alone), assessor

**Independent Review**:
One adjudicator's signed answers to the adjudication form, given without seeing anyone else's.
_Avoid_: assessment (alone), vote

**Disputed Field**:
A question on the form where the two Independent Reviews do not agree under that question's comparison rule (for example, dates more than a day apart).
_Avoid_: conflict, discrepancy

**Committee**:
The group of adjudicators that decides the fields Adjudicator 3 could not settle. It meets outside the tool; the CEC Coordinator records the meeting, and its Chair signs the Consensus.
_Avoid_: CEC meeting (for the group itself), panel

**Consensus**:
The committee's recorded decision on each open field, signed by its Chair.
_Avoid_: majority, verdict

**More Data Request**:
An adjudicator's or the committee's request for further documents. It reaches the site as a query; the new documents go through de-identification and the Quality Check before anyone reviews again.
_Avoid_: query (for the request itself), data clarification

**Round**:
One adjudication of a case, from assignment to Outcome. Re-adjudication starts the next Round; a More Data Request does not.
_Avoid_: cycle, iteration

**Outcome**:
The final answer to each compared question, with where it came from: both reviews agreed, Adjudicator 3 matched one of them, or the Consensus.
_Avoid_: result, decision (alone), verdict

**Superseded Outcome**:
An earlier Round's Outcome after a re-adjudication. It stays in the record unchanged, linked to the Outcome that replaced it.
_Avoid_: overwritten, cancelled outcome

**Re-adjudication**:
A new Round on a closed case, started by the CEC Coordinator with a reason.
_Avoid_: reopening, re-review

## Notifications

**Notification**:
One notice to one recipient about one case and task, holding no clinical content. Every Notification is kept in the outbox for good.
_Avoid_: alert, message (for the notice itself)

**Channel**:
A route that delivers Notifications to people: e-mail, Slack on trial, Teams later. The outbox is the record, not a Channel.
_Avoid_: transport, provider

**Delivery Attempt**:
One try at delivering one Notification through one Channel, recorded as sent or failed with the reason.
_Avoid_: send (as a noun), retry (for the first try)

**Clinical Content**:
Anything about the patient's event or care: the AE term and its dates, seriousness, outcome and relatedness, medical history, medication, labs, document pages, identifiers found in them, query text, the Narrative, AI-written text about the case, and adjudication answers. Never sent to Slack or put in a notification. Case IDs, site names, workflow status, task wording, people's names and counts are not Clinical Content.
_Avoid_: PHI (for this broader idea), medical data, sensitive data
