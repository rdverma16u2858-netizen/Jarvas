"""
Logging setup.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    `print()` does not survive contact with production: no timestamps, no
    severity, no module name, and it cannot be filtered or shipped to a log
    aggregator. Every module calls `get_logger(__name__)` instead and gets a
    logger that is already configured.

TWO FORMATS, CHOSEN BY ENVIRONMENT
    · local       — human-readable single lines, for reading in a terminal
    · production  — JSON, one object per line, for a log aggregator to index

    Same call sites, different rendering. Nothing in the app changes.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object.

    Log aggregators (CloudWatch, Loki, Datadog) parse JSON lines natively, so
    fields become searchable — `level:ERROR AND logger:app.api.routes.solve`
    instead of a substring grep over unstructured text.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Attach the traceback when logger.exception() was used.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Anything passed via logger.info("...", extra={"request_id": ...})
        # becomes a top-level field, so per-request context is searchable.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


# Attributes LogRecord always has — anything else came from `extra=`.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


def configure_logging() -> None:
    """Install handlers on the root logger. Called once, from main.py.

    Replaces any existing handlers rather than adding to them — uvicorn
    installs its own, and without this you get every line twice.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if settings.is_local:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s  %(levelname)-8s %(name)-28s %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    else:
        handler.setFormatter(JsonFormatter())

    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.is_local else logging.INFO)

    # SQLAlchemy logs every statement at INFO when echo is on. Pin it to
    # WARNING unless DATABASE_ECHO was explicitly requested, or the query log
    # buries everything else.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DATABASE_ECHO else logging.WARNING
    )
    # uvicorn's access log duplicates information the app already logs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a module. Always call as `get_logger(__name__)`.

    Using `__name__` means the log line identifies the module it came from
    (`app.api.routes.health`), which is the difference between a log you can
    navigate and one you can only read top to bottom.
    """
    return logging.getLogger(name)
