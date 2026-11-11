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
