from __future__ import annotations

from app.domain.canonical import PatientInfo
from app.domain.identity import IdentityDecision, candidate_name_key, resolve_identity
from app.domain.ports.repositories import PatientRepository


class ResolvePatientIdentityUseCase:
    """Loads candidates through the repository, then applies the pure domain policy."""

    def execute(self, patients: PatientRepository, info: PatientInfo) -> IdentityDecision:
        mrn_match = patients.get_by_mrn(info.mrn) if info.mrn else None
        key = candidate_name_key(info)
        same_name_dob = (
            patients.find_by_name_key_and_dob(key, info.date_of_birth)
            if key and info.date_of_birth and mrn_match is None else []
        )
        return resolve_identity(info, mrn_match, same_name_dob)
