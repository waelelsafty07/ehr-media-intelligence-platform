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

## What exists today (503 backend tests + 1 opt-in skipped, plus 24 frontend)

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
| Search domain | `unit/test_search_domain.py` | 39 | query validation (blank/long/limit/types/dates/extra fields), extraction from stored resources, content-hash sensitivity, snippets |
| Embedding configuration | `unit/test_embedding_config.py` | 17 | semantic default, explicit lexical choice, env selection, unknown names rejected, lazy load, CPU/normalization, availability check, clear errors (no provider details), no silent lexical vectors, startup warning |
| Semantic default + privacy | `integration/test_semantic_default_and_privacy.py` | 11 | default mode end to end with a faked model, missing model -> 503 with nothing indexed, load failure, explicit lexical fallback, mixed-model safety, no names/MRNs/DOBs in search/status/index responses and the OpenAPI schema, demo/no-auth notice, preserved isolation/top-5/filters |
| Hashing embedder | `unit/test_hashing_embedder.py` | 10 | determinism, unit norm, related vs unrelated, documented lexical limit, empty text |
| Indexing use case (fakes) | `unit/test_index_use_case.py` | 19 | metadata, idempotency, only-changed embedding, update/deletion/invalidation, FHIR invalid/failed, stale/failed summaries, model change, no identifiers to the embedder, failures + retry, delete-failure safety, per-patient isolation |
| Search use case (fakes) | `unit/test_search_use_case.py` | 23 | ranking, limit<=5, patient filter before ranking, leaky-store isolation, type/date filters, empty/no-match, min_score, provider/store failures, stale/changed/wrong-patient hits dropped, determinism, snippets, query not logged |
| SQLite vector store | `integration/test_sqlite_vector_store.py` | 13 | exact cosine, SQL filters, patient-scoped scan spy, SQL-injection safety, model/dimension isolation, FK + atomic batch, persistence, error mapping, wipe-and-rebuild |
| Search API | `integration/test_search_api.py` | 29 | result shape, linked FHIR resource exists, idempotent index/reindex/status, isolation, filters, summary staleness, 422/404/503 envelopes, limits, optional-extra-missing 503, query not logged |
| Search latency | `integration/test_search_performance.py` | 1 | 50 records: warm searches < 2 s (regression guard) |
| Real model (opt-in) | `optional/test_real_sentence_transformers.py` | 1 (skipped) | real all-MiniLM-L6-v2 is semantic, normalized, deterministic. Run: `pip install -e "./backend[embeddings]"` then `EHR_RUN_REAL_EMBEDDING_TESTS=1 pytest backend/tests/optional -v` |
| Log privacy | `unit/test_summary_logging.py` | 2 | no API key, provider text, patient identifiers or clinical content in logs across success, cache-hit, rejected-output and provider-error paths and in the OpenAI adapter (incl. SDK DEBUG logs) |
| AI evaluation (synthetic) | `evaluation/test_summary_evaluation.py` | 18 | 14 synthetic cases with expected verdicts, aggregate detection metrics, determinism, a documented known limit |
| API | `integration/test_ingest_api.py` | 10 | sample files end to end, 422/415/413, validation envelope, no internal leaks |
| Frontend | `frontend/src/App.test.tsx` | 2 | health success / unreachable states |

### Phase 6 additions
- Backend `tests/integration/test_patient_display_api.py` (6): success, exactly the three approved fields, safe 404, id-length validation, `/search` still free of names/MRNs, OpenAPI schema minimal.
- Frontend (Vitest + Testing Library + user-event, `fetch` fully mocked, no network): `src/App.test.tsx` (18) covers submission, ranked cards, display lookup (and de-duplication to one call per patient),
  lookup-failure fallback, patient/type/date filters, client-side validation, empty/loading/error+retry states, lexical-mode indicator, drawer open/close/Escape/focus restore,
  AI summary labelling + disclaimer, FHIR-unavailable / missing-resource / missing-summary handling. `src/api/client.test.ts` (5) covers body shaping, safe errors, abort propagation, and that only the requested DocumentReference is extracted.
- Backend `tests/integration/test_fhir_resource_api.py` (7): exact resource returned and equal to the stored one, no demographics, Patient type refused, another patient's resource = same 404 as missing, wrong/unknown type, unknown patient / FHIR not generated, id length.
- **Real-browser smoke test (manual, not part of `pytest`/`npm test`, not committed):** a throwaway Playwright script driving the locally installed Chrome (`playwright-core` installed outside the repo; no
  new project dependency) against a real backend (lexical embedder, local OpenAI HTTP stub for the summary, synthetic `records_for_summary.json`) and the Vite dev server. 23 checks passed:
  ingest->FHIR->summary->index->search, 3 result cards, name/MRN lookup (one call), document and AI-summary drawers, focused FHIR links open a single resource, Escape/focus restore,
  type/date/patient filters, no horizontal overflow at 768 px and 375 px, no unexpected console errors. A second throwaway run (9 checks) confirmed a live OpenAI summary renders in the UI: card badge, drawer banner, quotes, source ids, model id and disclaimer, with no `POST /summary` from the UI. It is a one-off check on one machine (macOS, Chrome), not a CI test.
- Not covered: no automated visual/regression or accessibility (axe) scan; other browsers untested; contrast not measured; keyboard behaviour verified only for Escape/focus restore.

### OpenAI error classification (`tests/integration/test_openai_error_classification.py`, 27 tests)
Real `openai` SDK over a mocked HTTP transport, driven through the real use case (no network, no live API). Covers 429 `insufficient_quota` (code or type) = permanent, 429 rate limit / unknown 429 = transient,
401/403/404/400 = permanent, 500/503/connection/timeout/malformed 200 body = transient. Asserts permanent failures make exactly 1 provider attempt with no backoff sleep; transient failures make exactly `max_attempts`
with backoff `[1, 2]`; a transient-then-success path still succeeds; no provider text, key or request id appears in exceptions, logs or the API response. Mutation-checked (disabling the quota check fails 6 tests).

## Not covered
No CI pipeline; no automated live OpenAI test (one manual live request was verified; see README "Live OpenAI verification"); no automated browser/visual/accessibility tests; no load testing beyond the Phase 5 benchmark script.

## AI evaluation
Implemented: deterministic checks on synthetic cases (unsupported claims, dates, patient identity, attribution, length,
schema validity) in `backend/tests/evaluation/`, plus a documented human-review rubric in [AI_EVALUATION.md](AI_EVALUATION.md)
(not yet performed). API success alone is not evidence of clinical accuracy.

## Notes on FHIR tests
Mapping tests use the real `fhir.resources.R4B` adapter; use-case tests use the real mapper with failure/mutation
hooks and the in-memory fakes. I confirmed the key tests fail when the behavior breaks (temporarily disabling the
idempotency comparison and the reference check each made the relevant tests fail).

## Notes on search tests
No test needs a network, API key or model download. The application default is the real semantic model, so the shared `client` fixture selects the deterministic hashing embedder
explicitly and an autouse fixture in `conftest.py` blocks `sentence_transformers` entirely (unless `EHR_RUN_REAL_EMBEDDING_TESTS=1`), making an accidental model load/download impossible;
semantic mode is tested with a faked `sentence_transformers` module. The real model has one opt-in test and the benchmark script. Mutation checks: disabling the document-staleness check, summary-staleness check, stale-entry deletion, idempotency
(re-embed always) and the use case's own patient check each made tests fail; for the review adjustments, reverting the semantic default, adding a silent lexical fallback and re-adding an MRN to results each failed tests. The last one initially did **not**: no test covered a misbehaving store, so a
leaky-store test was added. `backend/scripts/benchmark_search.py` is the repeatable benchmark (not collected by pytest).

## Notes on AI tests
No test needs a network or an API key: providers are scripted fakes (`tests/summary_helpers.py::ScriptedLLM`), and the OpenAI
adapter is tested with the real SDK over `httpx.MockTransport`. Mutation checks: disabling grounding verification, the
cache lookup and the patient-isolation check each made the relevant tests fail. The automated suite never calls the live API; one manual, billable live request was verified separately (README).
