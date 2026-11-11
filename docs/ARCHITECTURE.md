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

## What exists today (Phases 1-2)

[built] app factory, settings, logging/redaction, error envelope, request IDs, `GET /health`, React shell
(Phase 1); canonical model, normalization, identity policy, JSON/CSV/text ingestion, duplicate
detection, audit log, SQLite persistence, `POST /ingest` (Phase 2). FHIR, AI, embeddings and search are **[planned]**.

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
| `api/` | `ingest.py` (thin route), `schemas.py`, `dependencies.py` (composition root), `domain_errors.py` |

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

## Known limitations (Phase 2)
- No review-resolution workflow or endpoint: `needs_review` records are held, not resolved.
- Edited content for the same source is a *new* record; there is no supersede/update logic.
- Existing patient demographics are never updated or back-filled from later records.
- Identical raw records repeated inside one file are treated as already-ingested.
- No `GET` endpoints for records/audit yet (inspect via the database).
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

Vite dev server proxies `/api/*` to the backend. **[built]** shell only; UI is Phase 6.
