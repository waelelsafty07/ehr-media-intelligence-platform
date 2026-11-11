# AI evaluation: clinical summaries

**What this is and is not.** Automated checks reduce the risk of unsupported output. They do **not** establish
clinical correctness, safety or fitness for care. Nothing here has been validated with clinicians, and the live
OpenAI integration has **not** been exercised (no credentials were used; see "Verification status").

**Safety position.** A generated summary is AI output that requires review by a qualified clinician. It must not be
presented as a verified diagnosis or as medical advice. Evidence quotes establish textual traceability (the words exist in
a cited record), not clinical correctness: a correctly cited quote can still support a distorted or misleading paraphrase.

## Automated, deterministic checks (run on every generation and in the test suite)
Implemented in `app/domain/summary.py`; synthetic evaluation suite in `backend/tests/evaluation/`.

| Failure mode | Check | Outcome |
|---|---|---|
| Malformed / non-JSON / extra or missing fields | Safe parsing + strict Pydantic schema | rejected, re-asked |
| Summary longer than 200 words | Schema validator | rejected |
| Citing a record that is not this patient's (or does not exist) | Cited IDs must be in the patient-scoped context | rejected |
| Statement not traceable | `evidence_quote` must appear (normalized) in a **cited** record | rejected |
| Invented diagnosis | Diagnosis `name` must appear inside its own quote, which must be in the source | rejected |
| Uncertain finding presented as confirmed | Quote contains hedging (possible, suspected, probable, likely, rule out, query, ...) but `certainty` is not `uncertain` | rejected |
| History presented as current | Quote contains history markers (history of, previous, resolved, ...) but `temporal` is `current` | rejected |
| Negated finding listed as a diagnosis | Quote contains negation (no evidence of, denies, ruled out, ...) | rejected |
| Wrong/fabricated dates and numbers in prose | Numbers/dates with 2+ digits must occur in the source records | rejected |
| Treatment advice | Advice phrases (should start/stop, recommend, prescribe, ...) not present in the source | rejected |
| Truncated model output | `finish_reason == "length"` | rejected |
| Patient identity correctness | The model is never given identifiers; the summary's `patient_id` is set by the pipeline, not by the model | by construction |

Synthetic suite result (`test_summary_evaluation.py`): 13 of 13 known-bad outputs detected, 0 of 1 known-good output
falsely rejected. This measures the checks, on cases chosen by the author; it says nothing about real model output.

## Known limits (what the checks cannot catch)
- A quote proves the *words exist* in a cited record, not that the paraphrase (`text`, `summary_text`) is faithful.
  The suite includes a test documenting this: a distorted paraphrase attached to a real quote passes.
- `summary_text` is free prose. Only dates, numbers and advice phrases in it are checked; other unsupported statements
  in the prose can pass. Structured fields are verified more strongly than the prose.
- Marker lists (hedging, history, negation, advice) are English heuristics; negation or hedging phrased differently can
  be missed, and legitimate text can occasionally be rejected (it is then re-asked; after the attempt budget the
  request fails rather than serving an unverified summary).
- Omissions are not detected: important facts that the model leaves out cannot be found automatically.
- The quality indicator measures evidence coverage of the inputs, not correctness of the output.

## Human-review rubric (required before any clinical use; not performed)
Score each summary against the source records, 0 = fail, 1 = partial, 2 = pass.

1. **Factual consistency** - every statement is supported by the cited records.
2. **No unsupported content** - no diagnosis, medication, procedure, result or date that is not in the records.
3. **Uncertainty preserved** - hedged findings remain hedged; negated/ruled-out findings are not listed.
4. **Temporality** - current vs historical information is correctly distinguished; dates preserved.
5. **Completeness** - important documented facts (including anomalies) are not omitted; missing information is stated.
6. **Attribution** - quotes and record IDs correspond to the statements.
7. **Tone** - concise, neutral, no advice, no overstated certainty; <= 200 words.
8. **Identity** - nothing from another patient.
Record reviewer, date, model, prompt version and cases reviewed. A failure on 2, 3 or 8 is a blocking defect.

## Verification status
- Verified: prompts/validation/caching/retries/persistence with scripted fake providers; the OpenAI adapter against the
  real `openai` SDK over a mocked HTTP transport (request shape, parsing, error classification).
- **Not verified:** behavior against the live OpenAI API (model availability, JSON-mode behavior, real output quality,
  rejection rate of real outputs by the checks, latency). A human-reviewed pass over real outputs on synthetic
  records is the recommended next step.
