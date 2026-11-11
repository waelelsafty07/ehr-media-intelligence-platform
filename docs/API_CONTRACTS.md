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
