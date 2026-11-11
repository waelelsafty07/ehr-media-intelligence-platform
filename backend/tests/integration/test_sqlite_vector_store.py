"""SqliteVectorStore on a real temp SQLite database (and rebuild-from-source-of-truth)."""

import json
import math
from datetime import UTC, date, datetime

import numpy as np
import pytest

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_clinical_summary import SummaryConfig
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.application.use_cases.index_clinical_records import IndexClinicalRecordsUseCase
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.application.use_cases.search_clinical_records import SearchClinicalRecordsUseCase
from app.domain.canonical import SourceFormat
from app.domain.errors import VectorStoreError
from app.domain.search import IndexEntry, SearchFilters, SearchQuery
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)
from app.infrastructure.persistence.tables import SearchIndexRow
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.search.hashing_embedder import HashingEmbeddingProvider
from app.infrastructure.search.sqlite_vector_store import SqliteVectorStore
from tests.fhir_helpers import make_patient

T = datetime(2025, 3, 1, tzinfo=UTC)
MODEL = "m-test"


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'v.db'}")
    init_schema(engine)
    return get_session_factory(engine)


@pytest.fixture
def store(factory):
    with SqlAlchemyUnitOfWork(factory) as u:
        for pid, mrn in (("p1", "000101"), ("p2", "000102")):
            u.patients.add(make_patient(patient_id=pid, mrn=mrn))
        u.commit()
    return SqliteVectorStore(factory)


def entry(doc_id="DocumentReference:a", pid="p1", rtype="DocumentReference", vec=(1.0, 0.0, 0.0), rdate=date(2024, 1, 10),
          model=MODEL, text="text", chash="h1"):
    return IndexEntry(doc_id=doc_id, patient_id=pid, resource_type=rtype, resource_id=doc_id.split(":")[1],
                      source_record_id="rec", record_date=rdate, text=text, content_hash=chash, model_identifier=model,
                      embedding=list(vec), indexed_at=T)


F = SearchFilters


def ids(hits):
    return [h.doc_id for h in hits]


def test_roundtrip_upsert_replace_and_delete(store):
    store.upsert([entry("DocumentReference:a", text="first"), entry("ClinicalSummary:b", rtype="ClinicalSummary", chash="h2")])
    assert store.content_hashes("p1") == {"DocumentReference:a": "h1", "ClinicalSummary:b": "h2"} and store.content_hashes("p2") == {}
    store.upsert([entry("DocumentReference:a", text="second", chash="h9", vec=(0.0, 1.0, 0.0))])       # replace in place
    assert store.content_hashes("p1")["DocumentReference:a"] == "h9" and len(store.content_hashes("p1")) == 2
    (hit,) = store.query([0.0, 1.0, 0.0], model_identifier=MODEL, filters=F(resource_types=("DocumentReference",)), limit=5)
    assert hit.text == "second" and hit.record_date == date(2024, 1, 10) and hit.source_record_id == "rec" and hit.score == pytest.approx(1.0)
    assert store.delete(["DocumentReference:a", "missing"]) == 1 and store.delete([]) == 0
    assert list(store.content_hashes("p1")) == ["ClinicalSummary:b"]


def test_cosine_scores_are_exact_and_ranked_with_deterministic_ties(store):
    store.upsert([entry("DocumentReference:a", vec=(1, 0, 0)), entry("DocumentReference:b", vec=(1, 1, 0)),
                  entry("DocumentReference:c", vec=(0, 0, 1)), entry("DocumentReference:d", vec=(1, 1, 0)),
                  entry("DocumentReference:e", vec=(-1, 0, 0))])
    hits = store.query([1.0, 0.0, 0.0], model_identifier=MODEL, filters=F(), limit=10)
    assert ids(hits) == ["DocumentReference:a", "DocumentReference:b", "DocumentReference:d", "DocumentReference:c", "DocumentReference:e"]
    assert [round(h.score, 6) for h in hits] == [1.0, round(1 / math.sqrt(2), 6), round(1 / math.sqrt(2), 6), 0.0, -1.0]
    assert ids(store.query([1.0, 0.0, 0.0], model_identifier=MODEL, filters=F(), limit=2)) == ["DocumentReference:a", "DocumentReference:b"]


def test_vectors_are_normalized_on_the_way_in_and_on_the_way_out(store):
    store.upsert([entry("DocumentReference:a", vec=(10.0, 0.0, 0.0))])
    (hit,) = store.query([0.001, 0.0, 0.0], model_identifier=MODEL, filters=F(), limit=1)
    assert hit.score == pytest.approx(1.0)


def test_score_matches_a_manual_numpy_cosine(store):
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(6, 16))
    store.upsert([entry(f"DocumentReference:{i}", vec=tuple(v)) for i, v in enumerate(vecs)])
    q = rng.normal(size=16)
    expected = {f"DocumentReference:{i}": float(v @ q / (np.linalg.norm(v) * np.linalg.norm(q))) for i, v in enumerate(vecs)}
    for h in store.query(list(q), model_identifier=MODEL, filters=F(), limit=10):
        assert h.score == pytest.approx(expected[h.doc_id], abs=1e-5)


def test_filters_are_applied_in_sql_before_ranking(store):
    store.upsert([
        entry("DocumentReference:a", pid="p1", vec=(1, 0, 0), rdate=date(2024, 1, 10)),
        entry("DocumentReference:b", pid="p2", vec=(1, 0, 0), rdate=date(2024, 2, 20)),
        entry("ClinicalSummary:c", pid="p1", rtype="ClinicalSummary", vec=(1, 0, 0), rdate=date(2024, 3, 30)),
        entry("DocumentReference:d", pid="p1", vec=(1, 0, 0), rdate=None)])
    q = [1.0, 0.0, 0.0]
    run = lambda **kw: sorted(ids(store.query(q, model_identifier=MODEL, filters=F(**kw), limit=10)))
    assert run(patient_id="p1") == ["ClinicalSummary:c", "DocumentReference:a", "DocumentReference:d"]
    assert run(patient_id="p2") == ["DocumentReference:b"] and run(patient_id="nobody") == []
    assert run(resource_types=("ClinicalSummary",)) == ["ClinicalSummary:c"]
    assert run(resource_types=("ClinicalSummary", "DocumentReference"), patient_id="p1") == run(patient_id="p1")
    assert run(date_from=date(2024, 2, 1)) == ["ClinicalSummary:c", "DocumentReference:b"]           # undated excluded
    assert run(date_to=date(2024, 1, 10)) == ["DocumentReference:a"]                                 # inclusive upper bound
    assert run(date_from=date(2024, 1, 10), date_to=date(2024, 2, 20)) == ["DocumentReference:a", "DocumentReference:b"]
    assert run(patient_id="p1", date_from=date(2024, 3, 1), resource_types=("DocumentReference",)) == []


def test_patient_scoped_queries_never_load_other_patients_vectors(store, monkeypatch):
    store.upsert([entry("DocumentReference:a", pid="p1"), entry("DocumentReference:b", pid="p2")])
    seen = []
    real = np.vstack

    def spy(rows):
        seen.append(len(rows))
        return real(rows)

    monkeypatch.setattr(np, "vstack", spy)
    store.query([1.0, 0.0, 0.0], model_identifier=MODEL, filters=F(patient_id="p1"), limit=5)
    assert seen == [1]                                                   # only p1's single vector reached the scorer


def test_sql_special_characters_in_filters_are_harmless(store):
    store.upsert([entry("DocumentReference:a")])
    assert store.query([1.0, 0.0, 0.0], model_identifier=MODEL, filters=F(patient_id="x' OR '1'='1"), limit=5) == []
    assert store.query([1.0, 0.0, 0.0], model_identifier="'; DROP TABLE search_index_entries;--", filters=F(), limit=5) == []
    assert len(store.content_hashes("p1")) == 1


def test_different_models_and_dimensions_are_never_compared(store):
    store.upsert([entry("DocumentReference:a", model="m1", vec=(1, 0, 0)), entry("DocumentReference:b", model="m2", vec=(1, 0, 0)),
                  entry("DocumentReference:c", model="m1", vec=(1, 0))])
    assert ids(store.query([1.0, 0.0, 0.0], model_identifier="m1", filters=F(), limit=9)) == ["DocumentReference:a"]
    assert ids(store.query([1.0, 0.0, 0.0], model_identifier="m2", filters=F(), limit=9)) == ["DocumentReference:b"]
    assert store.query([1.0, 0.0, 0.0], model_identifier="unknown", filters=F(), limit=9) == []


def test_stats(store):
    assert store.stats().total == 0
    store.upsert([entry("DocumentReference:a"), entry("ClinicalSummary:b", rtype="ClinicalSummary", model="m2")])
    s = store.stats()
    assert s.total == 2 and s.by_resource_type == {"DocumentReference": 1, "ClinicalSummary": 1} and s.by_model == {MODEL: 1, "m2": 1}


def test_foreign_key_violation_raises_a_generic_error_and_a_batch_is_all_or_nothing(store):
    with pytest.raises(VectorStoreError) as e:
        store.upsert([entry("DocumentReference:ok"), entry("DocumentReference:bad", pid="ghost")])
    assert "ghost" not in str(e.value) and store.content_hashes("p1") == {}          # nothing from the batch persisted


def test_entries_persist_across_store_instances_and_sessions(factory, store):
    store.upsert([entry("DocumentReference:a", text="durable")])
    again = SqliteVectorStore(factory)
    assert again.content_hashes("p1") == {"DocumentReference:a": "h1"}
    assert again.query([1.0, 0.0, 0.0], model_identifier=MODEL, filters=F(), limit=1)[0].text == "durable"


def test_database_errors_are_mapped_to_a_safe_domain_error(factory):
    bare = SqliteVectorStore(factory)
    with factory() as s:
        s.execute(SearchIndexRow.__table__.delete())
        s.execute(__import__("sqlalchemy").text("DROP TABLE search_index_entries"))
        s.commit()
    for call in (lambda: bare.stats(), lambda: bare.content_hashes("p1"),
                 lambda: bare.query([1.0], model_identifier="m", filters=F(), limit=1), lambda: bare.delete(["x"])):
        with pytest.raises(VectorStoreError) as e:
            call()
        assert "search_index_entries" not in str(e.value) and "sqlite" not in str(e.value).lower()


# ---------------------------------------------------------------- the index is derived: rebuild from SQLite
def test_the_index_can_be_wiped_and_rebuilt_from_the_source_of_truth(factory):
    uow = lambda: SqlAlchemyUnitOfWork(factory)
    rows = [{"mrn": "000601", "name": "Rae Rebuild", "dob": "1980-01-01", "note": "Persistent cough for three weeks.", "date": "2024-04-01"},
            {"mrn": "000602", "name": "Sid Source", "dob": "1990-02-02", "note": "Sprained ankle after football.", "date": "2024-04-02"}]
    results = IngestPatientRecordsUseCase(uow, IngestionStrategyFactory(), ResolvePatientIdentityUseCase()).execute(
        IngestCommand(json.dumps(rows).encode(), "r.json", SourceFormat.JSON)).results
    gen = GenerateFHIRBundleUseCase(uow, R4BBundleBuilder(), R4BSchemaValidator())
    for r in results:
        gen.execute(r.patient_id)
    embedder, vstore = HashingEmbeddingProvider(), SqliteVectorStore(factory)
    reader = GetPatientSummaryUseCase(uow, "gpt-x", SummaryConfig())
    index, search = (IndexClinicalRecordsUseCase(uow, embedder, vstore, reader),
                     SearchClinicalRecordsUseCase(uow, embedder, vstore, reader))
    assert sum(r.indexed for r in index.execute_all()) == 2
    before = [(i.resource_id, i.score) for i in search.execute(SearchQuery(query="cough")).items]
    assert before and vstore.stats().total == 2

    with factory() as s:                                                   # catastrophic loss of the derived index
        s.execute(SearchIndexRow.__table__.delete())
        s.commit()
    assert vstore.stats().total == 0 and search.execute(SearchQuery(query="cough")).items == []
    assert sum(r.indexed for r in index.execute_all()) == 2                # rebuilt from fhir_resources alone
    assert [(i.resource_id, i.score) for i in search.execute(SearchQuery(query="cough")).items] == before
