from __future__ import annotations

import csv
import io

from app.application.ingestion.base import ParsedRecord, decode_utf8
from app.domain.canonical import SourceFormat
from app.domain.canonicalization import field_for_key
from app.domain.errors import IngestionParseError


class CSVIngestionStrategy:
    source_format = SourceFormat.CSV

    def parse(self, content: bytes) -> list[ParsedRecord]:
        text = decode_utf8(content)
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            header = reader.fieldnames
            if not header:
                raise IngestionParseError("CSV has no header row.")
            if not any(field_for_key(h or "") for h in header):
                raise IngestionParseError("CSV header has no recognized columns.")
            records: list[ParsedRecord] = []
            for index, row in enumerate(reader):
                if None in row:  # more cells than header columns
                    records.append(ParsedRecord(index, parse_error="too_many_columns"))
                elif any(v is None for v in row.values()):  # fewer cells than header columns
                    records.append(ParsedRecord(index, parse_error="too_few_columns"))
                else:
                    records.append(ParsedRecord(index, dict(row)))
        except csv.Error as exc:
            raise IngestionParseError("CSV is malformed.") from exc
        if not records:
            raise IngestionParseError("CSV contains no data rows.")
        return records
