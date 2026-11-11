"""Strategy interface: turn raw bytes into structure-neutral parsed records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.canonical import SourceFormat
from app.domain.errors import IngestionParseError


@dataclass(frozen=True)
class ParsedRecord:
    """One source record. `fields` keeps the ORIGINAL keys; alias mapping/normalization happen later
    in the domain so all formats converge on the same canonical model."""

    index: int
    fields: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None  # short code; record is rejected but the rest of the file proceeds


class IngestionStrategy(Protocol):
    source_format: SourceFormat

    def parse(self, content: bytes) -> list[ParsedRecord]:
        """Raise IngestionParseError when the input as a whole is unusable."""
        ...


def decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionParseError("Input is not valid UTF-8 text.") from exc
