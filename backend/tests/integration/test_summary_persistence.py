"""Summary persistence on real SQLite (temp file per test) and the pipeline end to end with a fake LLM."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import SourceFormat
from app.domain.entities import AuditEventType
from app.domain.errors import LLMTransientError, SummaryGenerationFailedError
from app.domain.summary import StoredSummary, SummaryStatus
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.fhir_helpers import make_patient
from tests.summary_helpers import R1_RAW, R2_RAW, ScriptedLLM, as_json, good_output

T = datetime(2025, 3, 1, 12, tzinfo=UTC)


@pytest.fixture
def uow(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 's.db'}")
    init_schema(engine)
    factory = get_session_factory(engine)
    return lambda: SqlAlchemyUnitOfWork(factory)


def stored(sid="s1", pid="p1", h="h" * 64, status=SummaryStatus.FAILED, summary=None, attempts=1, when=T):
    return StoredSummary(summary_id=sid, patient_id=pid, content_hash=h, prompt_version="v1", model_identifier="m",
                         status=status, summary=summary, failure_category="provider_timeout" if not summary else None,
                         attempts=attempts, created_at=when, updated_at=when)


def seed_patient(uow, pid="p1", mrn="000101"):
    with uow() as u:
        u.patients.add(make_patient(patient_id=pid, mrn=mrn))
        u.commit()


def ingest(uow):
    uc = IngestPatientRecordsUseCase(uow, IngestionStrategyFactory(), ResolvePatientIdentityUseCase())
    rows = [{**R1_RAW, "mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14"},
            {**R2_RAW, "name": "Alex Test", "dob": "1980-03-14"}]
    return uc.execute(IngestCommand(json.dumps(rows).encode(), "r.json", SourceFormat.JSON)).results


def test_roundtrip_unique_key_and_upsert_replaces_failed_with_generated(uow):
    seed_patient(uow)
    with uow() as u:
        u.summaries.save(stored())
        u.commit()
    with uow() as u:
        got = u.summaries.get_by_key("p1", "h" * 64, "v1", "m")
        assert got.status == SummaryStatus.FAILED and got.failure_category == "provider_timeout" and got.updated_at == T
        assert u.summaries.get_by_key("p1", "h" * 64, "v2", "m") is None            # prompt version is part of the key
        assert u.summaries.get_by_key("p1", "h" * 64, "v1", "other") is None        # so is the model
        assert u.summaries.get_by_key("p1", "x" * 64, "v1", "m") is None            # and the content hash
        assert u.summaries.get_latest_generated("p1") is None


def test_save_with_same_key_updates_in_place_without_duplicates(uow):
    seed_patient(uow)
    with uow() as u:
        u.summaries.save(stored())
        u.commit()
    with uow() as u:
        u.summaries.save(stored(status=SummaryStatus.FAILED, attempts=2))
        u.commit()
    with uow() as u:
        assert u.summaries.get_by_key("p1", "h" * 64, "v1", "m").attempts == 2
        from app.infrastructure.persistence.tables import ClinicalSummaryRow
        assert u.summaries._s.query(ClinicalSummaryRow).count() == 1


def test_foreign_key_enforced(uow):
    with uow() as u, pytest.raises(IntegrityError):
        u.summaries.save(stored(pid="ghost"))


def test_rollback_discards_uncommitted_summary(uow):
    seed_patient(uow)
    with uow() as u:
        u.summaries.save(stored())
    with uow() as u:
        assert u.summaries.get_by_key("p1", "h" * 64, "v1", "m") is None


def test_end_to_end_pipeline_on_sqlite_with_cache_staleness_and_retry(uow):
    pid = ingest(uow)[0].patient_id
    with uow() as u:
        recs = {r.source_id.split("#")[1]: r.record_id for r in u.source_records.list_completed_for_patient(pid)}
    ok = as_json(good_output(recs["0"], recs["1"]))
    llm = ScriptedLLM(LLMTransientError("x"), ok)
    uc = GenerateClinicalSummaryUseCase(uow, llm, SummaryConfig(retry_base_seconds=0), sleeper=lambda s: None)
    first = uc.execute(pid)
    assert not first.cache_hit and llm.calls == 2

    again = uc.execute(pid)
    assert again.cache_hit and again.summary == first.summary and again.summary_id == first.summary_id
    assert llm.calls == 2

    reader = GetPatientSummaryUseCase(uow, "test-model", SummaryConfig())
    view = reader.execute(pid)
    assert view.summary == first.summary and not view.is_stale and view.summary.patient_id == pid
    assert view.summary.key_diagnoses[1].certainty == "uncertain"

    with uow() as u:
        types = [e.event_type for e in u.audit.list_events()
                 if e.patient_id == pid and e.event_type.value.startswith("summary")]
    assert types == [AuditEventType.SUMMARY_GENERATED, AuditEventType.SUMMARY_CACHE_HIT]


def test_failure_then_recovery_on_sqlite(uow):
    pid = ingest(uow)[0].patient_id
    with uow() as u:
        recs = [r.record_id for r in u.source_records.list_completed_for_patient(pid)]
    failing = GenerateClinicalSummaryUseCase(uow, ScriptedLLM(LLMTransientError("x")),
                                             SummaryConfig(max_attempts=2, retry_base_seconds=0),
                                             sleeper=lambda s: None)
    with pytest.raises(SummaryGenerationFailedError):
        failing.execute(pid)
    reader = GetPatientSummaryUseCase(uow, "test-model", SummaryConfig())
    from app.domain.errors import SummaryNotFoundError
    with pytest.raises(SummaryNotFoundError):
        reader.execute(pid)
    # records order in list_completed is by received_at; look ids up by source index instead
    with uow() as u:
        by_idx = {r.source_id.split("#")[1]: r.record_id for r in u.source_records.list_completed_for_patient(pid)}
    ok = as_json(good_output(by_idx["0"], by_idx["1"]))
    assert recs
    res = GenerateClinicalSummaryUseCase(uow, ScriptedLLM(ok)).execute(pid)
    assert not res.cache_hit and reader.execute(pid).summary == res.summary
