"""Fixtures for search/indexing tests: in-memory vector store, flaky embedder, world builders (synthetic)."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.application.use_cases.index_clinical_records import IndexClinicalRecordsUseCase
from app.application.use_cases.search_clinical_records import SearchClinicalRecordsUseCase
from app.domain.errors import EmbeddingUnavailableError, VectorStoreError
from app.domain.search import IndexEntry, IndexStats, SearchFilters, VectorHit
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from app.infrastructure.search.hashing_embedder import HashingEmbeddingProvider
from tests.fakes import FakeStore, FakeUnitOfWork
from tests.fhir_helpers import make_patient, make_record
from tests.summary_helpers import ScriptedLLM, as_json, good_output, records

PID = "11111111-1111-4111-8111-111111111111"


class FakeVectorStore:
    """Pure-python VectorStore with the same contract as the SQLite one, plus fault injection."""

    def __init__(self) -> None:
        self.entries: dict[str, IndexEntry] = {}
        self.fail_on: dict[str, int] = {}
        self.calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        self.calls.append(op)
        if self.fail_on.get(op, 0) > 0:
            self.fail_on[op] -= 1
            raise VectorStoreError("The search index is unavailable.")

    def content_hashes(self, patient_id):
        self._maybe_fail("content_hashes")
        return {k: e.content_hash for k, e in self.entries.items() if e.patient_id == patient_id}

    def upsert(self, entries):
        self._maybe_fail("upsert")
        for e in entries:
            self.entries[e.doc_id] = e

    def delete(self, doc_ids):
        self._maybe_fail("delete")
        return sum(1 for d in doc_ids if self.entries.pop(d, None) is not None)

    def query(self, vector, *, model_identifier, filters: SearchFilters, limit):
        self._maybe_fail("query")
        hits = []
        for e in self.entries.values():
            if e.model_identifier != model_identifier or len(e.embedding) != len(vector):
                continue
            if filters.patient_id and e.patient_id != filters.patient_id:
                continue
            if filters.resource_types and e.resource_type not in filters.resource_types:
                continue
            if (filters.date_from or filters.date_to) and e.record_date is None:
                continue
            if filters.date_from and e.record_date < filters.date_from:
                continue
            if filters.date_to and e.record_date > filters.date_to:
                continue
            score = sum(a * b for a, b in zip(vector, e.embedding, strict=True))
            hits.append(VectorHit(**e.model_dump(exclude={"embedding", "indexed_at"}), score=score))
        hits.sort(key=lambda h: (-h.score, h.doc_id))
        return hits[:limit]

    def stats(self):
        self._maybe_fail("stats")
        by_type: dict[str, int] = {}
        by_model: dict[str, int] = {}
        for e in self.entries.values():
            by_type[e.resource_type] = by_type.get(e.resource_type, 0) + 1
            by_model[e.model_identifier] = by_model.get(e.model_identifier, 0) + 1
        return IndexStats(total=len(self.entries), by_resource_type=by_type, by_model=by_model)


class RecordingEmbedder(HashingEmbeddingProvider):
    """Real deterministic embedder that records every text it is asked to embed; can be made to fail."""

    def __init__(self, dimension: int = 384, fail: bool = False) -> None:
        super().__init__(dimension)
        self.texts: list[str] = []
        self.batches = 0
        self.fail = fail

    def embed(self, texts):
        self.batches += 1
        self.texts.extend(texts)
        if self.fail:
            raise EmbeddingUnavailableError("Embedding failed.")
        return super().embed(texts)


def new_clock():
    ticks = itertools.count()
    return lambda: datetime(2025, 3, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))


def uow_factory(store):
    return lambda: FakeUnitOfWork(store)


def add_patient(store: FakeStore, patient_id: str, mrn: str, name: str, notes: list[dict]):
    """Add a patient with COMPLETED records (one per raw dict in `notes`, each needs mrn/note/date)."""
    store.patients[patient_id] = make_patient(patient_id=patient_id, mrn=mrn, name=name)
    recs = []
    for i, raw in enumerate(notes):
        rec = make_record({"mrn": mrn, **raw}, patient_id, index=i)
        store.records[rec.record_id] = rec
        recs.append(rec)
    return recs


def generate_fhir(store: FakeStore, patient_id: str):
    return GenerateFHIRBundleUseCase(uow_factory(store), R4BBundleBuilder(), R4BSchemaValidator(),
                                     new_clock()).execute(patient_id)


def generate_summary(store: FakeStore, patient_id: str = PID):
    """Summary for the default patient (records R1/R2 from summary_helpers)."""
    r1, r2 = records()
    llm = ScriptedLLM(as_json(good_output(r1.record_id, r2.record_id)))
    return GenerateClinicalSummaryUseCase(uow_factory(store), llm, SummaryConfig(), new_clock()).execute(patient_id)


def default_world(*, summary: bool = True) -> FakeStore:
    """Alex (PID) with R1/R2 chest-tightness notes: FHIR generated, optionally an AI summary."""
    store = FakeStore()
    store.patients[PID] = make_patient()
    for r in records():
        store.records[r.record_id] = r
    generate_fhir(store, PID)
    if summary:
        generate_summary(store, PID)
    return store


def build(store: FakeStore, embedder=None, vstore=None):
    embedder = embedder or RecordingEmbedder()
    vstore = vstore if vstore is not None else FakeVectorStore()
    reader = GetPatientSummaryUseCase(uow_factory(store), "test-model", SummaryConfig())
    index = IndexClinicalRecordsUseCase(uow_factory(store), embedder, vstore, reader, new_clock())
    search = SearchClinicalRecordsUseCase(uow_factory(store), embedder, vstore, reader)
    return index, search, embedder, vstore


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def install_fake_sentence_transformers(monkeypatch, *, fail_load: bool = False, fail_encode: bool = False):
    """Install a fake `sentence_transformers` module (offline, deterministic: vectors come from the hashing embedder).
    Returns a recorder with .constructed (model loads), .calls (encode kwargs) and .devices."""
    import sys
    import types

    import numpy as np

    class Recorder:
        constructed = 0
        calls: ClassVar[list] = []
        devices: ClassVar[list] = []
        names: ClassVar[list] = []

    class FakeSentenceTransformer:
        def __init__(self, name, device=None):
            if fail_load:
                raise OSError("SECRET-PROVIDER-DETAIL: cannot reach huggingface.co")
            Recorder.constructed += 1
            Recorder.names.append(name)
            Recorder.devices.append(device)

        def encode(self, texts, **kwargs):
            Recorder.calls.append(kwargs)
            if fail_encode:
                raise RuntimeError("SECRET-PROVIDER-DETAIL: out of memory")
            return np.array(HashingEmbeddingProvider().embed(list(texts)), dtype=np.float32)

    import importlib.machinery

    module = types.ModuleType("sentence_transformers")
    module.__spec__ = importlib.machinery.ModuleSpec("sentence_transformers", None)   # as a really-imported package has
    module.SentenceTransformer = FakeSentenceTransformer
    module.__version__ = "0.0-fake"
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return Recorder
