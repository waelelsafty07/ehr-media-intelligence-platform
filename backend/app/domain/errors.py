"""Domain/application errors. Presentation maps these to HTTP statuses; messages are client-safe."""


class DomainError(Exception):
    """Base class. Messages must never contain clinical content."""


class IngestionParseError(DomainError):
    """The whole input could not be parsed (malformed JSON, invalid CSV, empty file...)."""


class UnsupportedFormatError(DomainError):
    """Input type is not one of json/csv/text."""


class FileTooLargeError(DomainError):
    """Upload exceeds the configured size limit."""
