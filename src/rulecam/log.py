"""Structured JSON logging. Use `get_logger(__name__)`; never `print` (rule R10)."""

from __future__ import annotations

import logging

import structlog

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog for JSON output once per process.

    Args:
        level: Minimum log level.
    """
    global _configured  # noqa: PLW0603 - one-time process configuration
    if _configured:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """Return a structured logger bound to `name`.

    Args:
        name: Usually the calling module's `__name__`.

    Returns:
        A structlog logger; bind context such as `event_id` or `camera_id` on it.
    """
    configure_logging()
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger.bind(logger=name)
