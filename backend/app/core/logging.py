"""Structured (JSON) logging with request-ID correlation and secret redaction."""

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Redacts OpenAI-style keys and bearer tokens if they ever reach a log message.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
]
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    return _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": redact(record.getMessage()),
        }
        # Extra fields passed via logger.info(..., extra={...}); values are redacted too.
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
