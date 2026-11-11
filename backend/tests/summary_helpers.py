"""Synthetic clinical fixtures and a scripted fake LLM for summary tests (no network, no keys)."""

from __future__ import annotations

import json
from typing import Any

from app.domain.errors import LLMProviderError
from app.domain.ports.llm import LLMRequest, LLMResponse
from tests.fhir_helpers import make_record

R1_RAW = {"mrn": "000101", "date": "2023-11-10", "diagnosis": "Hypertension",
          "note": "Seen for chest tightness on exertion. History of hypertension. "
                  "Possible angina, awaiting stress test."}
R2_RAW = {"mrn": "000101", "date": "2024-02-01",
          "note": "Follow-up visit. Stress test normal. Chest tightness has resolved."}

SUMMARY_TEXT = ("Chest tightness on exertion was documented on 2023-11-10, with a history of hypertension and "
                "possible angina pending a stress test. At follow-up on 2024-02-01 the stress test was reported "
                "normal and the chest tightness had resolved.")


def records(patient_id: str = "11111111-1111-4111-8111-111111111111"):
    return make_record(R1_RAW, patient_id, 0), make_record(R2_RAW, patient_id, 1)


def good_output(r1_id: str, r2_id: str) -> dict[str, Any]:
    return {
        "chief_concern": {"text": "Chest tightness on exertion", "evidence_quote": "chest tightness on exertion",
                          "source_record_ids": [r1_id]},
        "key_diagnoses": [
            {"name": "hypertension", "certainty": "stated", "temporal": "historical", "text": "History of hypertension",
             "evidence_quote": "History of hypertension", "source_record_ids": [r1_id]},
            {"name": "angina", "certainty": "uncertain", "temporal": "unspecified", "text": "Possible angina",
             "evidence_quote": "Possible angina, awaiting stress test", "source_record_ids": [r1_id]},
        ],
        "anomalies": [],
        "summary_text": SUMMARY_TEXT,
    }


def as_json(output: dict[str, Any]) -> str:
    return json.dumps(output)


class ScriptedLLM:
    """Fake LLMProvider: returns/raises scripted items in order, repeating the last one when exhausted."""

    def __init__(self, *items: str | Exception | LLMResponse, model: str = "test-model") -> None:
        self.model_identifier = model
        self._items = list(items)
        self.requests: list[LLMRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._items.pop(0) if len(self._items) > 1 else self._items[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(text=item, model=self.model_identifier, finish_reason="stop", total_tokens=100)


class BrokenProvider(ScriptedLLM):
    def __init__(self, error: LLMProviderError) -> None:
        super().__init__(error)
