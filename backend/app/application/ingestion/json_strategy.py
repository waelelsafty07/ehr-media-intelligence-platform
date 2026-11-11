from __future__ import annotations

import json
from typing import Any

from app.application.ingestion.base import ParsedRecord, decode_utf8
from app.domain.canonical import SourceFormat
from app.domain.errors import IngestionParseError

_WRAPPER_KEYS = ("records", "patients")  # {"records": [...]}
_NESTED_KEYS = ("patient", "demographics", "document")  # {"patient": {"mrn": ...}, "document": {...}}


def _flatten(item: dict[str, Any]) -> dict[str, Any]:
    flat = {k: v for k, v in item.items() if not (k in _NESTED_KEYS and isinstance(v, dict))}
    for key in _NESTED_KEYS:
        if isinstance(item.get(key), dict):
            for k, v in item[key].items():
                flat.setdefault(k, v)
    return flat


class JSONIngestionStrategy:
    source_format = SourceFormat.JSON

    def parse(self, content: bytes) -> list[ParsedRecord]:
        try:
            data = json.loads(decode_utf8(content))
        except json.JSONDecodeError as exc:
            # position only: never echo content
            raise IngestionParseError(f"Malformed JSON at line {exc.lineno}, column {exc.colno}.") from exc
        if isinstance(data, dict):
            wrapped = next((data[k] for k in _WRAPPER_KEYS if isinstance(data.get(k), list)), None)
            data = wrapped if wrapped is not None else [data]
        if not isinstance(data, list):
            raise IngestionParseError("JSON must be an object or an array of objects.")
        if not data:
            raise IngestionParseError("JSON contains no records.")
        return [
            ParsedRecord(i, _flatten(item)) if isinstance(item, dict)
            else ParsedRecord(i, parse_error="record_not_an_object")
            for i, item in enumerate(data)
        ]
