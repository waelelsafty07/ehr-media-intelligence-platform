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

## Open item for Phase 3 (resolved: see D20)
`fhir-resources` 8.x exposes R5 at the top level; R4 resources live under `fhir.resources.R4B`.

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

---
# Phase 3 decisions (2026-11-11)

## D20. Use `fhir.resources.R4B` (FHIR 4.3.0) as the R4 implementation, and only that namespace
- **Context:** the package's default namespace is FHIR 5.0.0; the assessment requires R4. **Decision:** import only
  `fhir.resources.R4B.*`, confined to `infrastructure/fhir`, enforced by an architecture test. **Alternatives:**
  `fhir.resources` 6.4.0 (true R4 4.0.1; requires Pydantic v1, incompatible with this stack); the top-level R5
  namespace; hand-written dicts without the library. **Reasoning:** I compared R4 4.0.1 and R4B 4.3.0 models for the
  eight classes used: element names and required elements are identical. **Tradeoffs:** R4B is not byte-for-byte
  R4 everywhere (e.g. medicinal-product resources), and this is evidence of structural equivalence for the used
  resources only. **Consequences:** only elements present in both are populated.
- **Implemented:** yes.

## D21. Three-layer FHIR validation instead of trusting the library
- **Context:** probing showed the library accepts invalid code values and dangling references. **Decision:**
  library validation + domain value-set checks + domain reference/attachment checks, all run before persisting.
  **Alternatives:** library only; an external validator (HAPI/Java) as a service. **Reasoning:** cheap, deterministic,
  offline; covers the failure modes that matter for the fields we populate. **Tradeoffs:** not a complete validator;
  rules are hand-maintained. **Consequences:** documented as a limitation.
- **Implemented:** yes.

## D22. Mapper emits only source-supported data; no coded or inferred content
- **Context:** "do not invent clinical facts". **Decision:** omit `DocumentReference.type/category/docStatus`; use
  `DiagnosticReport.status=unknown` and an uncoded `code.text`; names as text only; diagnoses verbatim.
  **Alternatives:** assign LOINC/SNOMED codes, status `final`. **Reasoning:** the source supports none of these.
  **Tradeoffs:** lower interoperability value than a coded record. **Consequences:** coding is future work needing a
  terminology source and clinical review.
- **Implemented:** yes.

## D23. Separate FHIR processing state; explicit generation; idempotent by fingerprint
- **Context:** ingestion success must not imply FHIR success. **Decision:** `patient_fhir_state`
  (`generated|invalid|failed`), generation via `POST /patients/{id}/fhir`, fingerprint of mapper version + patient +
  completed-record content hashes, resources replaced atomically per patient, bundle only served when `generated`.
  **Alternatives:** generate during ingestion; status columns on `source_records`; regenerate always.
  **Reasoning:** FHIR is per patient and depends on all of the patient's records; a failure must not roll back or
  relabel ingestion. **Tradeoffs:** an extra call; no automatic regeneration. **Consequences:** the bundle is
  re-assembled from stored resources rather than stored twice.
- **Implemented:** yes.

## D24. `urn:uuid` references and full URLs inside collection Bundles
- **Context:** no FHIR server base URL exists. **Decision:** `fullUrl` and references are `urn:uuid:<id>`;
  Bundle type `collection`. **Alternatives:** `transaction` bundle with PUT entries; relative references with a fake
  base URL. **Reasoning:** every reference resolves inside the bundle without inventing a base URL.
  **Tradeoffs:** not directly loadable as a transaction; references are not RESTful. **Consequences:** a future
  server-loading step would convert to a transaction.
- **Implemented:** yes.

---
# Phase 4 decisions (2026-11-11)

## D25. Statements carry verbatim evidence; verification is deterministic
- **Context:** summaries must be traceable and must not invent facts. **Decision:** the model must return, for each
  chief concern/diagnosis/anomaly, an exact `evidence_quote` and `source_record_ids`; the pipeline verifies IDs, quote
  presence in a cited record, diagnosis-in-quote, hedging/history/negation consistency, dates/numbers and advice, and
  rejects (re-asks) on failure. **Alternatives:** trust the model; second-LLM judge; free text with citations only.
  **Reasoning:** checkable without another model, cheap, explainable. **Tradeoffs:** a quote proves presence of words,
  not faithful paraphrase; heuristics are English-only and can over-reject; more output tokens.
  **Consequences:** limits documented in AI_EVALUATION.md; human review still required.
- **Implemented:** yes.

## D26. Deterministic parts are computed, not generated
- **Context:** correctness and traceability beat fluency for list-like fields. **Decision:** `recent_records`,
  `missing_information`, `source_record_ids` and the quality indicator are built in code; the model writes only chief
  concern, diagnoses, anomalies and prose. **Alternatives:** have the model produce everything. **Reasoning:** fewer
  hallucination surfaces. **Tradeoffs:** less flexible wording. **Implemented:** yes.

## D27. The quality indicator is an evidence-coverage heuristic, not model confidence
- **Context:** an uncalibrated LLM confidence is not clinically meaningful. **Decision:** low/medium/high from four
  transparent factors (chief concern documented, diagnoses stated, >=2 records, all records dated), with factors and an
  explanation returned. **Alternatives:** ask the model for a score; omit. **Tradeoffs:** says nothing about output
  correctness. **Implemented:** yes.

## D28. Cache key = (patient, hash of exact context, prompt version, model); failed rows are retryable state
- **Context:** regenerate when content or generation settings change; recover from failures. **Decision:** the hash
  covers record ids, normalized content hashes, truncation and the record/char limits; prompt version and model are
  separate key parts; one row per key with status `generated`/`failed`; failed rows are never hits; `GET` reports
  staleness. **Alternatives:** time-based TTL; in-memory cache; hash of prompt text. **Reasoning:** deterministic,
  persistent, explainable invalidation. **Tradeoffs:** prompt text changes require bumping `PROMPT_VERSION` by hand
  (documented in the prompt module); old rows accumulate. **Implemented:** yes.

## D29. No DB transaction during the provider call; one attempt budget for transient errors and invalid output
- **Context:** provider calls are slow and fail. **Decision:** read + cache check in a short transaction, call the
  provider outside any transaction, persist the outcome in a new one; transient errors back off exponentially, invalid
  output is re-asked, permanent errors stop immediately, all within `summary_max_attempts`. **Alternatives:** SDK
  built-in retries; unlimited re-asking. **Reasoning:** explicit, testable retry policy; no long-held write locks.
  **Tradeoffs:** concurrent duplicate calls possible. **Implemented:** yes.

## D30. Minimize what leaves the system; never propagate provider text
- **Decision:** the prompt contains only clinical text and diagnoses (no name, MRN, DOB, patient ID); the adapter
  converts SDK errors to generic categorized errors (observed: a 429 message echoed a key-like string); logs and audit
  hold codes/counts only. **Tradeoffs:** clinical text itself may contain identifiers in real life (synthetic here).
  **Implemented:** yes.

## D31. Chat Completions JSON mode, not provider-side structured-output schemas
- **Context:** the live API cannot be exercised here. **Decision:** request `json_object` mode and enforce the schema
  ourselves with Pydantic. **Alternatives:** `json_schema` strict mode (unverifiable without live access, and stricter
  schema subset), Responses API. **Reasoning:** provider-agnostic validation we fully control and test. **Tradeoffs:**
  relies on the model following the described format; the pipeline re-asks on violations. **Implemented:** yes.

---
# Phase 5 decisions (2026-11-11)

## D32. SQLite-backed vector store (exact cosine, numpy) behind a `VectorStore` port; not ChromaDB/FAISS
- **Context:** the earlier plan (D14, original stack) assumed ChromaDB as a derived index. This phase asked for an evidence-based choice.
  **Verified:** ChromaDB, FAISS and sentence-transformers all resolve on Python 3.12 and 3.14 - there is **no compatibility problem**; ChromaDB would add
  84 packages (onnxruntime, grpc, kubernetes, ...), FAISS 3, numpy 1. **Decision:** store vectors as float32 BLOBs in the existing SQLite database and score
  exactly with numpy. **Alternatives:** ChromaDB (original plan), FAISS. **Reasoning:** at this scale ANN buys nothing; one database means no second
  store to reconcile; patient/type/date filters are plain SQL applied before ranking, which makes strict patient isolation simple and auditable;
  tests need no extra service or model. **Tradeoffs:** O(n) scan (measured: 130 ms at 20,000 entries, store-only), no ANN, fewer features. **Consequences:**
  deviates from the original ChromaDB plan; a Chroma/FAISS adapter is a small addition behind the same port if needed.
- **Implemented:** yes.

## D33. Two embedding providers; every response reports the real mode  *(default superseded by D40)*
- **Context:** the normal test suite and a fresh clone must not need a download, network or API key; yet semantic search needs a real model.
  **Decision:** `hashing` (deterministic lexical embedder; default and test double) and `sentence-transformers` all-MiniLM-L6-v2 (optional extra,
  lazy load, explicit install and download). Responses and status report `embedding.kind`. **Alternatives:** make the model mandatory; ship only
  a mock. **Reasoning:** reproducible default, genuine semantics available, no silent mislabeling. **Tradeoffs:** the default does not match synonyms
  (measured: top-1 0.75 vs 1.00 with the real model). **Implemented:** yes (Strategy justified: two real implementations).

## D34. Indexing is a per-patient desired-state reconcile, keyed by content hash
- **Decision:** desired entries = DocumentReferences of a `generated` FHIR state + the latest generated non-stale summary; embed only entries whose hash
  (model, type, date, text) changed; upsert, then delete entries no longer desired. **Alternatives:** event-driven incremental updates; delete-and-rebuild.
  **Reasoning:** one rule covers update, delete and invalidation, is idempotent and trivially retryable, and never reduces the index on failure.
  **Tradeoffs:** explicit trigger (not automatic); a stale entry can persist until re-index (never shown, see D35). **Implemented:** yes.

## D35. Search re-validates every hit against SQLite; identity never comes from similarity
- **Decision:** after retrieving candidates (over-fetched), drop any hit whose resource no longer exists, belongs to another patient, changed text,
  FHIR state not `generated`, or (for summaries) is not the current non-stale summary; also drop hits outside the requested patient scope even if the
  store returned them. **Alternatives:** trust the index. **Reasoning:** the index is derived and can lag; SQLite is the source of truth; defense in
  depth for a safety-sensitive boundary. **Tradeoffs:** a few extra reads per query; fewer than `limit` results are possible when candidates are stale.
  **Implemented:** yes; mutation-checked (disabling each check fails tests).

## D36. Scores are cosine similarity; non-positive similarity is never a result
- **Context:** a smoke test returned five unrelated documents with score 0.0 for a nonsense query. **Decision:** never return similarity <= 0;
  `min_score` can only raise the floor; every response carries a note that scores are not clinical relevance or probability. **Implemented:** yes.

## D37. ~~Patient display fields in results: name and MRN, not DOB~~  *(superseded by D39: removed)*
- **Decision:** results include `patient_id`, `patient_name`, `mrn` (needed by the clinician UI), never date of birth. **Tradeoff:** these identifiers are
  exposed to any caller because there is no authentication yet. **Consequence:** authorization must precede real use. **Implemented:** yes.

## D38. Local embeddings only; only clinical text is embedded
- **Decision:** both providers run locally; the embedder receives only document/summary text, never name, MRN or DOB (tested); query text is never
  logged. **Reasoning:** no patient data leaves the machine for embedding. **Tradeoff:** note text may itself contain identifiers in real data (synthetic here).
  **Implemented:** yes.

---
# Phase 5 review adjustments (2026-11-11)

## D39. Search results carry no direct patient identifiers; filtering is not authorization; no fake authentication
- **Context:** the first version returned `patient_name` and `mrn` in every result and allowed unscoped cross-patient search with no access control (D37).
  **Decision:** results contain an opaque `patient_id` but never name, MRN or date of birth (tested on responses and the OpenAPI schema); the use case loads the patient only to
  confirm it exists. The docs state plainly that patient filtering is data scoping, that there is no authentication or authorization, that this is a local demo for synthetic
  data only, and what production would need ([SECURITY.md](SECURITY.md)). **Alternatives:** keep names/MRNs for the UI; add a placeholder login. **Reasoning:** minimum necessary
  exposure; a cosmetic auth mechanism would create false assurance; the assessment does not require building authentication. **Tradeoffs:** a clinician UI needs identity for display,
  which must come from a separate, explicitly authorized lookup (none exists yet; the Phase 6 UI will need a documented decision). The `snippet` can still contain whatever the stored
  text contains, and other endpoints (FHIR bundle) still return full Patient resources. **Preserved:** SQL patient scoping, the use-case check and the final ownership re-validation.
- **Implemented:** yes.

## D40. Semantic search (all-MiniLM-L6-v2) is the default; lexical is an explicit fallback; no silent fallback
- **Context:** the assessment requires semantic search with embeddings; a lexical default (D33) did not meet it. **Decision:** `EHR_EMBEDDING_PROVIDER` defaults to
  `sentence-transformers` (`all-MiniLM-L6-v2`); `pip install -e "./backend[dev,embeddings]"` and `python backend/scripts/init_embedding_model.py` are part of the documented setup.
  If the model is unavailable, search and indexing return 503 `embedding_provider_unavailable` with instructions, `/search/status` reports `available`, and startup logs a
  warning. `EHR_EMBEDDING_PROVIDER=hashing` selects the lexical fallback explicitly; responses always report the actual `kind`. The normal test suite stays offline: the shared
  fixture chooses the lexical embedder explicitly and an autouse guard blocks `sentence_transformers`, so no test can load or download a model; semantic mode is tested with a faked
  model, and one opt-in test uses the real one. **Alternatives:** keep lexical default; auto-fallback to lexical when unavailable (rejected: would silently misrepresent
  results). **Tradeoffs:** heavier setup (~1 GB PyTorch, one-time ~90 MB download), 3.7 s cold start, ~8 ms warm search; a fresh clone without the extra gets 503 on search until set up.
  **Implemented:** yes; mutation-checked (reverting the default, adding a silent fallback and re-adding identifiers each fail tests).

## D41. Patient names/MRNs reach the UI only through a separate `GET /patients/{id}/display`
**Decision:** search results stay identifier-free (D40); the clinician UI needs name and MRN on result cards, so a dedicated endpoint returns exactly `patient_id`, `patient_name`, `mrn`
(no DOB/gender/notes). The UI fetches it per patient, caches in memory for the page session, and degrades to "Patient details unavailable" on failure. **Alternatives:** put names in
`/search` (rejected: reverses D40); `GET /patients` listing (rejected: broader exposure, not needed). **Tradeoffs:** extra requests (one per distinct patient); the endpoint exposes name+MRN to
any caller by patient id. **Security:** no authentication/authorization exists; this is a demo display lookup, not access control. **Also:** the patient filter is an opaque-id text box (+ "Only this patient" on a card)
because there is deliberately no listing endpoint. **Implemented:** yes.

## D42. Focused FHIR resource endpoint instead of downloading the whole Bundle in the UI
**Decision:** `GET /patients/{id}/fhir/resources/{type}/{rid}` returns one stored resource (DocumentReference/DiagnosticReport only), reusing `FhirRepository.get_resource`; the use case checks the patient
exists, FHIR state is `generated`, and `record.patient_id == patient_id`. Missing, foreign and unserved-type all yield the same 404 `fhir_resource_not_found`. The `Patient` resource is refused
(still in the Bundle). **Why:** the drawer needed one DocumentReference but was pulling the full Patient (name, MRN, DOB) over the wire. **Alternatives:** keep the Bundle (rejected: needless exposure); id-only path
(rejected: resource ids are unique only per type). **Tradeoffs:** one more endpoint; the type in the path. **Limits:** does not reduce what the unauthenticated Bundle endpoint exposes. **Implemented:** yes.

## D43. HTTP 429 is split by structured error code: `insufficient_quota` is permanent, other 429s stay transient
**Context:** the first live request failed as `provider_unavailable` (transient, retried up to 3 times); the likely cause was a 429 for exhausted billing/quota, which the adapter treated like a rate limit.
**Decision:** the adapter inspects only the SDK's structured `code`/`type` (short strings; never the message). `insufficient_quota` raises `LLMQuotaExhaustedError` (a permanent error, API reason `provider_quota_exhausted`, one attempt, no backoff).
All other 429s, 5xx, 408/409/425, timeouts, connection errors and malformed 200 bodies remain transient with the unchanged bounded retry; 401/403/404/400 remain permanent. Timeouts, attempt limits and backoff are unchanged.
**Tradeoffs:** relies on the provider's documented code; an unrecognised quota code would still be retried (bounded). **Not changed:** schema, prompts, grounding. **Verified:** mocked-transport tests only; not against the live API.
