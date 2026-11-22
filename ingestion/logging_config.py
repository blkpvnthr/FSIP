"""Structured logging configuration.

Emits machine-parseable JSON lines by default (one object per log record) so the
pipeline's output is greppable and ready for log aggregation in later milestones,
while still supporting a human-friendly text format for local development.

Usage::

    from ingestion.logging_config import configure_logging, get_logger

    configure_logging()
    log = get_logger(__name__)
    log.info("extraction.start", extra={"context": {"agency": "NASA"}})

Arbitrary structured fields are passed via ``extra={"context": {...}}`` and are
merged into the emitted record.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_CONFIGURED = False

# Standard LogRecord attributes we never want to duplicate into the JSON payload.
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
    "context",
}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # Structured context passed via extra={"context": {...}}.
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        # Any other ad-hoc extras attached to the record.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger exactly once (idempotent).

    Args:
        level: Logging level name (``DEBUG``, ``INFO``, ...).
        fmt: ``"json"`` for structured output, ``"text"`` for console output.
    """
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Replace handlers so re-configuration (e.g. in tests) is deterministic.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "text":
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, configuring logging with defaults if needed."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
