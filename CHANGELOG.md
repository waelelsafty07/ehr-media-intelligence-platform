# Changelog

## [Unreleased]

### Fixed
- OpenAI billing/quota exhaustion (HTTP 429 `insufficient_quota`) is now a permanent error (`provider_quota_exhausted`, one attempt) instead of being retried as a transient outage; genuine rate limits, 5xx and network errors keep the bounded retry. Verified with mocked transport only.

### Added
- Clinician search UI (React): search with patient/resource-type/date filters, ranked result cards (name, MRN, type, date, similarity, snippet), search-mode indicator, detail drawer with
  document text or AI summary, FHIR references, AI-generated labelling and disclaimer; loading/empty/error states with retry; keyboard/focus handling.
- `GET /patients/{patient_id}/fhir/resources/{resource_type}/{resource_id}`: one DocumentReference or DiagnosticReport (ownership-checked; Patient refused; safe 404s). The UI uses it instead of downloading the Bundle.
- `GET /patients/{patient_id}/display`: returns only `patient_id`, `patient_name`, `mrn`. `/search` responses are unchanged and still contain no names or MRNs.
- Backend foundation: FastAPI app factory, environment-based settings, structured JSON logging
  with secret redaction, request-ID middleware, consistent error envelope, `GET /health`,
  SQLite engine/session setup.
- Frontend shell (React, Vite, TypeScript, Tailwind) that calls the backend health endpoint (replaced by the clinician search UI below).
- Synthetic sample records in `data/samples/`.
- Architecture and decision documentation.
- Clean Architecture layer packages (domain, application, infrastructure) and an import-direction test.
- Ingestion (JSON, CSV, plain text) via `POST /ingest` with a canonical record model, deterministic
  date/gender/MRN/name normalization, validation issues, duplicate detection, conservative patient identity
  resolution (conflicts and ambiguous matches held for review), idempotent reprocessing, explicit processing
  states with recoverable failures, and an append-only audit log (SQLite).
- Unit-of-work and repository ports with SQLAlchemy implementations; ingestion strategies and factory.
- Delivery tooling: repository-root `pytest.ini` so `pytest` works from the root; `backend/pyproject.toml`
  with explicit packaging (`app*`), runtime vs dev dependencies; `docs/TESTING_STRATEGY.md`; README rewritten
  to match the implementation; frontend dev-proxy target configurable via `API_PROXY_TARGET`; Node engine
  requirement declared in `frontend/package.json`.
- FHIR R4 (via `fhir.resources.R4B`): deterministic mapping of Patient, DocumentReference and DiagnosticReport;
  per-patient collection Bundle with resolvable `urn:uuid` references; three-layer validation (library, value
  sets, reference integrity); persisted resources and a FHIR processing state separate from ingestion status;
  idempotent regeneration by fingerprint; endpoints `POST /patients/{id}/fhir`, `GET /patients/{id}/fhir`,
  `GET /patients/{id}/fhir/status`; sample `records_with_diagnoses.json`; `docs/FHIR_MAPPING.md`.
- AI clinical summaries: `POST/GET /patients/{id}/summary`. Grounded RAG pipeline (patient-scoped context, no
  identifiers sent to the model), `LLMProvider` port with `OpenAIAdapter`, strict structured output (evidence quotes +
  source record IDs, <=200 words), deterministic grounding checks, deterministic recent records / missing information /
  quality indicator, cache keyed by patient + context hash + prompt version + model with staleness reporting,
  retries/backoff, recorded retryable failures, audit events; `docs/API_CONTRACTS.md`, `docs/AI_EVALUATION.md`.
  Verified with scripted fakes and the real OpenAI SDK over a mocked transport, and once against the live OpenAI API (one request, `gpt-4o-mini-2024-07-18`: schema, 200-word limit, grounding, disclaimer, persistence and cache hit passed; the summary was then indexed and displayed in the UI). One request does not establish model reliability or clinical correctness.
- Semantic search (default: real all-MiniLM-L6-v2 embeddings; see the review adjustments below): `POST /search` (top <= 5, patient/resource-type/date filters applied before ranking, snippets, FHIR links, similarity scores),
  `POST /patients/{id}/index`, `POST /search/reindex`, `GET /search/status`. SQLite-backed vector store (exact cosine, numpy) behind a `VectorStore`
  port; `EmbeddingProvider` port with a deterministic lexical `hashing` default and an optional `sentence-transformers` (`all-MiniLM-L6-v2`) provider;
  idempotent per-patient reconcile indexing (updates, deletions, stale-summary invalidation); search re-validates hits against SQLite for strict
  patient isolation; benchmark script; `docs/DATABASE_SCHEMA.md`.
- Search review adjustments: semantic (`sentence-transformers`, `all-MiniLM-L6-v2`) is now the default embedding mode, with `backend/scripts/init_embedding_model.py`, an `embeddings` extra in the
  documented setup, a clear 503 when the model is unavailable (no silent fallback), `embedding.available` in `/search/status`, and `EHR_EMBEDDING_PROVIDER=hashing` as an explicit lexical fallback;
  search results no longer contain patient names or MRNs; `docs/SECURITY.md`, a demo/no-authentication banner in the README and OpenAPI description; tests use an offline guard.
