"""Read side: the latest generated summary and whether it is stale."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.application.summary.prompt import PROMPT_VERSION
from app.application.use_cases.generate_clinical_summary import SummaryConfig
from app.domain.errors import PatientNotFoundError, SummaryNotFoundError
from app.domain.ports.unit_of_work import UnitOfWork
from app.domain.summary import ClinicalSummary, build_summary_context


@dataclass(frozen=True)
class SummaryView:
    summary: ClinicalSummary
    summary_id: str
    is_stale: bool
    stale_reasons: list[str] = field(default_factory=list)


class GetPatientSummaryUseCase:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], model_identifier: str,
                 config: SummaryConfig = SummaryConfig()) -> None:  # noqa: B008
        self._uow_factory = uow_factory
        self._model = model_identifier
        self._config = config

    def execute(self, patient_id: str) -> SummaryView:
        with self._uow_factory() as uow:
            patient = uow.patients.get(patient_id)
            if patient is None:
                raise PatientNotFoundError("Patient not found.")
            stored = uow.summaries.get_latest_generated(patient_id)
            if stored is None or stored.summary is None:
                raise SummaryNotFoundError("No summary has been generated for this patient.")
            context = build_summary_context(
                patient, uow.source_records.list_completed_for_patient(patient_id),
                max_records=self._config.max_records, max_chars=self._config.max_chars_per_record)
        reasons = []
        if stored.content_hash != context.content_hash:
            reasons.append("source_records_changed")
        if stored.prompt_version != PROMPT_VERSION:
            reasons.append("prompt_version_changed")
        if stored.model_identifier != self._model:
            reasons.append("model_changed")
        return SummaryView(stored.summary, stored.summary_id, bool(reasons), reasons)
