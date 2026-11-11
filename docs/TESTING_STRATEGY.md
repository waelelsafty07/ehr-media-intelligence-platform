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

## What exists today (185 backend tests, plus 2 frontend)

| Area | File | Tests | Covers |
|---|---|---|---|
| Foundation | `test_foundation.py` | 7 | health (ok/degraded), error envelope, no leakage of internal errors, log redaction, secrets absent from settings repr |
| Architecture | `test_architecture.py` | 4 | domain/application import direction; checker self-test; `fhir.resources` confined to `infrastructure/fhir` and R4B-only |
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
| API | `integration/test_ingest_api.py` | 10 | sample files end to end, 422/415/413, validation envelope, no internal leaks |
| Frontend | `frontend/src/App.test.tsx` | 2 | health success / unreachable states |

## Planned (not yet written; tracked per phase)
AI with a mocked provider: schema,
attribution, cache, failures, length (Phase 4); embeddings/indexing/search filters/patient isolation/empty
results/benchmark (Phase 5); UI flows (Phase 6).

## AI evaluation (design, Phase 4)
Deterministic checks on synthetic cases (unsupported claims, dates, patient identity, attribution, length,
schema validity) plus a documented human-review rubric. API success alone is not evidence of clinical accuracy.

## Notes on FHIR tests
Mapping tests use the real `fhir.resources.R4B` adapter; use-case tests use the real mapper with failure/mutation
hooks and the in-memory fakes. I confirmed the key tests fail when the behavior breaks (temporarily disabling the
idempotency comparison and the reference check each made the relevant tests fail).
