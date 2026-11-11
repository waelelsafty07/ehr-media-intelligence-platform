"""Enforces Clean Architecture dependency direction by scanning imports (no execution needed)."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

INFRA_LIBS = {"fastapi", "starlette", "sqlalchemy", "chromadb", "openai",
              "sentence_transformers", "fhir", "httpx", "uvicorn"}
RULES = {  # layer -> top-level modules it must not import
    "domain": INFRA_LIBS | {"app.application", "app.infrastructure", "app.api"},
    "application": INFRA_LIBS | {"app.infrastructure", "app.api"},
}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _violations(layer: str) -> list[str]:
    bad = []
    for file in (APP / layer).rglob("*.py"):
        for imp in _imports(file):
            for banned in RULES[layer]:
                if imp == banned or imp.startswith(banned + "."):
                    bad.append(f"{file.relative_to(APP)} imports {imp}")
    return bad


def test_domain_has_no_outward_or_framework_dependencies():
    assert _violations("domain") == []


def test_application_does_not_depend_on_infrastructure_or_api():
    assert _violations("application") == []


def test_checker_detects_a_violation(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("import sqlalchemy\nfrom app.infrastructure import x\n")
    assert {"sqlalchemy", "app.infrastructure"} <= _imports(f)


def test_fhir_library_is_confined_to_infrastructure_and_only_the_r4b_namespace():
    """fhir.resources' top-level namespace is FHIR R5 (5.0.0); R4-compatible models live in R4B."""
    offenders = []
    for file in APP.rglob("*.py"):
        for imp in _imports(file):
            if imp == "fhir" or imp.startswith("fhir."):
                rel = file.relative_to(APP)
                if rel.parts[:2] != ("infrastructure", "fhir"):
                    offenders.append(f"{rel} imports {imp} outside infrastructure/fhir")
                elif not imp.startswith("fhir.resources.R4B"):
                    offenders.append(f"{rel} imports non-R4B namespace {imp}")
    assert offenders == []


def test_openai_sdk_is_confined_to_infrastructure_llm():
    offenders = []
    for file in APP.rglob("*.py"):
        for imp in _imports(file):
            if (imp == "openai" or imp.startswith("openai.")) and file.relative_to(APP).parts[:2] != ("infrastructure", "llm"):
                offenders.append(f"{file.relative_to(APP)} imports {imp}")
    assert offenders == []


def test_numpy_and_sentence_transformers_are_confined_to_infrastructure_search():
    offenders = []
    for file in APP.rglob("*.py"):
        for imp in _imports(file):
            if imp.split(".")[0] in {"numpy", "sentence_transformers"} and file.relative_to(APP).parts[:2] != ("infrastructure", "search"):
                offenders.append(f"{file.relative_to(APP)} imports {imp}")
    assert offenders == []
