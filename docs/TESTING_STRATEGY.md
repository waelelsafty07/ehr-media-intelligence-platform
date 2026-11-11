# Testing strategy

Canonical command (repository root, virtual environment active): `pytest` (or `python -m pytest`).
Configuration: root [pytest.ini](../pytest.ini) (`testpaths = backend/tests`, `pythonpath = backend`).
Frontend: `cd frontend && npm test` (Vitest). Lint: `ruff check backend`.

## Rules the suite follows
- Synthetic data only; deterministic (no clock, network or randomness dependence in assertions).
- No network, no API keys. External providers (OpenAI, embeddings, vector store) will be faked behind ports.
- Every test is independent of order. Database tests use a per-test temporary SQLite file (`tmp_path`);
  use-case tests use in-memory fakes ([tests/fakes.py](../backend/tests/fakes.py)) with snapshot/rollback
  and fault injection.
- Failure paths are tested next to success paths.

## What exists today (301 backend tests, plus 2 frontend)

| Area | File | Tests | Covers |
|---|---|---|---|
| Foundation | `test_foundation.py` | 7 | health (ok/degraded), error envelope, no leakage of internal errors, log redaction, secrets absent from settings repr |
| Architecture | `test_architecture.py` | 5 | domain/application import direction; checker self-test; `fhir.resources` confined to `infrastructure/fhir` and R4B-only; `openai` confined to `infrastructure/llm` |
| Normalization | `unit/test_normalization.py` | 36 | date formats, ambiguous/invalid/future/implausible dates, missing values, gender map, MRN normalization, names |
| Canonical build | `unit/test_canonicalization.py` | 12 | missing DOB, missing/invalid MRN, issue severities, aliases, diagnoses not invented, content/source hashing |
| Identity policy | `unit/test_identity_policy.py` | 10 | match, conflict, ambiguous, name-only twins not merged, no-MRN |
| Strategies/factory | `unit/test_ingestion_strategies.py` | 20 | JSON/CSV/text parsing, malformed JSON, invalid CSV, ragged rows, format equivalence, factory, format detection |
| Ingest use case (fakes) | `unit/test_ingest_use_case.py` | 18 | states, duplicates, conflicts, ambiguity, idempotent reprocessing, audit events and audit privacy, rollback + FAILED + retry |
| Persistence (SQLite) | `integration/test_sqlalchemy_persistence.py` | 5 | round trip, unique MRN, rollback, foreign keys, end-to-end idempotency |
| FHIR rules | `unit/test_fhir_rules.py` | 11 | value sets, dangling/duplicate references, fullUrl/id, patient count, attachment consistency, fingerprint, deterministic ids |
| FHIR mapping | `unit/test_fhir_mapping.py` | 14 | Patient/DocumentReference/DiagnosticReport/Bundle mapping, missing fields omitted, nothing invented, determinism, build errors, library validator behavior |
| FHIR use cases (fakes) | `unit/test_generate_fhir_use_case.py` | 18 | generation, idempotency, regeneration without duplicates, only completed records, patient isolation, INVALID/FAILED paths with injected faults, FHIR vs ingestion status, audit privacy |
| FHIR persistence (SQLite) | `integration/test_fhir_persistence.py` | 9 | state/resources round trip, replace semantics, FKs, PK uniqueness, rollback, end-to-end with real adapters |
| FHIR API | `integration/test_fhir_api.py` | 11 | generate/retrieve, 404/409/422/500, idempotency, isolation, re-ingest regeneration, independent R4B parse of the served bundle |
| Summary domain | `unit/test_summary_domain.py` | 30 | context building/caps/hash/isolation, safe parsing, strict schema, 200-word limit, grounding checks (foreign ids, quotes, invented/uncertain/historical/negated diagnoses, dates, advice), deterministic parts |
| Summary use case (fakes) | `unit/test_generate_summary_use_case.py` | 32 | generation, prompt privacy, cache hit/invalidation (records, model, prompt version, config), patient isolation, retries/backoff, permanent/not-configured errors, malformed/truncated output, unsupported claims, persistence failure, audit privacy, staleness |
| OpenAI adapter (real SDK, mocked HTTP) | `integration/test_openai_adapter.py` | 17 | request shape, response parsing, HTTP/timeout/connection error classification, no credential leakage, SDK retries disabled, provider selection from settings |
| Summary persistence (SQLite) | `integration/test_summary_persistence.py` | 6 | unique key/upsert, FK, rollback, end-to-end pipeline with cache, staleness, failure recovery |
| Summary API | `integration/test_summary_api.py` | 10 | 503 without key, full summary contract, cache, staleness, 404/409/502, no leakage, isolation |
| Log privacy | `unit/test_summary_logging.py` | 2 | no API key, provider text, patient identifiers or clinical content in logs across success, cache-hit, rejected-output and provider-error paths and in the OpenAI adapter (incl. SDK DEBUG logs) |
| AI evaluation (synthetic) | `evaluation/test_summary_evaluation.py` | 18 | 14 synthetic cases with expected verdicts, aggregate detection metrics, determinism, a documented known limit |
| API | `integration/test_ingest_api.py` | 10 | sample files end to end, 422/415/413, validation envelope, no internal leaks |
| Frontend | `frontend/src/App.test.tsx` | 2 | health success / unreachable states |

## Planned (not yet written; tracked per phase)
Embeddings/indexing/search filters/patient isolation/empty
results/benchmark (Phase 5); UI flows (Phase 6).

## AI evaluation
Implemented: deterministic checks on synthetic cases (unsupported claims, dates, patient identity, attribution, length,
schema validity) in `backend/tests/evaluation/`, plus a documented human-review rubric in [AI_EVALUATION.md](AI_EVALUATION.md)
(not yet performed). API success alone is not evidence of clinical accuracy.

## Notes on FHIR tests
Mapping tests use the real `fhir.resources.R4B` adapter; use-case tests use the real mapper with failure/mutation
hooks and the in-memory fakes. I confirmed the key tests fail when the behavior breaks (temporarily disabling the
idempotency comparison and the reference check each made the relevant tests fail).

## Notes on AI tests
No test needs a network or an API key: providers are scripted fakes (`tests/summary_helpers.py::ScriptedLLM`), and the OpenAI
adapter is tested with the real SDK over `httpx.MockTransport`. Mutation checks: disabling grounding verification, the
cache lookup and the patient-isolation check each made the relevant tests fail. Live-API behavior is untested.
