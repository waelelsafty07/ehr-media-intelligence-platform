# API contracts

Interactive OpenAPI: `http://localhost:8000/docs`. All errors use one envelope:
`{"error": {"code": str, "message": str, "request_id": str, "details": [..]?}}`. Internal exception text is never returned.
Every response carries `X-Request-ID`.

## GET /health
`200 {"status": "ok"|"degraded", "database": "ok"|"unavailable", "version": str}`

## POST /ingest (multipart)
Fields: `file` (.json/.csv/.txt), optional `format` (`json|csv|text`). `200` with per-record results
(`status`: completed | rejected | duplicate | needs_review | failed; `issues`; `patient_id`; `already_ingested`).
Errors: 413 `file_too_large`, 415 `unsupported_format`, 422 `ingestion_parse_error` / `validation_error`.

## FHIR (see FHIR_MAPPING.md)
| Method / path | Success | Errors |
|---|---|---|
| `POST /patients/{id}/fhir` | 200 `{status, unchanged, bundle_id, resource_counts, issues, updated_at}` | 404 `patient_not_found`, 422 `fhir_validation_failed` (details: path+code), 500 `fhir_generation_failed` |
| `GET /patients/{id}/fhir` | 200 Bundle (`application/fhir+json`) | 404, 409 `fhir_not_available` |
| `GET /patients/{id}/fhir/status` | 200 `{status: generated\|invalid\|failed\|not_generated, ...}` | 404 |

## Clinical summaries
### POST /patients/{id}/summary
Generates a grounded summary (or returns the cached one). Requires `EHR_OPENAI_API_KEY`.
`200`:
```json
{
  "summary_id": "uuid", "cache_hit": false, "is_stale": false, "stale_reasons": [],
  "summary": {
    "patient_id": "uuid",
    "chief_concern": {"text": "...", "evidence_quote": "exact words from a record", "source_record_ids": ["record-uuid"]},
    "key_diagnoses": [{"name": "angina", "certainty": "uncertain", "temporal": "unspecified",
                       "text": "...", "evidence_quote": "...", "source_record_ids": ["..."]}],
    "recent_records": [{"record_id": "...", "document_date": "2024-02-01", "excerpt": "...", "diagnoses": []}],
    "anomalies": [],
    "missing_information": ["Date of birth is not recorded."],
    "summary_text": "<= 200 words",
    "source_record_ids": ["every record shown to the model"],
    "confidence_or_quality_indicator": {"level": "low|medium|high", "factors": {"chief_concern_documented": true, "...": true},
                                        "explanation": "Heuristic evidence-coverage indicator ... NOT a model confidence ..."},
    "generated_at": "ISO-8601", "model_identifier": "gpt-4o-mini", "prompt_version": "clinical-summary-v1",
    "source_content_hash": "sha256", "disclaimer": "... not medical advice ..."
  }
}
```
**Safety notes.** The summary is AI-generated and requires qualified clinical review; it is not a verified diagnosis and not
medical advice. `evidence_quote`/`source_record_ids` give textual traceability, not clinical correctness (a real quote can
support a distorted paraphrase). The quality indicator is an evidence-coverage heuristic, not a probability or calibrated
confidence. Grounding heuristics are English-only. Live OpenAI behavior is unverified.

`certainty`: `stated` | `uncertain`. `temporal`: `current` | `historical` | `unspecified`.
`recent_records`, `missing_information`, `source_record_ids` and the quality indicator are computed deterministically,
not written by the model. Errors:

| Status | code | Meaning |
|---|---|---|
| 404 | `patient_not_found` | Unknown patient |
| 409 | `no_source_records` | No completed records with text or stated diagnoses |
| 502 | `summary_generation_failed` | After retries; `details: [{"reason": provider_unavailable \| provider_timeout \| provider_rejected \| invalid_output \| persistence_error}]`; recorded, retry by calling again |
| 503 | `ai_provider_not_configured` | No API key configured |

### GET /patients/{id}/summary
The latest generated summary. `is_stale` is true (with `stale_reasons` from `source_records_changed`,
`prompt_version_changed`, `model_changed`) when it no longer matches the current records or configuration.
404 `summary_not_found` if none has been generated (failed attempts are never served).

## Semantic search
> **Demo system: no authentication or authorization.** Anyone who can reach the API can search all patients. `patient_id` filtering is data
> scoping, not access control. See [SECURITY.md](SECURITY.md).

Index first (explicit, idempotent), then search. FHIR must be generated for a patient before its documents are indexed; summaries are indexed only
when generated and not stale.

**Embedding mode.** The default is semantic: `all-MiniLM-L6-v2` (`EHR_EMBEDDING_PROVIDER=sentence-transformers`), which needs the `embeddings` extra and a
one-time model download. If it is unavailable, search and indexing return `503 embedding_provider_unavailable` (message explains the fix). There is **no
silent fallback**. Lexical mode (`EHR_EMBEDDING_PROVIDER=hashing`) is an explicit choice. Every search response reports the actual mode.

### POST /patients/{id}/index  and  POST /search/reindex
Reconcile the derived index with SQLite for one patient / all patients. `200 {"patient_id", "indexed", "unchanged", "removed", "skipped": [...]}`
(`/search/reindex` returns totals plus per-patient `results`). `skipped` codes: `fhir_not_generated`, `document_without_text`,
`summary_not_available`, `summary_stale`. Errors: 404 `patient_not_found`, 503 `embedding_provider_unavailable`, 503 `search_index_unavailable`.

### GET /search/status
`200 {"entries_total", "by_resource_type", "by_model", "embedding": {"model_identifier", "kind", "available"}}`. `available: false` means the configured provider
cannot currently be used (e.g. the extra is not installed); searches will return 503. This call never loads the model.

### POST /search
Request (unknown fields rejected):
```json
{"query": "chest tightness", "patient_id": "optional", "resource_types": ["DocumentReference", "ClinicalSummary"],
 "date_from": "2024-01-01", "date_to": "2024-12-31", "limit": 5, "min_score": 0.2}
```
`query` 1-500 chars (whitespace-normalized); `limit` 1-5 (default 5); `date_from <= date_to`, both inclusive, and records without a date are excluded when a
bound is set; `min_score` can only raise the similarity floor (results with similarity <= 0 are never returned). Patient/type/date filters are applied
**before** ranking. `200`:
```json
{"query": "chest tightness", "returned": 3, "candidates_considered": 3, "stale_results_dropped": 0, "took_ms": 8.2,
 "embedding": {"model_identifier": "sentence-transformers/all-MiniLM-L6-v2", "kind": "semantic"},
 "note": "Scores are embedding similarity, not clinical relevance ...",
 "results": [{"rank": 1, "resource_type": "DocumentReference", "resource_id": "uuid",
              "fhir_reference": "DocumentReference/uuid", "link": "/patients/<id>/fhir",
              "patient_id": "uuid", "record_date": "2024-02-01",
              "score": 0.6492, "snippet": "...", "source_record_id": "uuid", "ai_generated": false}]}
```
- **No patient name, MRN or date of birth is returned** (tested, including the OpenAPI schema). `patient_id` is an opaque internal id; the `snippet` is an
  excerpt of stored clinical text and contains whatever that text contains. A client that needs a patient's identity must obtain it through a separate,
  explicitly authorized lookup (none exists yet; there is no patient listing endpoint).
- `resource_type` is `DocumentReference` (FHIR R4) or `ClinicalSummary` (AI-generated, `ai_generated: true`, `fhir_reference: null`, `link` ->
  `/patients/<id>/summary`; requires clinician review and is not a verified diagnosis).
- `score` is cosine similarity of embeddings. The semantic model returns weak runners-up for unrelated text (observed 0.07); use `min_score` to cut them.
  The score is **not** clinical relevance, diagnostic confidence or a probability.
- `embedding.kind` is `semantic` (sentence-transformers) or `lexical` (explicit hashing fallback: matches shared words only).
- Errors: 422 `validation_error` (details: location + rule, never the input), 404 `patient_not_found` (unknown `patient_id`),
  503 `embedding_provider_unavailable`, 503 `search_index_unavailable`.

## Focused FHIR resource
### GET /patients/{patient_id}/fhir/resources/{resource_type}/{resource_id}
Returns **one** stored, validated FHIR resource (`application/fhir+json`), not the Bundle. Only `DocumentReference` and `DiagnosticReport` are served this way; the `Patient`
resource (name, MRN, date of birth) is intentionally not retrievable here and stays available only in the full Bundle. Requires the patient's FHIR state to be `generated`.
Errors (envelope as elsewhere): `404 patient_not_found`; `409 fhir_not_available`; `404 fhir_resource_not_found` when the resource does not exist, is a type not served here, or
belongs to a different patient (the three cases are deliberately indistinguishable); `422` for ids over 64 characters. Added in Phase 6 so the UI does not download the Bundle.

## Patient display lookup

### GET /patients/{patient_id}/display
Purpose: let the clinician UI label a search result with a patient name and MRN **without** putting identifiers in `/search` responses (which stay free
of name, MRN and date of birth). The UI calls this separately, once per patient per page session.

`200 {"patient_id": "...", "patient_name": "Alex Test" | null, "mrn": "000101"}` — exactly these three fields. No date of birth, gender or clinical content.
`patient_name` is null when no name was recorded. `404 {"error": {"code": "patient_not_found", ...}}` for an unknown id; `422` for an id longer than 64 characters.
There is no `GET /patients` listing endpoint.

**Security limitation:** this project has no authentication or authorization. This endpoint is a display convenience for the local synthetic-data demo; it is
**not** an authorization mechanism, and anyone who can reach the API and knows (or obtains) a patient id can call it.
