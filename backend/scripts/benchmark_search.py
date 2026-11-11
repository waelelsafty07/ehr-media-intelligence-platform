"""Repeatable semantic-search benchmark (synthetic data only).

Usage (from the repository root, venv active):
    python backend/scripts/benchmark_search.py                                     # default: semantic (needs the `embeddings` extra)
    python backend/scripts/benchmark_search.py --provider hashing                  # explicit lexical fallback
    python backend/scripts/benchmark_search.py --provider hashing --scale 500,5000,20000

Measures, over the real HTTP stack (in-process FastAPI TestClient) against a temporary SQLite database:
  * cold start: provider construction + first embedding call (for sentence-transformers: torch import + model load)
  * indexing 50 records, warm search latency (p50 / p95 / max), patient-scoped latency
  * relevance on topic queries worded differently from the notes (hit@5 and precision@5)
  * optional store-level scaling with random vectors (exact O(n) scan)
It is NOT a clinical-quality evaluation: topics and queries are small, synthetic and written by the author.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 10 topics x 5 phrasings = 50 distinct synthetic notes (one record per synthetic patient).
TOPICS: dict[str, list[str]] = {
    "chest_pain": ["Chest tightness on exertion, resolves with rest.", "Pressure behind the sternum when climbing stairs.",
                   "Exertional chest discomfort, possible angina, stress test planned.",
                   "Squeezing sensation in the chest during a brisk walk.", "Retrosternal pain with effort, no pain at rest."],
    "breathing": ["Shortness of breath on minor exertion.", "Wheezing and breathlessness at night.",
                  "Dyspnea when climbing one flight of stairs.", "Short of breath lying flat, uses two pillows.",
                  "Breathless after walking a short distance, no cough."],
    "cough": ["Persistent dry cough for three weeks.", "Productive cough with yellow sputum for ten days.",
              "Nagging cough worse at night, no fever.", "Chronic cough since a cold last month.",
              "Coughing fits after meals, throat clearing."],
    "ankle_injury": ["Sprained ankle after football, swelling and bruising.", "Twisted ankle on a curb, painful to weight-bear.",
                     "Swollen painful ankle after a fall, no deformity.", "Lateral ankle ligament injury while running.",
                     "Rolled ankle during hiking, tender over the outer ankle."],
    "allergy": ["Seasonal sneezing and itchy watery eyes.", "Hay fever symptoms every spring, runny nose.",
                "Allergic rhinitis with nasal congestion and itching.", "Itchy eyes and sneezing around pollen season.",
                "Blocked nose and sneezing, worse outdoors in summer."],
    "hypertension": ["Follow-up for high blood pressure, readings controlled.", "Hypertension review, medication tolerated well.",
                     "Elevated blood pressure at home monitoring.", "Blood pressure remains above target on current treatment.",
                     "Routine hypertension check, no headaches or dizziness."],
    "abdominal": ["Abdominal pain after meals with nausea.", "Stomach cramps and bloating for two weeks.",
                  "Epigastric discomfort and heartburn at night.", "Upper belly pain, worse after fatty food.",
                  "Nausea and abdominal cramping, no vomiting."],
    "migraine": ["Headache with sensitivity to light, history of migraine.", "Throbbing one-sided headache with nausea.",
                 "Recurrent migraine with visual aura.", "Severe headache, photophobia, lies in a dark room.",
                 "Pulsating head pain on the left side, lasts hours."],
    "back_pain": ["Lower back pain after lifting, no numbness.", "Aching in the lumbar spine when bending.",
                  "Sharp back pain radiating to the buttock, no weakness.", "Stiff painful lower back in the mornings.",
                  "Back strain from gardening, improves with movement."],
    "fatigue": ["Fatigue and weight loss over two months.", "Constant tiredness and low energy, sleeping poorly.",
                "Exhausted all day despite adequate sleep.", "Unexplained weight loss and lack of energy.",
                "Persistent tiredness, struggles to finish the day."],
}
# Queries deliberately use different words than the notes.
QUERIES: list[tuple[str, str]] = [
    ("pain in the chest when exercising", "chest_pain"), ("heart-related discomfort during activity", "chest_pain"),
    ("difficulty breathing", "breathing"), ("cannot catch breath going upstairs", "breathing"),
    ("long-lasting cough", "cough"), ("coughing up phlegm", "cough"),
    ("twisted foot injury", "ankle_injury"), ("swollen joint after sports", "ankle_injury"),
    ("pollen sensitivity", "allergy"), ("runny nose and watery eyes in spring", "allergy"),
    ("raised blood pressure follow-up", "hypertension"), ("antihypertensive medication check", "hypertension"),
    ("tummy ache after eating", "abdominal"), ("indigestion and bloating", "abdominal"),
    ("bad headache bothered by bright light", "migraine"), ("recurrent head pain with aura", "migraine"),
    ("sore lumbar region from lifting", "back_pain"), ("pain in the lower spine", "back_pain"),
    ("always tired and losing weight", "fatigue"), ("lack of energy", "fatigue"),
]


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


def build_app(provider: str, workdir: str):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(database_url=f"sqlite:///{workdir}/bench.db", embedding_provider=provider, log_level="ERROR", _env_file=None)
    return TestClient(create_app(settings))


def run_corpus(provider: str) -> dict:
    out: dict = {"provider": provider}
    with tempfile.TemporaryDirectory() as tmp:
        client = build_app(provider, tmp)
        rng = random.Random(42)

        # ---- build 50 synthetic patients/records through the real pipeline
        rows, labels = [], []
        for topic, notes in TOPICS.items():
            for note in notes:
                labels.append(topic)
                rows.append({"mrn": f"8{len(rows):05d}", "name": f"Bench Patient{len(rows)}", "dob": "1980-01-01",
                             "date": f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", "note": note})
        results = client.post("/ingest", files={"file": ("bench.json", json.dumps(rows).encode())}).json()["results"]
        topic_of = {r["patient_id"]: labels[i] for i, r in enumerate(results)}
        for r in results:
            client.post(f"/patients/{r['patient_id']}/fhir")

        # ---- cold start: provider construction (done in create_app) + first embedding call
        provider_obj = client.app.state.embedding_provider
        t = time.perf_counter()
        provider_obj.embed(["warm-up text"])
        out["cold_start_first_embed_s"] = round(time.perf_counter() - t, 3)

        # ---- indexing
        t = time.perf_counter()
        idx = client.post("/search/reindex").json()
        out["index_50_records_s"] = round(time.perf_counter() - t, 3)
        out["indexed"] = idx["indexed"]
        t = time.perf_counter()
        again = client.post("/search/reindex").json()
        out["reindex_unchanged_s"] = round(time.perf_counter() - t, 3)
        out["reindex_second_run_indexed"] = again["indexed"]

        # ---- warm search latency (first request after warm-up excluded from stats by a priming call)
        client.post("/search", json={"query": "priming query"})
        lat = []
        for _ in range(3):
            for q, _topic in QUERIES:
                t = time.perf_counter()
                resp = client.post("/search", json={"query": q})
                lat.append((time.perf_counter() - t) * 1000)
                assert resp.status_code == 200
        out["warm_search_ms"] = {"n": len(lat), "p50": round(statistics.median(lat), 1), "p95": round(percentile(lat, 95), 1),
                                 "max": round(max(lat), 1), "mean": round(statistics.mean(lat), 1)}
        scoped = []
        pid = results[0]["patient_id"]
        for q, _ in QUERIES:
            t = time.perf_counter()
            client.post("/search", json={"query": q, "patient_id": pid})
            scoped.append((time.perf_counter() - t) * 1000)
        out["scoped_search_ms"] = {"p50": round(statistics.median(scoped), 1), "max": round(max(scoped), 1)}

        # ---- relevance (topic-level, synthetic)
        hits, precisions, top1 = 0, [], 0
        for q, topic in QUERIES:
            res = client.post("/search", json={"query": q}).json()["results"]
            labels_top = [topic_of[r["patient_id"]] for r in res]
            hits += topic in labels_top
            top1 += bool(labels_top) and labels_top[0] == topic
            precisions.append(sum(1 for lab in labels_top if lab == topic) / 5)
        n = len(QUERIES)
        out["relevance"] = {"queries": n, "hit_at_5": round(hits / n, 2), "top1_correct": round(top1 / n, 2),
                            "precision_at_5": round(statistics.mean(precisions), 2)}
        out["embedding"] = client.get("/search/status").json()["embedding"]
    return out


def run_scale(sizes: list[int]) -> list[dict]:
    from datetime import UTC, date, datetime

    import numpy as np

    from app.domain.search import IndexEntry, SearchFilters
    from app.infrastructure.persistence.session import (
        create_db_engine,
        get_session_factory,
        init_schema,
    )
    from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
    from app.infrastructure.search.sqlite_vector_store import SqliteVectorStore

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_db_engine(f"sqlite:///{tmp}/scale.db")
        init_schema(engine)
        factory = get_session_factory(engine)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        from tests.fhir_helpers import make_patient

        with SqlAlchemyUnitOfWork(factory) as u:
            u.patients.add(make_patient(patient_id="p1", mrn="000101"))
            u.commit()
        store = SqliteVectorStore(factory)
        rng = np.random.default_rng(1)
        now, total = datetime.now(UTC), 0
        for size in sizes:
            batch = []
            for i in range(total, size):
                batch.append(IndexEntry(doc_id=f"DocumentReference:{i}", patient_id="p1", resource_type="DocumentReference",
                                        resource_id=str(i), record_date=date(2024, 1, 1), text="synthetic", content_hash=str(i),
                                        model_identifier="scale", embedding=rng.normal(size=384).tolist(), indexed_at=now))
                if len(batch) == 1000:
                    store.upsert(batch)
                    batch = []
            store.upsert(batch)
            total = size
            q = rng.normal(size=384).tolist()
            times = []
            for _ in range(5):
                t = time.perf_counter()
                store.query(q, model_identifier="scale", filters=SearchFilters(), limit=20)
                times.append((time.perf_counter() - t) * 1000)
            rows.append({"entries": size, "query_ms_median": round(statistics.median(times), 1), "query_ms_max": round(max(times), 1)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["hashing", "sentence-transformers"], default="sentence-transformers",
                    help="default = the application default (semantic); hashing = the explicit lexical fallback")
    ap.add_argument("--scale", default="", help="comma-separated entry counts for the store scaling test")
    args = ap.parse_args()
    report = {"environment": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system(),
                              "cpus": os.cpu_count()}}
    if args.scale:
        report["scale_store_only"] = run_scale([int(x) for x in args.scale.split(",")])
    else:
        report.update(run_corpus(args.provider))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
