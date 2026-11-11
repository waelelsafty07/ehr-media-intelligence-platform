"""Conservative patient identity policy (pure, deterministic, no LLM).

Rules, in order:
 1. MRN already known:
      both DOBs present and different            -> CONFLICT   (never linked)
      names differ (exact normalized key) or
      both genders known and different           -> AMBIGUOUS  (manual review)
      otherwise                                  -> MATCHED
 2. MRN new:
      same name AND same DOB as another patient  -> AMBIGUOUS  (maybe one person, two MRNs)
      otherwise                                  -> NEW_PATIENT
    Name similarity alone never links, merges, or blocks. No fuzzy matching is performed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.canonical import Gender, PatientInfo
from app.domain.entities import Patient
from app.domain.normalization import name_key


class IdentityOutcome(StrEnum):
    MATCHED = "matched"
    NEW_PATIENT = "new_patient"
    CONFLICT = "conflict"
    AMBIGUOUS = "ambiguous"


class IdentityDecision(BaseModel):
    outcome: IdentityOutcome
    patient_id: str | None = None  # set only for MATCHED
    rationale_code: str
    rationale: str  # generic explanation; contains no patient data
    related_patient_ids: list[str] = Field(default_factory=list)


def candidate_name_key(info: PatientInfo) -> str | None:
    return name_key(info.full_name) if info.full_name else None


def resolve_identity(
    info: PatientInfo, mrn_match: Patient | None, same_name_and_dob: list[Patient]
) -> IdentityDecision:
    if not info.mrn:  # defensive: validation rejects these earlier
        return IdentityDecision(outcome=IdentityOutcome.AMBIGUOUS, rationale_code="no_reliable_identifier",
                                rationale="No usable MRN; identity cannot be established automatically.")
    key = candidate_name_key(info)

    if mrn_match is not None:
        rel = [mrn_match.patient_id]
        if info.date_of_birth and mrn_match.date_of_birth and info.date_of_birth != mrn_match.date_of_birth:
            return IdentityDecision(outcome=IdentityOutcome.CONFLICT, rationale_code="mrn_dob_mismatch",
                                    rationale="MRN matches an existing patient but date of birth differs.",
                                    related_patient_ids=rel)
        if key and mrn_match.name_key and key != mrn_match.name_key:
            return IdentityDecision(outcome=IdentityOutcome.AMBIGUOUS, rationale_code="mrn_match_name_differs",
                                    rationale="MRN matches an existing patient but the name differs.",
                                    related_patient_ids=rel)
        if (info.gender != Gender.UNKNOWN and mrn_match.gender != Gender.UNKNOWN
                and info.gender != mrn_match.gender):
            return IdentityDecision(outcome=IdentityOutcome.AMBIGUOUS, rationale_code="mrn_match_gender_differs",
                                    rationale="MRN matches an existing patient but gender differs.",
                                    related_patient_ids=rel)
        full = bool(info.date_of_birth and mrn_match.date_of_birth and key and mrn_match.name_key)
        return IdentityDecision(
            outcome=IdentityOutcome.MATCHED, patient_id=mrn_match.patient_id,
            rationale_code="mrn_dob_name_agree" if full else "mrn_match_partial_demographics",
            rationale=("MRN, date of birth and name agree." if full else
                       "MRN matches; some demographics were missing so they could not be compared."),
            related_patient_ids=rel)

    if same_name_and_dob:
        return IdentityDecision(
            outcome=IdentityOutcome.AMBIGUOUS, rationale_code="same_name_dob_different_mrn",
            rationale="New MRN, but name and date of birth match an existing patient. Not merged.",
            related_patient_ids=[p.patient_id for p in same_name_and_dob])
    return IdentityDecision(
        outcome=IdentityOutcome.NEW_PATIENT, rationale_code="new_mrn_no_conflicting_match",
        rationale="New MRN with no conflicting patient. Name-only similarity is not used for matching.")
