"""In-memory fakes of the repository ports and unit of work, with fault injection."""

from __future__ import annotations

import copy
from datetime import date

from app.domain.canonical import ProcessingStatus
from app.domain.entities import AuditEvent, Patient, StoredSourceRecord
from app.domain.fhir import FhirResourceRecord, PatientFhirState


class FakeStore:
    def __init__(self) -> None:
        self.patients: dict[str, Patient] = {}
        self.records: dict[str, StoredSourceRecord] = {}
        self.audit: list[AuditEvent] = []
        self.fhir_state: dict[str, PatientFhirState] = {}
        self.fhir_resources: dict[tuple[str, str], FhirResourceRecord] = {}
        self.fail_on: dict[str, int] = {}  # operation name -> remaining failures

    def maybe_fail(self, op: str) -> None:
        if self.fail_on.get(op, 0) > 0:
            self.fail_on[op] -= 1
            raise RuntimeError(f"injected failure in {op}")

    def snapshot(self):
        return copy.deepcopy((self.patients, self.records, self.audit, self.fhir_state, self.fhir_resources))

    def restore(self, snap) -> None:
        self.patients, self.records, self.audit, self.fhir_state, self.fhir_resources = snap


class FakePatients:
    def __init__(self, s: FakeStore): self.s = s
    def get(self, patient_id): return self.s.patients.get(patient_id)
    def get_by_mrn(self, mrn): return next((p for p in self.s.patients.values() if p.mrn == mrn), None)
    def find_by_name_key_and_dob(self, name_key: str, dob: date):
        return [p for p in self.s.patients.values() if p.name_key == name_key and p.date_of_birth == dob]
    def add(self, patient):
        self.s.maybe_fail("patients.add")
        if self.get_by_mrn(patient.mrn):
            raise ValueError("unique violation")
        self.s.patients[patient.patient_id] = patient
    def list_all(self): return list(self.s.patients.values())


class FakeSourceRecords:
    def __init__(self, s: FakeStore): self.s = s
    def get(self, record_id): return self.s.records.get(record_id)
    def get_by_source_hash(self, h): return next((r for r in self.s.records.values() if r.source_hash == h), None)
    def find_original_by_content_hash(self, content_hash, exclude_record_id):
        c = [r for r in self.s.records.values()
             if r.content_hash == content_hash and r.record_id != exclude_record_id
             and r.processing.status in (ProcessingStatus.COMPLETED, ProcessingStatus.NEEDS_REVIEW)]
        return min(c, key=lambda r: r.received_at, default=None)
    def list_completed_for_patient(self, patient_id):
        c = [r for r in self.s.records.values()
             if r.patient_id == patient_id and r.processing.status == ProcessingStatus.COMPLETED]
        return sorted(c, key=lambda r: (r.received_at, r.record_id))
    def add(self, record): self.s.records[record.record_id] = record
    def update(self, record):
        self.s.maybe_fail("source_records.update")
        self.s.records[record.record_id] = record


class FakeAudit:
    def __init__(self, s: FakeStore): self.s = s
    def add(self, event):
        self.s.maybe_fail("audit.add")
        self.s.audit.append(event.model_copy(update={"id": len(self.s.audit) + 1}))
    def list_events(self, record_id=None):
        return [e for e in self.s.audit if record_id is None or e.record_id == record_id]


class FakeFhir:
    def __init__(self, s: FakeStore): self.s = s
    def get_state(self, patient_id): return self.s.fhir_state.get(patient_id)
    def save_state(self, state):
        self.s.maybe_fail("fhir.save_state")
        self.s.fhir_state[state.patient_id] = state
    def replace_resources(self, patient_id, resources):
        self.s.maybe_fail("fhir.replace_resources")
        self.s.fhir_resources = {k: v for k, v in self.s.fhir_resources.items() if v.patient_id != patient_id}
        for r in resources:
            self.s.fhir_resources[(r.resource_type, r.resource_id)] = r
    def list_resources(self, patient_id, resource_type=None):
        rs = [r for r in self.s.fhir_resources.values()
              if r.patient_id == patient_id and (resource_type is None or r.resource_type == resource_type)]
        return sorted(rs, key=lambda r: r.position)
    def get_resource(self, resource_type, resource_id): return self.s.fhir_resources.get((resource_type, resource_id))


class FakeUnitOfWork:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self._committed = False

    def __enter__(self):
        self._snap = self.store.snapshot()
        self.patients, self.source_records, self.audit, self.fhir = (
            FakePatients(self.store), FakeSourceRecords(self.store), FakeAudit(self.store), FakeFhir(self.store))
        return self

    def __exit__(self, *exc):
        if not self._committed:
            self.store.restore(self._snap)  # rollback

    def commit(self) -> None:
        self._committed = True
