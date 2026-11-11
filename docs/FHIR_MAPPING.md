# FHIR R4 mapping

## Version and package compatibility (verified 2026-11-11)

- Package: `fhir.resources` 8.3.0 (with `fhir-core` 1.1.12, Pydantic v2).
- **The package's default namespace is FHIR 5.0.0 (R5).** `fhir.resources.R4B` is FHIR **4.3.0**. Importing
  `fhir.resources.patient` would silently produce R5 resources, so this project imports **only**
  `fhir.resources.R4B.*`, and only inside `backend/app/infrastructure/fhir/`. `tests/test_architecture.py`
  fails if any other module imports `fhir.*` or if a non-R4B namespace is imported.
- **R4B vs R4 (4.0.1), checked rather than assumed.** I installed `fhir.resources` 6.4.0 (true R4 4.0.1) in a
  throwaway environment (it requires Pydantic v1, so it cannot be used in this Pydantic v2 project) and compared
  element names and required elements of `Patient`, `DocumentReference` (+ `content`, `context`),
  `DiagnosticReport`, `Bundle` (+ `entry`, `entry.request`) against R4B 4.3.0: **identical** (the only difference
  was a Pydantic-v1 internal field). R4B (a technical correction of R4) differs from R4 elsewhere (e.g.
  medicinal-product resources), not in the resources used here. Consequence: output for these four resource
  types is R4-shaped; I populate only elements present in both.
- **What the library does and does not validate** (probed, not assumed):
  - Enforced: types, cardinality of required elements (in two stages: e.g. a missing `code` is reported first,
    and a missing `status`/`Bundle.type` appears only after that passes), unknown fields, `id` pattern, date and
    base64 formats.
  - **Not enforced**: value-set bindings (`Patient.gender="banana"` and `DiagnosticReport.status="nonsense"` are
    accepted) and reference resolution (a dangling reference is accepted). No FHIRPath invariants, no terminology.
  - Therefore validation has three layers: (1) the library (`R4BSchemaValidator`), (2) our value-set checks for
    the code fields we populate, (3) our reference-integrity and attachment checks
    (`app/domain/fhir.py::check_bundle`). This is **not a complete FHIR validator** (no HAPI/official validator).

## Mapping (canonical -> FHIR)

Code: `app/infrastructure/fhir/r4b_builder.py` (mapping), `app/domain/fhir.py` (IDs, fingerprint, rules).

### Patient (one per resolved patient)
| FHIR element | Source | Notes |
|---|---|---|
| `id` | `patient_id` | Stable UUID |
| `identifier` | normalized MRN | `use=usual`, type `MR` (v2-0203), system `urn:ehr-media-intelligence:mrn` (a placeholder namespace: the source states no assigning authority) |
| `name[0].text` | full name | Text only. No given/family split (would be a guess). Omitted if unknown. |
| `gender` | canonical gender | Values are FHIR administrative-gender codes; `unknown` is valid |
| `birthDate` | date of birth | Omitted if unknown |

### DocumentReference (one per COMPLETED record that has document text)
| Element | Source | Notes |
|---|---|---|
| `id` | `record_id` | Deterministic |
| `identifier` | `record_id` | system `urn:ehr-media-intelligence:source-record` |
| `status` | constant `current` | The record is the current document in this system |
| `subject` | Patient | `urn:uuid:<patient id>`, `type=Patient` |
| `date` | `received_at` | R4 meaning: when the reference was created (ingestion time), **not** the clinical date |
| `context.period.start` | document date | The clinical/visit date; omitted if unknown |
| `context.related` | DiagnosticReport | Present when the same record states diagnoses |
| `content[0].attachment` | document text | `text/plain`, base64 `data`, `size`, SHA-1 `hash` (as FHIR requires) |
| `type`, `category`, `docStatus`, `author`, `description` | not set | Not stated in the source; no codes (e.g. LOINC) are invented |

### DiagnosticReport (only when the source record lists diagnoses)
| Element | Source | Notes |
|---|---|---|
| `id` | UUIDv5 of `record_id` | Deterministic |
| `status` | constant `unknown` | The source states no report status |
| `code.text` | fixed label "Diagnoses documented in source clinical record" | Uncoded; this is a container for stated diagnoses, not a coded test |
| `conclusion` / `conclusionCode[].text` | diagnoses, verbatim | Joined with `; ` / one entry each. No coding, no normalization |
| `effectiveDateTime` | document date | Omitted if unknown |
| `subject` | Patient | |
Mapping limitation: a list of stated diagnoses is not really a diagnostic-investigation report; it is mapped here
because the assessment requires DiagnosticReport and to keep diagnoses attributed to their source record.

### Bundle (one per patient)
`type=collection`; `id` = UUIDv5 of the patient ID; `timestamp` = generation time (changes only when
regenerated). Entries ordered Patient, DocumentReference, DiagnosticReport. `fullUrl` and every `reference` use
`urn:uuid:<resource id>` (no server base URL exists), so every reference resolves inside the bundle.
Which records: only `completed` ones. `duplicate`, `needs_review`, `rejected`, `failed` are never included.

## Processing and idempotency
- FHIR state is stored per patient in `patient_fhir_state` (`generated` | `invalid` | `failed`; no row = never
  generated), **separate** from `source_records.status` (ingestion).
- Fingerprint = hash of mapper version + patient fields + (record_id, content_hash) of completed records.
  Same fingerprint and state `generated` -> generation is skipped (`unchanged`). Changing mapping rules means
  bumping `MAPPER_VERSION`, which forces regeneration.
- Resources are stored per (type, id) in `fhir_resources`; regeneration replaces the patient's rows in one
  transaction (no duplicates). The Bundle is re-assembled from stored resources on read (same deterministic
  assembler), using the stored bundle id and timestamp.
- `invalid` (validation failed) or `failed` (unexpected error) never serves a bundle: `GET .../fhir` returns 409.
  The previous resources are not deleted but are not served until a successful regeneration.

## Endpoints
`POST /patients/{id}/fhir` (generate, idempotent), `GET /patients/{id}/fhir` (Bundle, `application/fhir+json`),
`GET /patients/{id}/fhir/status`. Errors: 404 `patient_not_found`, 409 `fhir_not_available`,
422 `fhir_validation_failed` (details: issue path + rule code), 500 `fhir_generation_failed`.
