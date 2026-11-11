"""Latency budget for the assessment target: < 2 s for 50 records (deterministic hashing embedder, temp SQLite).

This is a regression guard on a documented local setup, not a benchmark. The real-model, cold-vs-warm
measurements are produced by backend/scripts/benchmark_search.py and recorded in docs/ARCHITECTURE.md.
"""

import json
import statistics
import time

import pytest

NOTES = [
    "Persistent cough for three weeks, no fever.", "Chest tightness on exertion, possible angina.",
    "Sprained ankle after football, swelling and bruising.", "Seasonal allergic rhinitis, sneezing and itchy eyes.",
    "Follow-up for hypertension, blood pressure controlled.", "Abdominal pain after meals, nausea.",
    "Headache with photophobia, history of migraine.", "Lower back pain after lifting, no numbness.",
    "Fatigue and weight loss over two months.", "Rash on forearms, itchy, no new medications.",
]


@pytest.fixture
def fifty_records(client):
    rows = [{"mrn": f"7{i:05d}", "name": f"Perf Patient{i}", "dob": "1980-01-01", "date": f"2024-0{1 + i % 9}-1{i % 9}",
             "note": f"{NOTES[i % len(NOTES)]} Visit number {i}."} for i in range(50)]
    res = client.post("/ingest", files={"file": ("perf.json", json.dumps(rows).encode())}).json()["results"]
    assert len(res) == 50 and all(r["status"] == "completed" for r in res)
    for r in res:
        assert client.post(f"/patients/{r['patient_id']}/fhir").status_code == 200
    return res


def test_indexing_and_searching_50_records_is_well_under_two_seconds(client, fifty_records):
    t = time.perf_counter()
    r = client.post("/search/reindex").json()
    index_seconds = time.perf_counter() - t
    assert r["patients"] == 50 and r["indexed"] == 50

    queries = ["chest tightness", "ankle injury", "cough", "high blood pressure follow-up", "allergy symptoms", "back pain"]
    warm = []
    for q in queries * 5:
        t = time.perf_counter()
        resp = client.post("/search", json={"query": q})
        warm.append(time.perf_counter() - t)
        assert resp.status_code == 200 and 1 <= resp.json()["returned"] <= 5
    assert max(warm) < 2.0, f"slowest warm search {max(warm):.3f}s"
    assert statistics.median(warm) < 0.5 and index_seconds < 10
    scoped = client.post("/search", json={"query": "cough", "patient_id": fifty_records[0]["patient_id"]})
    assert scoped.status_code == 200 and all(x["patient_id"] == fifty_records[0]["patient_id"] for x in scoped.json()["results"])
