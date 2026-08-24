"""Request correlation for standard-library logging."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_FACTORY_MARKER = "_flo_correlation_factory"


def current_correlation_id() -> str | None:
    """Return the correlation id bound to the current request, if any."""

    return _correlation_id.get()


@contextmanager
def correlation_context(correlation_id: str) -> Iterator[None]:
    """Attach one correlation id to all logs emitted inside the context."""

    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def install_correlation_logging() -> None:
    """Add ``correlation_id`` to every standard log record exactly once."""

    previous_factory = logging.getLogRecordFactory()
    if getattr(previous_factory, _FACTORY_MARKER, False):
        return

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        record.correlation_id = current_correlation_id() or "-"
        return record

    setattr(record_factory, _FACTORY_MARKER, True)
    logging.setLogRecordFactory(record_factory)
