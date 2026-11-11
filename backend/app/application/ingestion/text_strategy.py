from __future__ import annotations

import re

from app.application.ingestion.base import ParsedRecord, decode_utf8
from app.domain.canonical import SourceFormat
from app.domain.canonicalization import field_for_key
from app.domain.errors import IngestionParseError

_SEPARATOR = re.compile(r"^\s*(-{3,}|={3,})\s*$")  # separates multiple records in one file
_LABELLED = re.compile(r"^\s*([A-Za-z][A-Za-z _/\-]{0,40}?)\s*:\s*(.*)$")


class TextIngestionStrategy:
    """'Label: value' lines for known demographic labels; all other lines form the document text."""

    source_format = SourceFormat.TEXT

    def parse(self, content: bytes) -> list[ParsedRecord]:
        text = decode_utf8(content)
        if not text.strip():
            raise IngestionParseError("Text input is empty.")
        blocks: list[list[str]] = [[]]
        for line in text.splitlines():
            if _SEPARATOR.match(line):
                blocks.append([])
            else:
                blocks[-1].append(line)
        records: list[ParsedRecord] = []
        for block in (b for b in blocks if any(line.strip() for line in b)):
            fields: dict[str, str] = {}
            body: list[str] = []
            for line in block:
                m = _LABELLED.match(line)
                if m and field_for_key(m.group(1)) not in (None, "text"):
                    fields.setdefault(m.group(1).strip(), m.group(2).strip())
                else:
                    body.append(line)
            note = "\n".join(body).strip()
            if note:
                fields["note"] = note
            records.append(ParsedRecord(len(records), fields))
        return records
