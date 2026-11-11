import json
import logging

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import JsonFormatter, redact


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert "X-Request-ID" in r.headers


def test_health_reports_degraded_without_leaking_details(client):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("secret connection string /path/to/db")

    client.app.state.engine = BrokenEngine()
    r = client.get("/health")
    assert r.json()["status"] == "degraded"
    assert "secret" not in r.text


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "http_error" and err["request_id"]


def test_unhandled_exception_is_not_exposed(client):
    app: FastAPI = client.app

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal detail: password=hunter2")

    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in r.text


def test_redact_removes_api_keys_and_bearer_tokens():
    assert "sk-abcdefgh12345" not in redact("key=sk-abcdefgh12345")
    assert "tok123" not in redact("Authorization: Bearer tok123")


def test_json_formatter_redacts_extra_fields():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    rec.api_key = "sk-abcdefgh12345"
    out = json.loads(JsonFormatter().format(rec))
    assert out["api_key"] == "[REDACTED]" and out["message"] == "msg"


def test_secret_not_in_settings_repr():
    s = Settings(openai_api_key="sk-abcdefgh12345", _env_file=None)
    assert "sk-abcdefgh12345" not in repr(s)
