# Database schema

SQLite (via SQLAlchemy 2), created at startup with `create_all` (no migration tool yet). Foreign keys are enforced
(`PRAGMA foreign_keys=ON`). Source of truth: every table except `search_index_entries`, which is a derived index that can be
dropped and rebuilt (`POST /search/reindex`). Definitions: `backend/app/infrastructure/persistence/tables.py`.

```
patients 1---* source_records (patient_id, nullable until identity is resolved)
patients 1---* fhir_resources, 1---1 patient_fhir_state, 1---* clinical_summaries, 1---* search_index_entries
source_records 1---* fhir_resources (source_record_id) ; source_records *---1 source_records (duplicate_of_id)
audit_events -> source_records (record_id, nullable), patients (patient_id, nullable)
```

| Table | Key | Constraints / indexes | Purpose |
|---|---|---|---|
| `patients` | `patient_id` | unique `mrn`; index `(name_key, date_of_birth)` | Resolved patients (normalized MRN, name, DOB, gender) |
| `source_records` | `record_id` (deterministic) | unique `source_hash` (idempotency); indexes on `source_id`, `content_hash`, `status`, `patient_id`; FK `patient_id`, self-FK `duplicate_of_id` | Raw payload (stored once), canonical JSON, ingestion status/attempts/failure category |
| `audit_events` | `id` (autoincrement) | indexes on `occurred_at`, `event_type`, `record_id`; FKs to `source_records`, `patients` | Append-only audit trail: codes, IDs and counts only, never clinical content |
| `fhir_resources` | `(resource_type, resource_id)` | index `(patient_id, resource_type)`; FKs to `patients`, `source_records` | Generated FHIR R4 resource JSON, replaced wholesale per patient |
| `patient_fhir_state` | `patient_id` | index on `status`; FK `patients` | FHIR processing state (`generated`/`invalid`/`failed`), fingerprint, bundle id/timestamp, issues; separate from ingestion status |
| `clinical_summaries` | `summary_id` | **unique `(patient_id, content_hash, prompt_version, model_identifier)`** (the cache key); indexes on `patient_id`, `updated_at`; FK `patients` | AI summaries and their status (`generated`/`failed`), attempts, failure category |
| `search_index_entries` | `doc_id` (`<type>:<resource id>`) | index `(patient_id, resource_type, record_date)`; indexes on `patient_id`, `model_identifier`; FK `patients` | **Derived** semantic index: text, metadata, float32 embedding BLOB, content hash, model |

Notes
- `search_index_entries.content_hash` covers model id, resource type, record date and text, so any change (or a model change)
  triggers re-embedding; `doc_id` is unique, so re-indexing replaces rather than duplicates. Entries are never trusted on their
  own: search re-validates each hit against `fhir_resources` / `clinical_summaries` / `patients`.
- Clinical text exists in several places by design: `source_records.raw_payload` (replay), `source_records.canonical`
  (normalized), `fhir_resources` (base64 attachment), `search_index_entries.text` (derived, for snippets). The index copy is
  rebuildable; deleting it loses nothing.
- Timestamps are stored as naive UTC. Local database files (`*.db`) are git-ignored.
