"""FHIR R4 library adapter. The ONLY package allowed to import fhir.resources, and only its R4B namespace.

fhir.resources 8.x exposes FHIR R5 (5.0.0) at its top level; R4-compatible resources live in
fhir.resources.R4B (4.3.0). Importing from the top level would silently produce R5 (see
tests/test_architecture.py and docs/FHIR_MAPPING.md).
"""
