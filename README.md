# EHR Media Intelligence Platform

A technical-assessment project that ingests heterogeneous healthcare records, normalizes them, resolves
patient identity conservatively, transforms them to FHIR R4, generates grounded AI clinical summaries,
searches them semantically,
and presents them in a clinician search UI.

> **LOCAL ASSESSMENT / DEMO SYSTEM. Do not use it with real patient data.**
> It has **no authentication and no authorization**: anyone who can reach the API can read and modify all data, including patient names, MRNs
> and dates of birth (the FHIR endpoint returns full Patient resources). Patient filtering in search is data scoping, **not** access control.
> Not a medical device; AI output is unverified; no HIPAA or other compliance claim has been made or assessed.
> A production deployment would need authenticated users, authorization checks and patient-level access policies, encryption, audit review and
> a compliance assessment (see [docs/SECURITY.md](docs/SECURITY.md)).

## Implementation status

| Assessment task | Status |
|---|---|
| Foundation (API skeleton, logging, config, tests) | **Implemented** |
| Task 1: Data ingestion and cleaning (JSON/CSV/text) | **Implemented** |
| Task 2: FHIR R4 transformation and validation | **Implemented** (Patient, DocumentReference, DiagnosticReport, Bundle) |
| Task 3: AI clinical summaries (OpenAI) | **Implemented; one live OpenAI request verified** (fakes, the real SDK over a mocked transport, a local HTTP stub, and a single real `gpt-4o-mini` call that passed the application's own checks; see "Live OpenAI verification"). Not clinically validated |
| Task 4: Semantic search | **Implemented** (`POST /search`; default = real `all-MiniLM-L6-v2` embeddings, which need the `embeddings` extra; lexical mode is an explicit fallback) |
| Task 5: Clinician interface | **Implemented** (React search UI: filters, ranked cards, detail drawer; verified with mocked-API tests and a one-off real-Chrome smoke test) |

Everything below describes what exists unless it is explicitly marked **planned**.

## What works today

- `POST /ingest` accepts a `.json`, `.csv` or `.txt` file of records and returns a per-record result.
- Deterministic normalization of dates, gender, MRN and names (no AI). Ambiguous dates such as `03/04/2020`
  are rejected as invalid, never guessed. Missing values are reported, never invented.
- Validation issues are classified: missing required, missing optional, invalid, recovered format, duplicate,
  identity conflict, ambiguous match.
- Duplicate detection (identical normalized content) and conservative patient identity resolution: exact
  MRN-based matching; a conflicting DOB, a differing name/gender on the same MRN, or the same name+DOB under a new
  MRN is held as `needs_review` and never merged. Names are never fuzzy-matched.
- Idempotent reprocessing, explicit processing states, atomic per-record transactions, recoverable failures,
  and an append-only audit log (codes and IDs only, no clinical content).
- FHIR R4 generation per patient (`POST /patients/{id}/fhir`): Patient, DocumentReference, DiagnosticReport and a
  collection Bundle with `urn:uuid` references, three-layer validation, idempotent regeneration, persisted resources,
  and a FHIR status that is separate from ingestion status. Only source-supported data is mapped (no invented codes).
- AI clinical summaries (`POST/GET /patients/{id}/summary`): a grounded pipeline over the patient's own completed records
  (no name/MRN/DOB sent to the model), strict structured output where every statement carries an exact evidence quote and
  source record IDs, deterministic checks that reject unsupported claims (invented diagnoses, hedged findings stated as
  confirmed, history shown as current, wrong dates/numbers, advice, >200 words, other patients' records), computed recent
  records / missing information / quality indicator, caching (patient + records hash + prompt version + model),
  retries with backoff, and recorded retryable failures. **The checks do not guarantee clinical correctness**; see
  [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md). Without an API key the endpoint answers 503 and everything else works.
- Structured JSON logs with request IDs and secret redaction; consistent error responses.

## Architecture

Clean Architecture inside a modular monolith (details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).

```
api (FastAPI routes, schemas, DI)  ->  application (use cases, ingestion strategies)  ->  domain  <-  infrastructure (SQLite)
```

- `backend/app/domain`: canonical models, normalization, identity policy, entities, repository/unit-of-work ports.
  Imports no framework (enforced by `tests/test_architecture.py`).
- `backend/app/application`: `IngestPatientRecordsUseCase`, `ResolvePatientIdentityUseCase`, JSON/CSV/Text
  ingestion strategies selected by `IngestionStrategyFactory`.
- `backend/app/infrastructure/persistence`: SQLAlchemy tables, repositories, unit of work (SQLite).
- `backend/app/api`, `backend/app/core`: HTTP layer; settings, logging, error envelope.
- `frontend/`: React + Vite + TypeScript + Tailwind clinician search UI (`src/api` client, `src/components`, `src/hooks`).

Ingestion data flow: upload -> strategy parses structure -> store raw record (`received`) -> normalize ->
duplicate check -> identity decision -> patient/status/audit written in one transaction -> response.
States: `received -> completed | rejected | duplicate | needs_review | failed` (`received`/`failed` are retried
when the same content is submitted again).

FHIR flow: patient + `completed` records -> fingerprint (skip if unchanged) -> deterministic mapping with
`fhir.resources.R4B` -> Bundle -> value-set / reference / attachment checks + library validation -> resources
replaced atomically, state `generated` (`invalid`/`failed` serve nothing). See [docs/FHIR_MAPPING.md](docs/FHIR_MAPPING.md).

AI summary flow: patient-scoped records -> context -> cache lookup -> `LLMProvider` (OpenAI adapter) -> parse + strict
schema -> grounding checks -> persist; details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Search flow: FHIR DocumentReference text + current AI summaries -> embeddings -> vector rows in SQLite (a derived, rebuildable index) ->
`POST /search` applies patient/type/date filters before ranking, ranks by cosine similarity, re-validates every hit against SQLite, and returns
at most 5 results with scores, snippets and links. Why a SQLite vector store and not ChromaDB: D32 in [docs/DECISIONS.md](docs/DECISIONS.md).

UI flow: the UI calls `/search`, then (separately) `GET /patients/{id}/display` for names/MRNs, and `GET /patients/{id}/fhir/resources/...` or `/summary` for the detail drawer.

## Technology stack (actual dependencies)

| Package | Role |
|---|---|
| FastAPI, Uvicorn | HTTP API and server |
| Pydantic v2, pydantic-settings | Models, validation, environment configuration |
| SQLAlchemy 2 + SQLite | Persistence |
| python-multipart | File upload parsing |
| fhir.resources (R4B namespace, FHIR 4.3.0) | FHIR R4 resource models and structural validation |
| openai (SDK) | Chat Completions client, used only by `OpenAIAdapter` |
| numpy | Exact cosine scoring in the SQLite vector store |
| sentence-transformers (optional extra) | Real local embeddings, `all-MiniLM-L6-v2` (pulls in torch) |
| pytest, httpx, Ruff (dev) | Tests, FastAPI test client, lint |
| React, Vite, TypeScript, Tailwind CSS, Vitest (frontend) | UI shell and tests |

## Prerequisites

- Python 3.11 or newer (verified on 3.12.14 and 3.14.7, both including the `embeddings` extra: PyTorch 2.15.0, sentence-transformers 6.2.0).
- Node.js 22.12 or newer (verified on 24.19.0) and npm (verified 11.17.0). Vitest 5 requires Node 22.12+.
- Git.
- For the default (semantic) search mode: about 1 GB of disk for the `embeddings` extra (it installs PyTorch) and network access **once** to download the
  `all-MiniLM-L6-v2` model (~90 MB) from the Hugging Face Hub. After that it runs fully offline and locally.

## Installation and setup

macOS / Linux:

```bash
git clone https://github.com/waelelsafty07/ehr-media-intelligence-platform.git
cd ehr-media-intelligence-platform

python3 -m venv .venv
source .venv/bin/activate
pip install -e "./backend[dev,embeddings]"      # `embeddings` = sentence-transformers + PyTorch (needed for the default semantic search)
cp backend/.env.example backend/.env
python backend/scripts/init_embedding_model.py  # downloads/verifies all-MiniLM-L6-v2 (network once); prints "OK" when ready

cd frontend && npm ci && cd ..
```

Do you only want to run the tests or the lexical fallback? `pip install -e "./backend[dev]"` is enough: the test suite is fully offline and never downloads
a model. Without the extra, the default semantic mode answers search requests with a clear `503` (see "Search modes"), and nothing else is affected.

Windows (PowerShell). These commands are the equivalents but **were not tested on Windows**:

```powershell
git clone https://github.com/waelelsafty07/ehr-media-intelligence-platform.git
cd ehr-media-intelligence-platform
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".\backend[dev,embeddings]"
copy backend\.env.example backend\.env
python backend\scripts\init_embedding_model.py
cd frontend; npm ci; cd ..
```

## Environment configuration

[backend/.env.example](backend/.env.example) is copied to `backend/.env` (git-ignored). All variables use the
`EHR_` prefix; none are required for the current functionality.

| Variable | Default | Purpose |
|---|---|---|
| `EHR_ENVIRONMENT` | `development` | Label included in logs |
| `EHR_LOG_LEVEL` | `INFO` | Logging level |
| `EHR_DATABASE_URL` | `sqlite:///./data/ehr.db` | SQLite file (relative to `backend/` when started there; created automatically) |
| `EHR_MAX_UPLOAD_BYTES` | `5242880` | Upload size limit (413 above it) |
| `EHR_OPENAI_API_KEY` | empty | Enables AI summaries. Empty = summaries answer 503; never commit a real key |
| `EHR_OPENAI_MODEL` | `gpt-4o-mini` | Model id (part of the cache key); confirm it is available to your account |
| `EHR_OPENAI_TIMEOUT_SECONDS`, `EHR_OPENAI_TEMPERATURE` | `30`, `0.0` | Request timeout; sampling temperature (empty omits it) |
| `EHR_SUMMARY_MAX_ATTEMPTS`, `EHR_SUMMARY_RETRY_BASE_SECONDS` | `3`, `1.0` | Attempt budget (transient errors + invalid output); backoff base |
| `EHR_SUMMARY_MAX_CONTEXT_RECORDS`, `EHR_SUMMARY_MAX_CHARS_PER_RECORD`, `EHR_SUMMARY_MAX_OUTPUT_TOKENS` | `20`, `4000`, `1200` | Context and output limits |
| `EHR_EMBEDDING_PROVIDER` | `sentence-transformers` | **Default: real semantic embeddings** (`all-MiniLM-L6-v2`; needs the `embeddings` extra). `hashing` = deterministic **lexical** embedder (shared words only): an explicit fallback you must choose; tests use it |
| `EHR_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Model used when the provider is `sentence-transformers` |

`EHR_CORS_ORIGINS` (JSON list, default `["http://localhost:5173"]`) can also be set. Never commit `.env` or real keys.

## Running the application

```bash
# Terminal 1: backend (from the repository root, venv active)
cd backend
uvicorn app.main:app --reload
# http://localhost:8000  |  Swagger UI: http://localhost:8000/docs  |  OpenAPI: /openapi.json

# Terminal 2: frontend
cd frontend
npm run dev
# http://localhost:5173 (proxies /api/* to the backend; the clinician search UI)
```

## API documentation

Interactive docs: `http://localhost:8000/docs`. Implemented endpoints:

### `GET /health`
```bash
curl http://localhost:8000/health
# {"status":"ok","database":"ok","version":"0.1.0"}   (status "degraded" if the database is unreachable)
```

### `POST /ingest` (multipart form)
Fields: `file` (required; `.json`, `.csv` or `.txt`), `format` (optional: `json` | `csv` | `text`; overrides the extension).

```bash
curl -F "file=@data/samples/records.json" http://localhost:8000/ingest
```
Abbreviated actual response:
```json
{
  "source_name": "records.json", "source_format": "json", "total": 4,
  "counts": {"completed": 3, "duplicate": 1},
  "results": [
    {"source_id": "records.json#0", "record_id": "90c035d4-...", "status": "completed", "attempts": 1,
     "patient_id": "a82314c8-...", "duplicate_of_record_id": null,
     "identity_rationale_code": "new_mrn_no_conflicting_match", "already_ingested": false,
     "issues": [{"code": "recovered_format", "field": "mrn", "severity": "warning",
                 "message": "MRN was reformatted to canonical form."}]},
    {"source_id": "records.json#1", "status": "duplicate", "duplicate_of_record_id": "90c035d4-...", "...": "..."}
  ]
}
```
Re-uploading identical content returns `"already_ingested": true` and creates nothing new.

| Status | Meaning |
|---|---|
| 200 | Processed (see per-record `status`: `completed`, `rejected`, `duplicate`, `needs_review`, `failed`) |
| 413 `file_too_large` | Over `EHR_MAX_UPLOAD_BYTES` |
| 415 `unsupported_format` | Not json/csv/text |
| 422 `ingestion_parse_error` | Whole file unusable (malformed JSON, CSV without recognizable header, empty, not UTF-8) |
| 422 `validation_error` | Missing `file` field etc. |

### FHIR endpoints
Use a `patient_id` from the `/ingest` response.

```bash
# 1. ingest a sample that contains diagnoses
curl -F "file=@data/samples/records_with_diagnoses.json" http://localhost:8000/ingest
# 2. generate FHIR (idempotent; a second call returns "unchanged": true)
curl -X POST http://localhost:8000/patients/<patient_id>/fhir
# {"patient_id":"...","status":"generated","unchanged":false,"bundle_id":"...",
#  "resource_counts":{"Patient":1,"DocumentReference":2,"DiagnosticReport":1},"issues":[],"updated_at":"..."}
# 3. retrieve the Bundle (Content-Type: application/fhir+json)
curl http://localhost:8000/patients/<patient_id>/fhir
# 4. FHIR processing state: generated | invalid | failed | not_generated
curl http://localhost:8000/patients/<patient_id>/fhir/status
```

| Status | Meaning |
|---|---|
| 200 | Generated / unchanged / bundle returned |
| 404 `patient_not_found` | Unknown patient ID |
| 409 `fhir_not_available` | Bundle requested but state is not `generated` (never generated, invalid, or failed) |
| 422 `fhir_validation_failed` | Generated FHIR failed validation; `error.details` lists `path` + `code` per issue |
| 500 `fhir_generation_failed` | Unexpected failure (recorded; call POST again to retry) |

### Summary endpoints
Require `EHR_OPENAI_API_KEY` in `backend/.env` (restart the backend after setting it). Full contracts: [docs/API_CONTRACTS.md](docs/API_CONTRACTS.md).

```bash
curl -F "file=@data/samples/records_for_summary.json" http://localhost:8000/ingest      # note a patient_id
curl -X POST http://localhost:8000/patients/<patient_id>/summary    # generate (second call: "cache_hit": true)
curl http://localhost:8000/patients/<patient_id>/summary            # latest; "is_stale" is true after new records
```
The response contains `chief_concern`, `key_diagnoses` (each with `certainty` and `temporal`), `recent_records`, `anomalies`,
`missing_information`, `summary_text` (<= 200 words), `source_record_ids`, `confidence_or_quality_indicator` (an
evidence-coverage heuristic, **not** a probability or model confidence), `generated_at`, `model_identifier`,
`prompt_version` and a disclaimer.

| Status | Meaning |
|---|---|
| 404 `patient_not_found` / `summary_not_found` | Unknown patient / nothing generated yet |
| 409 `no_source_records` | Patient has no completed records with text or stated diagnoses |
| 502 `summary_generation_failed` | After retries; `details[0].reason` is provider_unavailable, provider_timeout, provider_rejected, invalid_output or persistence_error. Call POST again to retry |
| 503 `ai_provider_not_configured` | No API key configured |

### Search endpoints
Indexing is an explicit, idempotent step: FHIR must be generated first (`POST /patients/{id}/fhir`); summaries are indexed only when generated and
not stale. Contracts: [docs/API_CONTRACTS.md](docs/API_CONTRACTS.md).

```bash
curl -X POST http://localhost:8000/search/reindex                    # index every patient (second call: indexed 0, unchanged N)
curl -X POST http://localhost:8000/patients/<patient_id>/index       # or one patient
curl http://localhost:8000/search/status                             # entries, models, embedding kind
curl -X POST http://localhost:8000/search -H 'content-type: application/json' \
  -d '{"query": "chest tightness on exertion", "limit": 5}'
curl -X POST http://localhost:8000/search -H 'content-type: application/json' \
  -d '{"query": "cough", "patient_id": "<patient_id>", "resource_types": ["DocumentReference"], "date_from": "2024-01-01", "date_to": "2024-12-31"}'
```
Each result has `rank`, `resource_type` (`DocumentReference` or AI-generated `ClinicalSummary`), `resource_id`, `fhir_reference`, `link`, `patient_id`
(an opaque internal id), `record_date`, `score`, `snippet`, `source_record_id`, `ai_generated`. **Results contain no patient name, MRN or date of birth**
(the `snippet` is an excerpt of the stored clinical text, so it can contain whatever that text contains). `score` is embedding similarity, **not** clinical relevance
or probability. The response states the actual mode in `embedding` (`model_identifier`, `kind`: `semantic` or `lexical`). The semantic model also returns weak runners-up (e.g. 0.07 for an unrelated
note), so use `min_score` (e.g. `0.25`) to cut low-similarity results; results with similarity <= 0 are never returned. Limit is at most 5. Unknown fields, blank queries, bad dates or types
return 422; an unknown `patient_id` returns 404; an unavailable embedder or index returns 503.

| Status | Meaning |
|---|---|
| 422 `validation_error` | Invalid request (details list locations, never the input) |
| 404 `patient_not_found` | Unknown `patient_id` filter |
| 503 `embedding_provider_unavailable` | Provider not usable (e.g. `sentence-transformers` selected but the extra is not installed) |
| 503 `search_index_unavailable` | Vector store failure |

#### Search modes
**Default: semantic.** `EHR_EMBEDDING_PROVIDER=sentence-transformers` with `all-MiniLM-L6-v2`, running locally. One-time setup (already part of "Installation"):
```bash
pip install -e "./backend[embeddings]"           # sentence-transformers + PyTorch (~1 GB)
python backend/scripts/init_embedding_model.py   # downloads the model once (needs network), verifies it, exits 0 when ready
```
The model loads lazily on the first request (cold start ~4 s with the model on disk; ~50 s the very first time in a fresh environment), so the app starts
instantly. `GET /search/status` shows `embedding.available` (is the library installed?) and the app logs a warning at startup if it is not.

**If the semantic model is unavailable** (extra not installed, model cannot be loaded), search and indexing return `503 embedding_provider_unavailable` with
instructions. The application **never silently falls back** to lexical search and never labels lexical results as semantic.

**Explicit lexical fallback** (no PyTorch, no download): set `EHR_EMBEDDING_PROVIDER=hashing` in `backend/.env` and restart. It is a deterministic bag-of-words
embedder: it matches shared (lightly stemmed) words only, so "dyspnea" will not find "shortness of breath". Responses then report `"kind": "lexical"`.
Switching mode re-embeds on the next `POST /search/reindex`; vectors from different models are never compared.

The test suite always uses the lexical embedder (or a faked model) and a guard blocks any real model load, so `pytest` is offline. Optional real-model test:
`EHR_RUN_REAL_EMBEDDING_TESTS=1 pytest backend/tests/optional -v`.

#### Benchmark
`python backend/scripts/benchmark_search.py` (default = semantic; `--provider hashing` for the lexical fallback; `--scale 1000,5000,20000` for store scaling).
Measured results, conditions and caveats are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): with the default semantic mode, warm search over 50 records took a
median of 8.0 ms (max 9.9 ms); the lexical fallback 3.1 ms.

Errors share one envelope: `{"error": {"code", "message", "request_id", "details"?}}`. There is no `/patients` listing endpoint yet.

### Clinician search UI and patient display lookup
`npm run dev` serves the UI at http://localhost:5173. Prepare data first (ingest, `POST /patients/{id}/fhir`, `POST /search/reindex`), then search, filter (patient id, resource type, date range),
and open a result to see the full document or AI summary with its FHIR references (each links to the individual FHIR resource via `GET /patients/{id}/fhir/resources/{type}/{rid}`; the UI never downloads the whole Bundle). Cards show patient name and MRN, which come from a **separate** call, `GET /patients/{patient_id}/display`
(returns only `patient_id`, `patient_name`, `mrn`); `/search` results intentionally contain no names or MRNs. If the lookup fails the card shows "Patient details unavailable".
Lookups are cached in memory per page session only. Scores are embedding similarity, not clinical relevance. AI summaries are labelled and carry the clinical disclaimer.
**Local synthetic-data demo: no authentication or authorization; do not use with real patient data.**

## Sample data

[data/samples/](data/samples/) holds fabricated records with deliberate defects:
- `records.json`: a clean record; a reformatted duplicate of it; a record with no date of birth; a patient with the
  same name but a different DOB and MRN (stays a separate patient).
- Search demo: ingest the sample files, call `POST /patients/{id}/fhir` for each patient, then `POST /search/reindex` and `POST /search`.
- `records_for_summary.json`: two notes for one patient (hedged finding, history, resolved symptom) for the summary demo.
- `records_with_diagnoses.json`: records that state diagnoses, for the FHIR demo (DocumentReference + DiagnosticReport).
- `records.csv`: a clean row and a row with an impossible date (`31/02/1990`, rejected).
- `note.txt`: a plain-text note with `Label: value` header lines.

## Running tests and lint

```bash
# from the repository root with the venv active (canonical command)
pytest                       # or: python -m pytest
ruff check backend           # lint

cd frontend && npm test      # frontend (Vitest)
```
Current result: 503 backend tests passed + 1 opt-in test skipped, 24 frontend tests, lint clean, production build OK. No network or API key is needed.
Strategy and coverage map: [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md).

## Design decisions

- Architecture and flows: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Decision records with alternatives and tradeoffs: [docs/DECISIONS.md](docs/DECISIONS.md)
- Testing: [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md)
- API contracts: [docs/API_CONTRACTS.md](docs/API_CONTRACTS.md)
- Database schema: [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)
- AI evaluation and human-review rubric: [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md)
- FHIR mapping, version compatibility evidence and validation limits: [docs/FHIR_MAPPING.md](docs/FHIR_MAPPING.md)
- One-page technical write-up: [docs/TECHNICAL_WRITEUP.pdf](docs/TECHNICAL_WRITEUP.pdf); delivery checklist: [docs/DELIVERY_CHECKLIST.md](docs/DELIVERY_CHECKLIST.md)

## Live OpenAI verification
One controlled live request was made with the synthetic fixture `data/samples/records_for_summary.json` and an isolated temporary SQLite database; no mock, stub or proxy; the application's own code path, one provider attempt, no retries.
- Real OpenAI API, HTTP 200; model `gpt-4o-mini-2024-07-18` (configured `gpt-4o-mini`); `finish_reason: stop`; 975 total tokens reported by the SDK.
- Passed: structured schema; 46 words (limit 200); evidence grounding (cited record ids exist, evidence quotes appear verbatim in the cited records, diagnosis names appear in the cited records); clinical disclaimer present; the summary was stored and a later `GET` returned it unchanged.
- A second `POST /patients/{id}/summary` returned `cache_hit: true` and made no second provider call (call counter stayed at 1).
- A live summary for a second synthetic patient was then indexed (`POST /patients/{id}/index`) and displayed in the React UI (checked in real Chrome): the result card carries the AI-generated badge, and the detail drawer shows the AI-generated banner, source quotes, source record ids, model id and the clinical disclaimer. The UI itself never calls OpenAI.
- **What this does not show:** no clinician reviewed the output, and grounding checks do not establish clinical correctness. One request does not establish model reliability. Synthetic data only. Authentication and authorization are not implemented.
- Reproduce (billable, needs your own funded key in `backend/.env`; the normal tests never call OpenAI): ingest the fixture, `POST /patients/{id}/fhir`, then `POST /patients/{id}/summary` once.

## Limitations and future work

- UI: smoke-tested once in real Chrome on macOS with a throwaway script (see `docs/TESTING_STRATEGY.md`), but no automated browser/visual/accessibility tests, other browsers untested; the patient filter takes an opaque patient id (there is no patient listing endpoint, by design); search requires records to be ingested, FHIR-generated and indexed first.
- No authentication or authorization anywhere (UI included); `GET /patients/{id}/display` returns a name and MRN to any caller and is not an access control.
- Search: semantic mode needs the `embeddings` extra and a one-time model download (otherwise 503, no silent fallback); the lexical fallback matches shared
  words only; indexing is an explicit step, not automatic; exact scan measured only to 20,000 entries; long texts are truncated by the model (~256 word
  pieces, no chunking); only DocumentReference text and AI summaries are indexed; the quality evaluation is small, synthetic and author-written.
- **Security: no authentication or authorization (see the banner and [docs/SECURITY.md](docs/SECURITY.md)).** Any caller can search across patients by
  omitting `patient_id`. Search results omit names/MRNs, but other endpoints do not (the FHIR bundle contains full Patient resources). Demo only.
- AI summaries, safety: **AI-generated, not verified.** A summary must be reviewed by a qualified clinician before any use. It must never be
  presented as a verified diagnosis or as medical advice; the response carries a disclaimer to that effect.
- **Evidence quotes show textual traceability, not clinical correctness.** A correctly cited, verbatim quote can still sit
  under a distorted or misleading paraphrase (`text`, `summary_text`); omissions are not detected.
- **English-only heuristics.** Hedging/history/negation/advice detection uses English marker lists with known coverage gaps
  (missed phrasings, occasional over-rejection).
- **Live OpenAI verification is one successful request only.** It establishes that the integration works once, not general model reliability, output quality or rejection rates. An earlier live attempt had failed at
  the provider (exact status not captured; a billing/quota 429 is the likely cause); afterwards `insufficient_quota` was made a permanent, non-retried error (D43). Details in "Live OpenAI verification" below.
- **Clinical correctness has not been independently validated.** Automated grounding checks establish textual traceability (cited ids exist, quotes appear verbatim in the cited records), **not** clinical correctness. No clinician has reviewed any generated summary, including the live one.
- The quality indicator is an evidence-coverage heuristic, not a probability or calibrated model confidence.
- AI summaries, other: run against the live OpenAI API once only (see above); automated checks reduce but do not eliminate unsupported output;
  prose in `summary_text` is only partly verifiable; retrieval is recency-based, not semantic; requires human clinical review
  before any use; sending real clinical text to a provider needs appropriate agreements (this project uses synthetic data only).
- FHIR: not a complete FHIR validator (no FHIRPath invariants or terminology); no coded content (the source has none);
  names are free text; bundles use `urn:uuid` references; generation is on demand per patient.
- **Deviations from the original brief (deliberate, documented):** (1) the vector index is a SQLite store behind a `VectorStore` port, not ChromaDB (D32); (2) `GET /patients` and `GET /patients/{id}` were not
  implemented: the owner ruled out a listing endpoint, and the UI uses `GET /patients/{id}/display` instead; (3) the search request field is `limit` (max 5), not `top_k`; (4) FHIR generation, summaries and indexing
  are explicit per-patient calls, not automatic after ingestion; (5) normalized records are stored as canonical JSON on `source_records`, not in a separate table.
- No resolution workflow for `needs_review` records; no listing/read endpoints for patients, records or the audit log (FHIR endpoints exist).
- A changed source record is stored as a new record (no supersede logic); existing patient demographics are never updated.
- Identical raw records repeated inside one file are reported as already ingested.
- Tables are created at startup (`create_all`); there is no migration tool.
- No authentication or authorization: run on localhost only, with synthetic data only. Not HIPAA-assessed.

## Troubleshooting

- `pip install -e "./backend[dev]"` fails with "Multiple top-level packages discovered": use the repository
  version of `backend/pyproject.toml` (it restricts packaging to `app*`).
- `pytest: command not found`: activate the virtual environment (`source .venv/bin/activate`) or use
  `python -m pytest`.
- Start `uvicorn` from `backend/`: a relative `EHR_DATABASE_URL` (the `.env.example` default) is resolved against the current directory, so starting elsewhere creates the database elsewhere.
- Port 8000 already in use: start the backend on another port (`uvicorn app.main:app --port 8001`) and run the frontend with `API_PROXY_TARGET=http://localhost:8001 npm run dev`.
- Frontend says "Backend unreachable": start the backend on port 8000 first.
- Search returns no results: documents are searchable only after `POST /patients/{id}/fhir` and then `POST /search/reindex` (or `/patients/{id}/index`).
- `503 embedding_provider_unavailable` from search: install the extra (`pip install -e "./backend[embeddings]"`), run `python backend/scripts/init_embedding_model.py`
  (the one-time model download needs to reach the Hugging Face Hub), or explicitly choose the lexical fallback with `EHR_EMBEDDING_PROVIDER=hashing`.
- `503 ai_provider_not_configured`: set `EHR_OPENAI_API_KEY` in `backend/.env` and restart the backend.
- `502 summary_generation_failed` with `invalid_output`: the model's output failed the grounding checks on every attempt (nothing is stored or served); retry, or inspect the model/prompt. With `provider_rejected`, check the key, model name and account access.
- `npm test` fails on syntax/engine errors: check `node --version` (needs 22.12+).
- `413`/`415`/`422` from `/ingest`: see the status table above; the `message` field says what to fix.
- Delete `backend/data/ehr.db` to reset local data (it is git-ignored).
