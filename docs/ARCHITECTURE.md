# Architecture

**Clean Architecture inside a modular monolith.** One deployable FastAPI app. Status legend:
**[built]** exists and is tested; **[planned]** designed, not yet implemented.

## Layers and dependency direction

```
api (presentation)  ->  application  ->  domain  <-  infrastructure
                                            ^ ports (interfaces) live here
```

Dependencies point inward. `infrastructure` implements ports defined in `domain/ports`; `application`
use cases receive those implementations by constructor injection; only the composition root
(`main.py` + `api` dependency providers) knows concrete classes.

| Layer | Path | Contains | May import |
|---|---|---|---|
| Domain | `app/domain/` | Entities, canonical models, normalization/identity rules, ports | stdlib, pydantic |
| Application | `app/application/` | Use cases, ingestion strategies + factory, DTOs, pipelines | domain |
| Infrastructure | `app/infrastructure/` | SQLite repositories, OpenAI, Sentence Transformers, ChromaDB, fhir.resources adapters | domain, application, libraries |
| Presentation | `app/api/` | FastAPI routes, request/response schemas, DI providers | application, domain |
| Cross-cutting | `app/core/` | Settings, JSON logging + redaction, error envelope, request-ID middleware | stdlib, fastapi |

**[built]** `tests/test_architecture.py` scans imports and fails if `domain` imports FastAPI, SQLAlchemy,
ChromaDB, OpenAI, Sentence Transformers, fhir, httpx, or any outer layer, or if `application`
imports infrastructure/api. Pydantic is allowed in the domain (validation, not I/O).

## What exists today (Phases 1-5)

[built] app factory, settings, logging/redaction, error envelope, request IDs, `GET /health`, React shell
(Phase 1); canonical model, normalization, identity policy, JSON/CSV/text ingestion, duplicate
detection, audit log, SQLite persistence, `POST /ingest` (Phase 2); FHIR R4 mapping, validation, persistence and
endpoints (Phase 3); grounded AI summaries with an OpenAI adapter, caching and validation (Phase 4). Semantic search over FHIR documents and
summaries (Phase 5).

| Path | Contents (Phase 2) |
|---|---|
| `domain/canonical.py` | `CanonicalRecord`, `PatientInfo`, `ClinicalDocumentInfo`, `DiagnosticInfo`, `SourceMetadata`, `ValidationIssue`, `ProcessingInfo`, enums |
| `domain/normalization.py` | date / gender / MRN / name normalizers (deterministic, return `Normalized(value, outcome, reason)`) |
| `domain/canonicalization.py` | alias mapping, `build_canonical`, source/content hashing |
| `domain/identity.py` | pure identity policy -> `IdentityDecision` |
| `domain/entities.py` | `Patient`, `StoredSourceRecord`, `AuditEvent` |
| `domain/ports/` | `PatientRepository`, `SourceRecordRepository`, `AuditLogRepository`, `UnitOfWork` |
| `application/ingestion/` | `JSON/CSV/TextIngestionStrategy`, `IngestionStrategyFactory`, `resolve_format` |
| `application/use_cases/` | `IngestPatientRecordsUseCase`, `ResolvePatientIdentityUseCase` |
| `infrastructure/persistence/` | SQLAlchemy tables, repositories, `SqlAlchemyUnitOfWork` |
| `domain/fhir.py`, `domain/ports/fhir.py` | FHIR state, deterministic IDs, fingerprint, library-independent validation rules; `FhirBundleBuilder` / `FhirSchemaValidator` ports |
| `application/use_cases/` (FHIR) | `GenerateFHIRBundleUseCase`, `GetPatientFHIRBundleUseCase`, `GetPatientFHIRStatusUseCase` |
| `infrastructure/fhir/` | `R4BBundleBuilder`, `R4BSchemaValidator`: the only code importing `fhir.resources` (R4B namespace) |
| `infrastructure/persistence/` (FHIR) | `fhir_resources`, `patient_fhir_state` tables + `SqlAlchemyFhirRepository` |
| `domain/summary.py`, `domain/ports/llm.py` | Summary schemas, context building, grounding checks; `LLMProvider` port |
| `application/summary/`, `application/use_cases/` (summary) | Versioned prompt; `GenerateClinicalSummaryUseCase`, `GetPatientSummaryUseCase` |
| `infrastructure/llm/` | `OpenAIAdapter` (only code importing `openai`), `UnconfiguredLLMProvider`, `build_llm_provider` |
| `domain/search.py`, `domain/ports/search.py` | Query validation, indexed-document model, text/date extraction, snippets; `EmbeddingProvider` and `VectorStore` ports |
| `application/use_cases/` (search) | `IndexClinicalRecordsUseCase`, `SearchClinicalRecordsUseCase`, `GetSearchStatusUseCase` |
| `infrastructure/search/` | `HashingEmbeddingProvider`, `SentenceTransformerEmbeddingProvider` (optional), `SqliteVectorStore`; the only code importing `numpy` / `sentence_transformers` |
| `api/` | `search.py` (4 thin routes), `summaries.py` (2 thin routes), `fhir.py` (3 thin routes), `ingest.py` (thin route), `schemas.py`, `dependencies.py` (composition root), `domain_errors.py` |

## Ingestion design (built)

**Canonical model.** Independent of input format and FHIR. Holds only normalized values; the raw
payload is stored once on the source-record row. Trade-off: clinical text exists twice (raw + normalized);
the raw copy is required to retry/replay, the normalized copy is what later phases read.
Original values never appear in validation messages or audit details.

**Strategy + factory.** A strategy only parses structure into `ParsedRecord(index, fields, parse_error)` with
the *original keys*. Alias mapping and normalization are shared domain code, so JSON, CSV and text all
converge on the same canonical record (tested). `IngestionStrategyFactory` only selects a strategy.
Per-record problems (non-object JSON item, ragged CSV row) reject that record; whole-file problems
(malformed JSON, no CSV header, empty text, non-UTF-8) raise `IngestionParseError` -> HTTP 422.

**Data-quality classes.** `missing_required` (MRN; error), `missing_optional` (DOB, name, gender, document date; warning),
`invalid_value` (error for MRN/DOB/document date, warning for gender/name), `recovered_format` (warning),
`duplicate_record` (warning), `identity_conflict` / `ambiguous_match` (error). Ambiguous dates such as
`03/04/2020` are invalid, never guessed. Nothing missing is ever filled in.

**Processing states.** `received -> completed | rejected | duplicate | needs_review | failed`.
`received` and `failed` are retryable; the others are terminal. `completed` means normalized, identity
resolved, and persisted; later phases add separate FHIR / indexing status so "completed" never implies
downstream work was done.

**Transactions (per record).** A: store raw record as `received` + audit, commit (never lost).
B: normalize, duplicate check, identity, patient/status/audit writes in ONE transaction.
C: if B raises, B rolls back completely (no partial patient), then `failed` + failure category
(exception class name only) is recorded in a new transaction. If C also fails the row stays `received`,
which is still retryable. Re-submitting the same content retries `received`/`failed` records.

**Idempotency.** `source_hash` = sha256 of the raw record fields (unique column). Same raw content again =>
skipped, audited as `reprocess_skipped`, no new rows; history is kept. `content_hash` = sha256 of
normalized MRN/DOB/document date/text/diagnoses; same hash on a different submission => `duplicate`
linked to the original (`duplicate_of_id`). `record_id` is derived from `source_hash`, so IDs are stable.

**Identity rules** (`domain/identity.py`): MRN known + DOBs differ -> conflict; MRN known + name or gender
differs -> ambiguous; MRN known otherwise -> matched; new MRN + same name AND DOB as another patient ->
ambiguous; otherwise new patient. Names are compared exactly after normalization (case, accents,
punctuation, token order); there is no fuzzy matching and name similarity alone never merges or blocks.
Conflict/ambiguous records are `needs_review`: kept, not linked, rationale code in the audit log.

**Audit.** Append-only `audit_events`: received, retry, normalized (warning codes), validation_failed,
duplicate_detected, identity_decision (outcome + rationale code + related patient IDs), patient_created,
review_required, record_completed, processing_failed, reprocess_skipped, ingest_rejected (file level).
Details hold codes, IDs and counts only: a test asserts no names, dates, MRNs or note text appear.

**Tables (SQLite).** `patients` (unique `mrn`; index on `name_key, date_of_birth`), `source_records`
(unique `source_hash`; indexed `content_hash`, `status`; FKs to `patients` and self for duplicates; JSON
`raw_payload` and `canonical`), `audit_events` (FKs; indexed by record, type, time). Foreign keys are
enforced (PRAGMA). Tables are created with `create_all`; no migration tool yet.

## AI summary pipeline (built, Phase 4)

Grounded RAG, as decided in D12. Modules: `domain/summary.py` (schemas, context building, parsing, grounding checks,
deterministic parts), `domain/ports/llm.py` (`LLMProvider`), `application/summary/prompt.py` (versioned prompt),
`application/use_cases/generate_clinical_summary.py` + `get_patient_summary.py`, `infrastructure/llm/` (`OpenAIAdapter`,
`UnconfiguredLLMProvider`, `build_llm_provider`), table `clinical_summaries`, routes in `api/summaries.py`.

```
patient's completed records (SQLite) -> context (isolation re-checked, newest first, record/char caps)
 -> cache lookup (patient, hash of the exact context, prompt version, model)  -> hit: return stored summary
 -> LLMProvider.generate (no DB transaction held; retries/backoff) -> safe parse + strict schema
 -> grounding checks (cited ids in context, verbatim quotes, diagnosis-in-quote, hedging/history/negation,
    dates+numbers, advice, <=200 words) -> assemble with deterministic parts -> persist + audit
```
- **Retrieval** is deterministic and patient-scoped (SQLite), not semantic: Phase 4 predates the vector index. Semantic
  ranking within the same patient scope can be added in Phase 5 behind the same context builder.
- **What the model sees:** only clinical text and stated diagnoses of that patient. Never name, MRN, DOB or the patient ID.
- **Deterministic, not model-written:** `recent_records`, `missing_information`, `source_record_ids`, the quality
  indicator (an evidence-coverage heuristic, not a confidence), disclaimer, metadata.
- **States:** `clinical_summaries.status` is `generated` or `failed` per cache key. Failed rows are never cache hits;
  calling POST again retries and replaces the row. `GET` reports staleness.
- **Reliability:** provider errors are categorized (transient / timeout / permanent / not configured); transient errors
  and invalid model output share one attempt budget (default 3) with exponential backoff for transient errors;
  permanent errors stop at once; SDK error text is never propagated (it can echo credentials). The app starts without
  an API key; generation then returns 503.
- Evaluation and human-review rubric: [AI_EVALUATION.md](AI_EVALUATION.md). Endpoint contracts: [API_CONTRACTS.md](API_CONTRACTS.md).

## Semantic search (built, Phase 5)

Details of the decision: D32-D38 in [DECISIONS.md](DECISIONS.md). Schema: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).

```
index:  fhir_resources(DocumentReference text) + latest generated NON-STALE summary
         -> desired entries per patient -> embed only changed (content hash) -> upsert -> delete no-longer-desired
search: query -> embed -> SQL filters (patient/type/date/model) BEFORE ranking -> exact cosine (numpy) over the filtered rows
         -> re-validate every hit against SQLite -> rank (score, date, id) -> top <=5 with snippets + patient display
```
- **Vector store:** a table in the existing SQLite database (float32 BLOBs), exact cosine, behind the `VectorStore` port. Chosen over
  ChromaDB/FAISS for fit and simplicity (D32); a Chroma adapter would be a small addition behind the same port.
- **Embeddings:** `EmbeddingProvider` port with two implementations. **Default: `sentence-transformers`** - the real local semantic model
  `all-MiniLM-L6-v2` (needs the `embeddings` extra and a one-time model download; loaded lazily). `hashing`: deterministic lexical embedder
  (shared words only) - an **explicit fallback** and the test double. If the semantic model is unavailable the endpoints return a clear 503; the
  application never silently falls back or mislabels lexical results as semantic. Every response reports the actual `embedding.kind` (D33, D40).
- **Index = desired-state reconcile per patient:** one rule handles updates (hash changes), deletions (FHIR regenerated, resource gone, FHIR
  invalid/failed) and invalidation (summary stale/failed/missing). Idempotent; retry after any failure is safe; upsert happens before delete so a
  failure never leaves a patient with fewer entries (D34). The index is derived: wiping it and re-indexing reproduces identical results (tested).
- **Patient scoping in search (data separation, NOT authorization):** (1) the patient filter is a SQL `WHERE` so a scoped query never loads other patients' vectors (tested with a spy);
  (2) the use case drops any hit whose patient differs from the filter even if a store misbehaves (tested with a deliberately leaky store);
  (3) each hit is re-validated against SQLite (resource exists, belongs to the hit's patient, text unchanged, summary is the current
  non-stale one), so patient identity comes from stored records, never from similarity or index metadata (D35). These are correctness controls:
  the system has no authentication or authorization, so any caller may omit `patient_id` and search every patient (see [SECURITY.md](SECURITY.md)).
- **No direct identifiers in search results:** results carry an opaque `patient_id` but never name, MRN or date of birth; the use case loads the patient
  only to confirm it exists (D39). The `snippet` is stored clinical text and contains whatever that text contains.
- **No identifiers to the embedder:** only document text / summary text is embedded, never name, MRN or DOB (tested). Both providers run locally.
- **Score semantics:** cosine similarity; non-positive similarity is never returned; not clinical relevance or probability (D36).
- **Logging:** query text is never logged (it may contain identifiers); counts and latency only.

**Measured (synthetic, `backend/scripts/benchmark_search.py`; macOS arm64, 10 CPUs, Python 3.12.14; one run each, not a statistical study):**

Re-run after the default changed to semantic and names/MRNs were removed from results (second run; the first, before these changes, gave 7.6 ms p50 for the real
model - run-to-run variation is about 0.5 ms).

| | **semantic (default)** all-MiniLM-L6-v2 | lexical fallback (`hashing`) |
|---|---|---|
| Cold start: first embedding call in a new process | **3.7 s** with the model on disk; ~50 s the very first time in a fresh environment (26 s torch import + 24.6 s load incl. download) | 0 s |
| Index 50 records (reindex); second run | 0.38 s; 0 re-embedded | 0.07 s; 0 re-embedded |
| **Warm search, 50 records, over HTTP**: p50 / p95 / max (60 requests) | **8.0 / 8.8 / 9.9 ms** | 3.1 / 4.0 / 4.4 ms |
| Patient-scoped search p50 | 4.7 ms | 1.6 ms |
| Topic relevance, 20 queries worded unlike the notes: top-1 correct / precision@5 | **1.00 / 0.89** | 0.75 / 0.45 |

The target (< 2 s for 50 records) is met by a wide margin for warm searches in either mode; the only slow step is the one-time model
load, reported separately. `backend/tests/integration/test_search_performance.py` guards the budget (< 2 s, 50 records, hashing). Exact-scan
growth, store-only with random 384-d vectors: 50 -> 0.3 ms, 1,000 -> 3.5 ms, 5,000 -> 27.8 ms, 20,000 -> 130 ms (linear). I have not measured
beyond 20,000 entries; patient-scoped queries scan only that patient's rows. The relevance set is small, synthetic and author-written: it shows the
lexical-vs-semantic gap, not clinical retrieval quality.

## FHIR design (built, Phase 3)

Details and evidence: [FHIR_MAPPING.md](FHIR_MAPPING.md). Summary: adapter pattern around `fhir.resources.R4B`
(R4B 4.3.0; the package's default namespace is R5). The mapper (deterministic) and bundle assembler live behind
the `FhirBundleBuilder` port; the library validator behind `FhirSchemaValidator`; value-set, reference-integrity
and attachment rules are pure domain code. Generation flow (one transaction): load patient + COMPLETED records ->
fingerprint -> skip if unchanged -> map -> assemble bundle -> domain rules + library validation -> replace
resources and save state `generated`; validation issues save `invalid` and persist no resources; an exception rolls
back and records `failed` in a new transaction. FHIR state is independent of ingestion status. Generation is an
explicit step (`POST /patients/{id}/fhir`), not triggered by ingestion.

## Known limitations (Phase 2)
- No review-resolution workflow or endpoint: `needs_review` records are held, not resolved.
- Edited content for the same source is a *new* record; there is no supersede/update logic.
- Existing patient demographics are never updated or back-filled from later records.
- Identical raw records repeated inside one file are treated as already-ingested.
- No `GET` endpoints for patients, records or the audit log yet (inspect via the database); FHIR endpoints exist.
- Schema creation only (`create_all`); schema changes need a migration tool later.
- Upload size is limited by setting; there is no authentication.

## Patterns (planned unless noted)

| Pattern | Problem it solves | Where | Phase |
|---|---|---|---|
| Repository | Keep SQL out of use cases; swap in fakes for tests | ports in `domain/ports`, impls in `infrastructure/persistence` | 2-4 |
| Strategy | Three input formats, one canonical output | `application/ingestion/` (JSON, CSV, Text) | 2 |
| Factory | Select the strategy from input type | `IngestionStrategyFactory` (only real factory planned) | 2 |
| Adapter | Isolate OpenAI, Sentence Transformers, ChromaDB, fhir.resources | `infrastructure/{llm,embeddings,vectorstore,fhir}` | 3-5 |
| Dependency Injection | Explicit, replaceable dependencies | constructor injection in use cases; FastAPI `Depends` at the boundary (**[built]** for settings/engine via `app.state`) | all |
| Use case | One workflow per class, thin routes | `application/use_cases/` | 2-5 |

Deliberately **not** used: a Strategy per trivial conditional (date/gender normalization start as
plain functions and become strategies only if source-specific rules appear), factories wrapping a
single constructor, global singletons.

## Data flow (target)

Ingest: file -> `IngestionStrategyFactory` -> strategy -> canonical record -> normalization ->
duplicate/identity checks (deterministic) -> persist source + normalized + audit (SQLite) ->
FHIR mapping + validation -> persist FHIR -> index (embed -> ChromaDB) -> mark complete.
Failures at any step keep the SQLite record, store the failure status, and are retryable.

## AI pipeline: Grounded RAG clinical intelligence (planned, Phase 4)

Records -> selection/preprocessing -> patient-scoped retrieval -> scope/metadata validation ->
context assembly -> `LLMProvider.generate` -> structured output validation (Pydantic, <=200 words) ->
source-attribution check (every cited ID must be in the supplied context) -> persist -> cache
(patient ID + content hash + prompt version + model). Orchestrated by `GenerateClinicalSummaryUseCase`,
never by a route. The model sees only retrieved records of one patient; if context is missing the
summary says so. The quality indicator is an uncalibrated heuristic, not a clinical probability.
Evaluation: deterministic checks on synthetic cases (unsupported claims, dates, patient identity,
attribution, length, schema) plus a documented human-review rubric. RAG reduces, not eliminates, hallucination.

## Search architecture (planned, Phase 5)

Query -> validation -> `EmbeddingProvider` -> patient/metadata filter -> `VectorStore` query ->
optional keyword/metadata rerank -> format -> response. Patient scope is applied inside the vector
query filter, not after retrieval. Scores are similarity, not clinical relevance.

## Persistence

SQLite is the source of truth (patients, source records, normalized records, FHIR resources, audit,
summaries, embedding metadata, status). ChromaDB is a derived index rebuildable from SQLite.

## Deterministic by design

Date/gender/MRN normalization, duplicate detection, identity resolution, FHIR mapping/validation,
audit: no LLM. LLM is used only for grounded summarization. An LLM never decides patient identity
or fills FHIR data.

## Error handling

One envelope `{"error": {code, message, request_id, details?}}` **[built]**; internal text never
returned. Planned distinct error types: validation, domain, not-found, identity conflict, provider
failure, persistence, indexing. Retries only for transient provider errors.

## Frontend

Vite dev server proxies `/api/*` to the backend. **[built, Phase 6]** clinician search UI (React + TypeScript + Tailwind):
- `src/api/client.ts` is the only module that calls `fetch`; it maps HTTP errors to safe messages and supports `AbortSignal`. `src/api/types.ts` keeps **search results and
  patient display info as separate models**.
- `src/hooks/usePatientDisplay.ts` is a session-scoped cache of in-flight/resolved display lookups (one request per patient; failures are not cached).
- `src/components/`: `SearchForm` (query + patient/type/date filters, client-side validation), `ResultCard`, `DetailDrawer` (modal dialog: focus moved in and restored, Escape closes, Tab trapped).
- `App.tsx` owns search state; a new search aborts the previous request so a slow stale response cannot overwrite a newer one.
- Backend additions: `GET /patients/{id}/fhir/resources/{type}/{rid}` (`GetPatientFHIRResourceUseCase`: patient exists, FHIR state `generated`, resource owned by the patient, type limited to DocumentReference/DiagnosticReport; the UI never downloads the Bundle) and `GET /patients/{id}/display` (route `api/patients.py` -> `GetPatientDisplayUseCase` -> existing patient repository).

## Known limitations (Phase 3)
- FHIR validation is not a complete validator: no FHIRPath invariants or terminology/value-set service; our own
  value-set checks cover only the code fields we populate.
- No coded content: DocumentReference has no `type`/`category`, DiagnosticReport is uncoded with status `unknown`
  (the source provides none of these). Clinical coding would need a terminology source and a human/clinical decision.
- Patient names are free text (`name.text`), not split into given/family.
- Bundles use `urn:uuid` full URLs (no FHIR server base URL); resources are not served individually
  (`FhirRepository.get_resource` exists for Phase 5).
- Generation is per patient and on demand; there is no bulk or automatic regeneration.
- Stale-but-previously-valid resources are retained (not served) after an `invalid`/`failed` regeneration.

## Known limitations (Phase 4)
- **Not exercised against the live OpenAI API.** The adapter is verified against the real SDK over a mocked HTTP
  transport; real model output quality, JSON-mode behavior and rejection rate are unknown.
- Automated checks do not prove clinical correctness: evidence quotes establish textual traceability only, a correctly cited quote can
  still support a distorted paraphrase, and omissions are undetected. Summaries are AI-generated, need qualified clinical review, and
  must not be presented as verified diagnoses or medical advice.
- `summary_text` prose is only partially verified (dates, numbers, advice phrases); structured fields are stronger.
- Marker lists (hedging/history/negation/advice) are English heuristics and can miss or over-reject.
- Retrieval is deterministic recency selection with caps, not semantic; very long histories are truncated (disclosed in
  `missing_information`).
- Concurrent identical requests may both call the provider (no in-flight de-duplication); the cache key prevents
  duplicate rows.
- Sending clinical text to a third-party provider requires appropriate agreements for real data; this project uses
  synthetic data only and makes no compliance claim.
- Records held for identity review (`needs_review`) are not linked to a patient and therefore not summarized.

## Known limitations (Phase 5)
- **Semantic mode has setup requirements.** The default needs the `embeddings` extra (PyTorch, ~1 GB) and a one-time model download. Without them search
  returns 503 by design (no silent fallback). The explicit lexical fallback matches shared (lightly stemmed) words only ("dyspnea" will not find "shortness of
  breath"). Tradeoff accepted (D40): a heavier, slower-to-set-up default in exchange for search that is genuinely semantic out of the box.
- Indexing is an explicit step (`POST /patients/{id}/index` / `POST /search/reindex`), not automatic after ingestion/FHIR/summary changes;
  until re-indexed, new documents are not searchable. Stale entries are never *shown* (validated at query time) but can occupy candidate slots.
- **No authentication or authorization (demo system, synthetic data only).** Any caller can omit `patient_id` and search all patients; patient filtering is data
  scoping, not access control. Search results omit names/MRNs, but other endpoints do not: `GET /patients/{id}/fhir` returns the full Patient resource.
  A production deployment would need authenticated users, authorization checks and patient-level access policies. See [SECURITY.md](SECURITY.md).
- Exact O(n) scan: measured to 20,000 entries (130 ms store-only); beyond that use an ANN-backed adapter. No caching of vectors between queries.
- all-MiniLM-L6-v2 truncates inputs beyond ~256 word pieces; there is no chunking, so the tail of a long note is not searchable.
- Only DocumentReference text and AI summaries are indexed (not DiagnosticReport, canonical diagnoses or other resources); no keyword/metadata
  re-ranking; snippets are a fixed-length excerpt, not highlighted passages.
- Records without a date are excluded when a date bound is set. A summary's date is its most recent source-record date.
- Scores are similarity, not clinical relevance; the quality evaluation is small and synthetic. The real model is exercised in the benchmark and
  by one opt-in test (`EHR_RUN_REAL_EMBEDDING_TESTS=1`); the default test run uses the lexical embedder or a faked model and is fully offline.
