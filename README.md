# EHR Media Intelligence Platform

A technical-assessment project that ingests heterogeneous healthcare records, normalizes them, resolves
patient identity conservatively, transforms them to FHIR R4, and (planned) summarizes them with grounded AI
and exposes semantic search in a clinician UI. **Synthetic data only. Not a medical device. No HIPAA
compliance claim has been made or assessed.**

## Implementation status

| Assessment task | Status |
|---|---|
| Foundation (API skeleton, logging, config, tests) | **Implemented** |
| Task 1: Data ingestion and cleaning (JSON/CSV/text) | **Implemented** |
| Task 2: FHIR R4 transformation and validation | **Implemented** (Patient, DocumentReference, DiagnosticReport, Bundle) |
| Task 3: AI clinical summaries (OpenAI) | Not implemented |
| Task 4: Semantic search | Not implemented |
| Task 5: Clinician interface | Not implemented (a placeholder page only checks `/health`) |

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
- `frontend/`: React + Vite + Tailwind placeholder.

Ingestion data flow: upload -> strategy parses structure -> store raw record (`received`) -> normalize ->
duplicate check -> identity decision -> patient/status/audit written in one transaction -> response.
States: `received -> completed | rejected | duplicate | needs_review | failed` (`received`/`failed` are retried
when the same content is submitted again).

FHIR flow: patient + `completed` records -> fingerprint (skip if unchanged) -> deterministic mapping with
`fhir.resources.R4B` -> Bundle -> value-set / reference / attachment checks + library validation -> resources
replaced atomically, state `generated` (`invalid`/`failed` serve nothing). See [docs/FHIR_MAPPING.md](docs/FHIR_MAPPING.md).

**Planned (not built):** grounded RAG summaries behind an
`LLMProvider` port (OpenAI), embeddings (`all-MiniLM-L6-v2`) and ChromaDB as a rebuildable search index with
SQLite as source of truth, `POST /search`, and the clinician UI. Design rationale is in
[docs/DECISIONS.md](docs/DECISIONS.md); those decisions are marked there as not yet implemented.

## Technology stack (actual dependencies)

| Package | Role |
|---|---|
| FastAPI, Uvicorn | HTTP API and server |
| Pydantic v2, pydantic-settings | Models, validation, environment configuration |
| SQLAlchemy 2 + SQLite | Persistence |
| python-multipart | File upload parsing |
| fhir.resources (R4B namespace, FHIR 4.3.0) | FHIR R4 resource models and structural validation |
| pytest, httpx, Ruff (dev) | Tests, FastAPI test client, lint |
| React, Vite, TypeScript, Tailwind CSS, Vitest (frontend) | UI shell and tests |

## Prerequisites

- Python 3.11 or newer (verified on 3.12.14 and 3.14.7).
- Node.js 22.12 or newer (verified on 24.19.0) and npm (verified 11.17.0). Vitest 5 requires Node 22.12+.
- Git.

## Installation and setup

macOS / Linux:

```bash
git clone <repository-url> ehr-media-intelligence-platform
cd ehr-media-intelligence-platform

python3 -m venv .venv
source .venv/bin/activate
pip install -e "./backend[dev]"
cp backend/.env.example backend/.env

cd frontend && npm ci && cd ..
```

Windows (PowerShell). These commands are the equivalents but **were not tested on Windows**:

```powershell
git clone <repository-url> ehr-media-intelligence-platform
cd ehr-media-intelligence-platform
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".\backend[dev]"
copy backend\.env.example backend\.env
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
| `EHR_OPENAI_API_KEY`, `EHR_OPENAI_MODEL` | empty / `gpt-4o-mini` | Reserved for the planned AI summaries; unused today |

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
# http://localhost:5173 (proxies /api/* to the backend; shows whether the backend is reachable)
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

Errors share one envelope: `{"error": {"code", "message", "request_id", "details"?}}`. There are no
`/patients` listing, `/search` or summary endpoints yet.

## Sample data

[data/samples/](data/samples/) holds fabricated records with deliberate defects:
- `records.json`: a clean record; a reformatted duplicate of it; a record with no date of birth; a patient with the
  same name but a different DOB and MRN (stays a separate patient).
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
Current result: 185 backend tests, 2 frontend tests, lint clean. No network or API key is needed.
Strategy and coverage map: [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md).

## Design decisions

- Architecture and flows: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Decision records with alternatives and tradeoffs: [docs/DECISIONS.md](docs/DECISIONS.md)
- Testing: [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md)
- FHIR mapping, version compatibility evidence and validation limits: [docs/FHIR_MAPPING.md](docs/FHIR_MAPPING.md)

## Limitations and future work

- Tasks 3-5 are not implemented (see status table).
- FHIR: not a complete FHIR validator (no FHIRPath invariants or terminology); no coded content (the source has none);
  names are free text; bundles use `urn:uuid` references; generation is on demand per patient.
- No resolution workflow for `needs_review` records; no listing/read endpoints for patients, records or the audit log (FHIR endpoints exist).
- A changed source record is stored as a new record (no supersede logic); existing patient demographics are never updated.
- Identical raw records repeated inside one file are reported as already ingested.
- Tables are created at startup (`create_all`); there is no migration tool.
- No authentication or authorization: run on localhost only. Not HIPAA-assessed.

## Troubleshooting

- `pip install -e "./backend[dev]"` fails with "Multiple top-level packages discovered": use the repository
  version of `backend/pyproject.toml` (it restricts packaging to `app*`).
- `pytest: command not found`: activate the virtual environment (`source .venv/bin/activate`) or use
  `python -m pytest`.
- Start `uvicorn` from `backend/`: a relative `EHR_DATABASE_URL` (the `.env.example` default) is resolved against the current directory, so starting elsewhere creates the database elsewhere.
- Port 8000 already in use: start the backend on another port (`uvicorn app.main:app --port 8001`) and run the frontend with `API_PROXY_TARGET=http://localhost:8001 npm run dev`.
- Frontend says "Backend unreachable": start the backend on port 8000 first.
- `npm test` fails on syntax/engine errors: check `node --version` (needs 22.12+).
- `413`/`415`/`422` from `/ingest`: see the status table above; the `message` field says what to fix.
- Delete `backend/data/ehr.db` to reset local data (it is git-ignored).
