"""IngestionStrategyFactory: the single place that maps an input type to a parser."""

from __future__ import annotations

from app.application.ingestion.base import IngestionStrategy
from app.application.ingestion.csv_strategy import CSVIngestionStrategy
from app.application.ingestion.json_strategy import JSONIngestionStrategy
from app.application.ingestion.text_strategy import TextIngestionStrategy
from app.domain.canonical import SourceFormat
from app.domain.errors import UnsupportedFormatError

_EXTENSIONS = {".json": SourceFormat.JSON, ".csv": SourceFormat.CSV,
               ".txt": SourceFormat.TEXT, ".text": SourceFormat.TEXT}


def resolve_format(filename: str | None, explicit: str | None = None) -> SourceFormat:
    """Explicit format wins; otherwise infer from the file extension."""
    if explicit:
        try:
            return SourceFormat(explicit.strip().lower())
        except ValueError as exc:
            raise UnsupportedFormatError("Unsupported format. Use json, csv or text.") from exc
    name = (filename or "").lower()
    for ext, fmt in _EXTENSIONS.items():
        if name.endswith(ext):
            return fmt
    raise UnsupportedFormatError("Cannot determine format; use a .json/.csv/.txt file or set 'format'.")


class IngestionStrategyFactory:
    def __init__(self) -> None:
        strategies: list[IngestionStrategy] = [
            JSONIngestionStrategy(), CSVIngestionStrategy(), TextIngestionStrategy()]
        self._by_format = {s.source_format: s for s in strategies}

    def for_format(self, source_format: SourceFormat) -> IngestionStrategy:
        try:
            return self._by_format[source_format]
        except KeyError as exc:
            raise UnsupportedFormatError("Unsupported format.") from exc
