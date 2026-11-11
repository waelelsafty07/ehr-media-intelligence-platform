"""Domain/application errors. Presentation maps these to HTTP statuses; messages are client-safe."""


class DomainError(Exception):
    """Base class. Messages must never contain clinical content."""


class IngestionParseError(DomainError):
    """The whole input could not be parsed (malformed JSON, invalid CSV, empty file...)."""


class UnsupportedFormatError(DomainError):
    """Input type is not one of json/csv/text."""


class FileTooLargeError(DomainError):
    """Upload exceeds the configured size limit."""


class PatientNotFoundError(DomainError):
    """No patient with the requested ID."""


class FhirNotAvailableError(DomainError):
    """FHIR resources for the patient have not been (successfully) generated."""

    def __init__(self, status: str | None) -> None:
        super().__init__("FHIR resources are not available for this patient. Generate them first.")
        self.status = status


class FhirBuildError(DomainError):
    """The mapper could not construct valid FHIR resources; carries client-safe issues."""

    def __init__(self, issues: list) -> None:
        super().__init__("FHIR resources could not be constructed.")
        self.issues = issues


class FhirValidationFailedError(DomainError):
    """Generated FHIR failed validation. `issues` are client-safe (path + rule, no values)."""

    def __init__(self, issues: list) -> None:
        super().__init__("Generated FHIR resources failed validation.")
        self.issues = issues


class FhirGenerationFailedError(DomainError):
    """Unexpected failure while generating FHIR; the failure is recorded and generation can be retried."""

    def __init__(self) -> None:
        super().__init__("FHIR generation failed. It can be retried.")
