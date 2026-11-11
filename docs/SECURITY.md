# Security and privacy position

**This is a local assessment/demo system. Use synthetic data only. Do not use it with real patient data.**
No security guarantee is made. No HIPAA (or other) compliance claim has been made or assessed.

## What is NOT implemented
- **No authentication.** Nothing identifies the caller.
- **No authorization.** There are no roles, no patient-level access policies, no consent or break-glass logic. Anyone who can reach the API can call
  every endpoint and read or modify all data.
- No rate limiting, no TLS termination, no encryption at rest (SQLite file and logs are plain local files), no secrets manager, no tenant separation.
- No fake or cosmetic authentication was added: a placeholder that looks like access control would be misleading.

## "Patient filtering" is data scoping, not access control
`patient_id` filters in search, summaries and FHIR retrieval separate one patient's data from another's *within a request that the caller chose to
scope*. They do not decide whether the caller is *allowed* to see that patient. Search without `patient_id` deliberately spans all patients. Three
layers keep a *scoped* search from returning another patient's data by mistake (SQL `WHERE`, a use-case check, and a final ownership re-validation
against SQLite); these are correctness controls, not authorization.

## What each endpoint exposes (as implemented)
| Endpoint | Direct identifiers returned | Clinical content returned |
|---|---|---|
| `POST /ingest` | opaque `patient_id` / record ids; validation messages never contain values | no |
| `GET /patients/{id}/fhir` | **Yes: full FHIR `Patient` (name, MRN, date of birth, gender)** | document text (base64) and diagnoses |
| `POST/GET /patients/{id}/summary` | opaque `patient_id` only | AI-generated summary text, quotes, diagnoses |
| `POST /search`, `GET /search/status`, index endpoints | opaque `patient_id` only. **Search results contain no name, MRN or date of birth** | `snippet` (an excerpt of stored text; it contains whatever the text contains) |

The opaque `patient_id` is a random UUID. It is an identifier, not a secret, and it is returned to callers. There is no patient listing endpoint, but
that is not a protection (ids are returned by `/ingest` and `/search`).

## Data flows to third parties
- **AI summaries:** when `EHR_OPENAI_API_KEY` is set, clinical text and stated diagnoses (never name, MRN, DOB or patient id) are sent to OpenAI.
  Real data would require agreements and a compliance assessment. With no key, nothing is sent anywhere.
- **Embeddings:** computed locally (`all-MiniLM-L6-v2` or the lexical fallback); no text is sent to an external service. The model is downloaded once from
  the Hugging Face Hub (no patient data is involved in that download).

## Other controls that do exist (and their limits)
- Upload size limit (`EHR_MAX_UPLOAD_BYTES`) and format validation; request validation on every endpoint; errors never include internal exception text,
  provider messages, credentials or submitted values.
- Logs: structured, secret-redacted; never the search query, API key, prompts, model output or clinical text (tested).
- Audit events hold codes, ids and counts only. The audit trail records system actions, not user actions (there are no users).
- CORS allows only the configured dev origin (`http://localhost:5173`) for GET/POST. CORS is a browser rule, not access control.
- Secrets come from environment variables / `.env` (git-ignored); the key is a `SecretStr` and is never echoed.
- Run the API bound to localhost only (the default `uvicorn` host). Do not bind it to a network interface.

## What a production deployment would need (not exhaustive)
Authenticated users (SSO/OIDC), authorization checks on every endpoint with patient-level access policies and purpose-of-use/consent handling,
per-user audit logging, TLS, encryption at rest and key management, secrets management, rate limiting and abuse protection, data retention and
deletion policy, a vetted pipeline for any third-party AI provider (business associate agreements, data minimization review), vulnerability and
dependency management, backup/DR, and a formal privacy/security/compliance assessment and clinical safety review.
