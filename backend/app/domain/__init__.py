"""Domain layer: entities, business rules, policies and ports (interfaces).

May import only the standard library and pydantic. Must NOT import fastapi, sqlalchemy,
chromadb, openai, sentence_transformers or fhir.* (enforced by tests/test_architecture.py).
Phase 2 adds: canonical models, normalization rules, identity policy, duplicate detection.
"""
