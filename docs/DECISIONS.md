# Decisions

## D1. Python 3.12 for the backend (2026-11-11)
The machine default is Python 3.14. Dependency resolution for the full stack (fhir-resources,
openai, chromadb, sentence-transformers/torch) was verified with `uv pip install --dry-run` on 3.12.
The assignment requires 3.11+, so 3.12 qualifies. 3.14 was not used because native ML wheels
(torch, onnxruntime via chromadb) are the likeliest to lag behind a brand-new interpreter.
`pyproject.toml` keeps `requires-python >=3.11`.

## D2. uv + pyproject.toml for dependencies
Fast, reproducible virtualenvs, standard `pyproject.toml`. Later-phase dependencies are declared as
optional extras (`fhir`, `ai`, `search`) so they are installed only when used.

## D3. App factory + `pydantic-settings` with `EHR_` prefix
Enables dependency injection of settings in tests; API key is a `SecretStr` so it never appears in `repr`.

## D4. Frontend: Vite + React + TypeScript + Tailwind v4 + Vitest
Lightest React toolchain that matches the required stack; TypeScript added for API contract safety.

## Open item for Phase 3
`fhir-resources` 8.x exposes R5 at the top level; R4 resources live under `fhir.resources.R4B`
(FHIR 4.3.0, R4-compatible). Must be verified and documented when FHIR mapping starts.

## D5. Avoid a package named `models/` (2026-11-11)
The standard `.gitignore` ignores `models/` (ML artifacts), which would also match
`backend/app/**/models/` and silently exclude source. Canonical models will live in
`app/domain/` under a different name (e.g. `domain/canonical.py`). Status: applied.

---
# Architecture decisions (directive of 2026-11-11)
Status for each: **Accepted**. "Implemented" notes say what the code actually reflects today.

## D6. Modular monolith instead of microservices
- **Context:** Assessment scope, one team, local run. **Decision:** single FastAPI deployable with
  internal module boundaries. **Alternatives:** microservices; plain unstructured monolith.
- **Reasoning:** boundaries without network, deployment or consistency overhead. **Tradeoffs:** no
  independent scaling. **Consequences:** boundaries are enforced by convention + tests.
- **Implemented:** yes (single app, layered packages).

## D7. Clean Architecture boundaries (domain / application / infrastructure / presentation)
- **Context:** safety-sensitive logic must be testable without SQLite, OpenAI or Chroma.
  **Decision:** four layers with inward dependencies; ports in domain. **Alternatives:** flat feature
  modules; classic MVC with services calling the ORM. **Reasoning:** fakes make identity, FHIR and RAG
  logic unit-testable. **Tradeoffs:** more files/indirection; pydantic allowed in domain as a pragmatic exception.
  **Consequences:** every external call goes through a port.
- **Implemented:** layer packages + import-direction test. No business code yet.

## D8. Repository pattern for persistence
- **Context:** use cases must not contain SQL. **Decision:** repository interfaces
  (Patient, SourceRecord, FHIRResource, ClinicalSummary, AuditLog) in domain ports; SQLAlchemy impls in
  infrastructure; in-memory fakes in tests. **Alternatives:** use the session directly in use cases;
  active record. **Reasoning:** isolation and fast tests. **Tradeoffs:** boilerplate; transaction
  boundaries need explicit design (unit of work per request). **Consequences:** schema changes stay in infrastructure.
- **Implemented:** no (Phase 2+).

## D9. Strategy pattern for ingestion (+ one factory)
- **Context:** JSON, CSV, text must yield one canonical form. **Decision:** `JSONIngestionStrategy`,
  `CSVIngestionStrategy`, `TextIngestionStrategy` behind one interface; `IngestionStrategyFactory`
  chooses by input type. **Alternatives:** if/elif parser function. **Reasoning:** new formats added
  without touching the workflow; each parser tested alone. **Tradeoffs:** more classes than a
  conditional. **Consequences:** normalizers stay plain functions until real source-specific variation appears.
- **Implemented:** no (Phase 2).

## D10. Adapter pattern for external providers
- **Context:** OpenAI, Sentence Transformers, ChromaDB, fhir.resources have their own types and
  failure modes. **Decision:** ports `LLMProvider`, `EmbeddingProvider`, `VectorStore`, FHIR builder/validator;
  adapters in infrastructure translate and map errors. **Alternatives:** call SDKs from use cases.
  **Reasoning:** replaceable (e.g. Claude later), mockable, no live API in tests. **Tradeoffs:** lowest-
  common-denominator interfaces. **Consequences:** only OpenAI/ST/Chroma adapters now; no speculative providers.
- **Implemented:** no (Phases 3-5).

## D11. Dependency injection for testability
- **Context:** hidden globals make tests brittle. **Decision:** constructor injection into use cases;
  FastAPI `Depends` providers build them at the boundary; no singletons beyond app-lifetime resources
  (engine, loaded embedding model) held on `app.state`. **Alternatives:** DI container library; module globals.
  **Reasoning:** plain constructors suffice. **Tradeoffs:** manual wiring. **Consequences:** tests override providers.
- **Implemented:** partially (`create_app(settings)` injects settings/engine; tests use it).

## D12. Grounded RAG-based clinical intelligence architecture
- **Context:** summaries must be supported by the record and attributable. **Decision:** patient-scoped
  retrieval -> context assembly -> LLM with structured output -> schema + attribution validation ->
  persist/cache. **Alternatives:** stuff all records into the prompt; fine-tuning; free-text output
  without validation. **Reasoning:** evidence-constrained, checkable, cacheable. **Tradeoffs:** extra
  moving parts; hallucination reduced, not eliminated, so deterministic checks + human rubric are required;
  for a small per-patient record set, "retrieval" may reduce to selecting all that patient's records.
  **Consequences:** no clinical-accuracy claims from API success alone.
- **Implemented:** no (Phase 4).

## D13. Deterministic processing for identity resolution and FHIR mapping
- **Context:** wrong merges or invented FHIR data are clinical-safety failures. **Decision:** rule-based
  normalization, exact-identifier matching, content-hash duplicates, ambiguous -> manual review,
  deterministic FHIR mapping; no LLM in these steps. **Alternatives:** fuzzy/ML matching.
  **Reasoning:** explainable, auditable, reproducible. **Tradeoffs:** more records flagged for review.
  **Consequences:** every decision stores its rationale.
- **Implemented:** no (Phases 2-3).

## D14. SQLite as source of truth, ChromaDB as derived index
- **Context:** two stores can disagree. **Decision:** SQLite authoritative; Chroma rebuilt from it;
  indexing states tracked and retryable; idempotent upserts keyed by stable IDs. **Alternatives:**
  Chroma as primary; dual-write without status. **Reasoning:** recoverability. **Tradeoffs:** eventual
  consistency between stores. **Consequences:** a rebuild command is required.
- **Implemented:** SQLite engine only.

---
# Phase 2 decisions (2026-11-11)

## D15. Two hashes: raw `source_hash` (idempotency) and normalized `content_hash` (duplicates)
- **Context:** reprocessing must not duplicate, and differently formatted copies of one record must be
  detected. **Decision:** unique `source_hash` over raw fields skips identical re-submissions;
  `content_hash` over normalized content flags duplicates. **Alternatives:** one hash; row-number/file-name
  keys. **Reasoning:** a single raw hash misses reformatted duplicates; a single normalized hash would hide
  that the same input arrived twice. **Tradeoffs:** edits create new records (no supersede logic).
  **Consequences:** deterministic stable `record_id`.
- **Implemented:** yes.

## D16. Per-record three-phase transaction with an explicit state machine
- **Context:** a half-processed record must never look successful. **Decision:** commit receipt, process
  atomically, record failure separately; `received`/`failed` are retryable. **Alternatives:** one transaction
  per file (one bad record loses everything); no persisted failure state. **Reasoning:** records are never lost
  and partial writes roll back. **Tradeoffs:** several small transactions; retry happens on re-submission,
  not via a background worker. **Consequences:** `UnitOfWork` port.
- **Implemented:** yes; tested with injected failures.

## D17. Unit of Work port in addition to repositories
- **Context:** patient, record and audit writes must commit or roll back together. **Decision:** a small
  `UnitOfWork` protocol exposing the three repositories. **Alternatives:** repositories sharing a session
  implicitly. **Reasoning:** makes the transaction boundary explicit and fakeable. **Tradeoffs:** one more
  abstraction. **Consequences:** fakes implement snapshot/rollback.
- **Implemented:** yes.

## D18. Strategies parse structure only; alias mapping and normalization are shared domain code
- **Context:** all formats must yield the same canonical record. **Decision:** strategies emit original keys;
  the domain maps aliases and normalizes. **Alternatives:** each strategy builds canonical records.
  **Reasoning:** one normalization path, retry can re-run from the stored raw payload. **Tradeoffs:** raw payload
  has to be stored. **Consequences:** a test proves JSON/CSV/text equivalence.
- **Implemented:** yes.

## D19. Conservative validation severities
- **Context:** quarantine versus silent repair. **Decision:** missing MRN and invalid MRN/DOB/document date
  are errors (record `rejected`, kept for correction); missing DOB/name/gender and reformatting are warnings.
  Ambiguous day/month dates are invalid. **Alternatives:** drop invalid fields and continue (would bypass
  identity checks silently). **Reasoning:** DOB drives conflict detection. **Tradeoffs:** stricter than lenient
  parsing. **Consequences:** rejected records need correction and resubmission.
- **Implemented:** yes.
