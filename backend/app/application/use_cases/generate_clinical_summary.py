"""GenerateClinicalSummaryUseCase: the grounded RAG summary pipeline.

  load patient-scoped records -> build context (isolation re-checked) -> cache lookup
  -> [no DB transaction held] provider call with retries -> parse -> grounding checks -> assemble
  -> persist (generated) + audit.  Failure after retries -> persist (failed, retryable) + audit.

Retries: transient provider errors back off exponentially; invalid model output is simply re-asked; permanent
provider errors stop at once. All share one attempt budget (`max_attempts`).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.summary.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from app.domain.entities import AuditEvent, AuditEventType
from app.domain.errors import (
    LLMNotConfiguredError,
    LLMPermanentError,
    LLMProviderError,
    LLMTimeoutError,
    LLMTransientError,
    NoSourceRecordsError,
    PatientNotFoundError,
    SummaryGenerationFailedError,
    SummaryOutputError,
)
from app.domain.ports.llm import LLMProvider, LLMRequest
from app.domain.ports.unit_of_work import UnitOfWork
from app.domain.summary import (
    ClinicalSummary,
    StoredSummary,
    SummaryContext,
    SummaryStatus,
    assemble_summary,
    build_summary_context,
    parse_model_output,
    verify_grounding,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryConfig:
    max_attempts: int = 3
    retry_base_seconds: float = 1.0
    max_records: int = 20
    max_chars_per_record: int = 4000
    max_output_tokens: int = 1200
    temperature: float | None = 0.0


@dataclass(frozen=True)
class SummaryResult:
    summary: ClinicalSummary
    summary_id: str
    cache_hit: bool


class GenerateClinicalSummaryUseCase:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        llm: LLMProvider,
        config: SummaryConfig = SummaryConfig(),  # noqa: B008 - frozen dataclass
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._config = config
        self._clock = clock
        self._sleep = sleeper
        self._new_id = new_id

    def execute(self, patient_id: str) -> SummaryResult:
        # 1. read + context + cache lookup (short transaction; no provider call inside it)
        with self._uow_factory() as uow:
            patient = uow.patients.get(patient_id)
            if patient is None:
                raise PatientNotFoundError("Patient not found.")
            context = build_summary_context(
                patient, uow.source_records.list_completed_for_patient(patient_id),
                max_records=self._config.max_records, max_chars=self._config.max_chars_per_record)
            if not context.documents:
                raise NoSourceRecordsError("The patient has no completed records with text or diagnoses.")
            model = self._llm.model_identifier
            cached = uow.summaries.get_by_key(patient_id, context.content_hash, PROMPT_VERSION, model)
            if cached is not None and cached.status == SummaryStatus.GENERATED and cached.summary is not None:
                self._audit(uow, patient_id, AuditEventType.SUMMARY_CACHE_HIT,
                            {"summary_id": cached.summary_id, "prompt_version": PROMPT_VERSION})
                uow.commit()
                logger.info("summary_cache_hit", extra={"patient_id": patient_id})
                return SummaryResult(cached.summary, cached.summary_id, cache_hit=True)
            previous_attempts = cached.attempts if cached else 0
        logger.info("summary_cache_miss", extra={"patient_id": patient_id, "record_count": len(context.documents)})

        # 2. generate + validate (no DB transaction held while waiting on the provider)
        summary, attempts, failure = self._generate(context, model)
        total_attempts = previous_attempts + attempts

        # 3. persist outcome
        if summary is not None:
            return self._persist_success(patient_id, context, model, summary, total_attempts)
        self._persist_failure(patient_id, context, model, failure or "unknown", total_attempts)
        raise SummaryGenerationFailedError(failure or "unknown")

    # ------------------------------------------------------------------
    def _generate(self, context: SummaryContext, model: str) -> tuple[ClinicalSummary | None, int, str | None]:
        request = LLMRequest(system_prompt=SYSTEM_PROMPT, user_prompt=build_user_prompt(context),
                             max_output_tokens=self._config.max_output_tokens,
                             temperature=self._config.temperature)
        failure: str | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                response = self._llm.generate(request)
            except LLMNotConfiguredError:
                raise  # nothing was attempted; do not record a failed summary
            except LLMTransientError as exc:
                failure = exc.category if isinstance(exc, LLMTimeoutError) else "provider_unavailable"
                logger.warning("summary_provider_transient_error", extra={"attempt": attempt, "category": failure})
                if attempt < self._config.max_attempts:
                    self._sleep(self._config.retry_base_seconds * 2 ** (attempt - 1))
                continue
            except LLMPermanentError as exc:
                logger.error("summary_provider_permanent_error", extra={"category": exc.category})
                return None, attempt, exc.category
            except LLMProviderError as exc:  # unknown provider failure: do not retry blindly
                return None, attempt, exc.category
            try:
                if response.finish_reason == "length":
                    raise SummaryOutputError(["output_truncated"])
                output = parse_model_output(response.text)
                issues = verify_grounding(output, context)
                if issues:
                    raise SummaryOutputError(sorted({i.code for i in issues}))
                summary = assemble_summary(output, context, model_identifier=model,
                                           prompt_version=PROMPT_VERSION, generated_at=self._clock())
            except SummaryOutputError as exc:
                failure = "invalid_output"
                logger.warning("summary_output_rejected", extra={"attempt": attempt, "codes": exc.codes})
                continue
            return summary, attempt, None
        return None, self._config.max_attempts, failure

    def _persist_success(self, patient_id: str, context: SummaryContext, model: str, summary: ClinicalSummary,
                         attempts: int) -> SummaryResult:
        now = self._clock()
        try:
            with self._uow_factory() as uow:
                existing = uow.summaries.get_by_key(patient_id, context.content_hash, PROMPT_VERSION, model)
                stored = StoredSummary(
                    summary_id=existing.summary_id if existing else self._new_id(), patient_id=patient_id,
                    content_hash=context.content_hash, prompt_version=PROMPT_VERSION, model_identifier=model,
                    status=SummaryStatus.GENERATED, summary=summary, attempts=attempts,
                    created_at=existing.created_at if existing else now, updated_at=now)
                uow.summaries.save(stored)
                self._audit(uow, patient_id, AuditEventType.SUMMARY_GENERATED,
                            {"summary_id": stored.summary_id, "prompt_version": PROMPT_VERSION, "model": model,
                             "attempts": attempts, "record_count": len(context.documents)})
                uow.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("summary_persist_failed", extra={"failure_category": type(exc).__name__})
            self._persist_failure(patient_id, context, model, "persistence_error", attempts)
            raise SummaryGenerationFailedError("persistence_error") from None
        logger.info("summary_generated", extra={"patient_id": patient_id, "attempts": attempts})
        return SummaryResult(summary, stored.summary_id, cache_hit=False)

    def _persist_failure(self, patient_id: str, context: SummaryContext, model: str, reason: str,
                         attempts: int) -> None:
        now = self._clock()
        try:
            with self._uow_factory() as uow:
                existing = uow.summaries.get_by_key(patient_id, context.content_hash, PROMPT_VERSION, model)
                uow.summaries.save(StoredSummary(
                    summary_id=existing.summary_id if existing else self._new_id(), patient_id=patient_id,
                    content_hash=context.content_hash, prompt_version=PROMPT_VERSION, model_identifier=model,
                    status=SummaryStatus.FAILED, failure_category=reason, attempts=attempts,
                    created_at=existing.created_at if existing else now, updated_at=now))
                self._audit(uow, patient_id, AuditEventType.SUMMARY_FAILED,
                            {"reason": reason, "attempts": attempts, "prompt_version": PROMPT_VERSION, "model": model},
                            outcome="error")
                uow.commit()
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.error("summary_failure_not_persisted", extra={"patient_id": patient_id})

    def _audit(self, uow: UnitOfWork, patient_id: str, event_type: AuditEventType, details: dict,
               outcome: str = "ok") -> None:
        uow.audit.add(AuditEvent(occurred_at=self._clock(), event_type=event_type, patient_id=patient_id,
                                 outcome=outcome, details=details))
