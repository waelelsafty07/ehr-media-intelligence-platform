# Changelog

## [Unreleased]

### Added
- Backend foundation: FastAPI app factory, environment-based settings, structured JSON logging
  with secret redaction, request-ID middleware, consistent error envelope, `GET /health`,
  SQLite engine/session setup.
- Frontend shell (React, Vite, TypeScript, Tailwind) that calls the backend health endpoint.
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
  Verified with scripted fakes and the real OpenAI SDK over a mocked transport; not exercised against the live API.
