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


class PatientIsolationError(DomainError):
    """A record that does not belong to the requested patient reached the summary pipeline."""


class NoSourceRecordsError(DomainError):
    """The patient has no completed records with text or stated diagnoses to summarize."""


class SummaryNotFoundError(DomainError):
    """No generated summary exists for the patient."""


class LLMProviderError(DomainError):
    """Base for provider failures. Messages are generic: provider/SDK text is never propagated
    (it can echo credentials or request content)."""

    category = "provider_error"


class LLMTransientError(LLMProviderError):
    """Worth retrying: timeouts, connection problems, rate limits, 5xx."""

    category = "provider_unavailable"


class LLMTimeoutError(LLMTransientError):
    category = "provider_timeout"


class LLMPermanentError(LLMProviderError):
    """Not worth retrying: authentication, permission, bad request, content policy."""

    category = "provider_rejected"


class LLMNotConfiguredError(LLMProviderError):
    category = "provider_not_configured"

    def __init__(self) -> None:
        super().__init__("No LLM provider is configured.")


class SummaryOutputError(DomainError):
    """Model output could not be parsed or failed validation/grounding. `codes` are safe to log."""

    def __init__(self, codes: list[str]) -> None:
        super().__init__("Model output was invalid.")
        self.codes = codes


class SummaryGenerationFailedError(DomainError):
    """Generation failed after retries; recorded and retryable. `reason` is a safe category."""

    def __init__(self, reason: str) -> None:
        super().__init__("Summary generation failed. It can be retried.")
        self.reason = reason


class SearchInfrastructureError(DomainError):
    """Base for search/indexing infrastructure failures. Messages are generic (no provider text)."""


class EmbeddingUnavailableError(SearchInfrastructureError):
    """The embedding provider is missing, could not load its model, or failed while embedding."""


class VectorStoreError(SearchInfrastructureError):
    """The vector store could not be read or written."""
