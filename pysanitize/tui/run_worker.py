"""Background plumbing for the TUI: log capture and parameter shaping.

``sanitize_document`` is synchronous and long-running; Textual's UI loop must
never block on it. The app runs it on a ``@work(thread=True)`` worker (see
``app.py``) while a logging handler forwards the pipeline's log stream into
the Run tab's ``RichLog`` — live progress without touching pipeline internals.
"""

from __future__ import annotations

import logging
from typing import Any


class TuiLogHandler(logging.Handler):
    """Forwards ``PySanitize`` log records into a RichLog via the UI thread."""

    def __init__(self, write_line) -> None:
        super().__init__()
        self._write_line = write_line  # thread-safe callable(str)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._write_line(self.format(record))
        except Exception:  # never let logging break the worker
            self.handleError(record)


def attach_log_handler(write_line) -> TuiLogHandler:
    """Install the capture handler on the pipeline logger (caller removes it)."""
    handler = TuiLogHandler(write_line)
    handler.setFormatter(
        logging.Formatter("%(asctime)s │ %(levelname)-7s %(message)s", "%H:%M:%S")
    )
    logging.getLogger("PySanitize").addHandler(handler)
    return handler


def detach_log_handler(handler: TuiLogHandler) -> None:
    logging.getLogger("PySanitize").removeHandler(handler)


def shape_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop None/blank values so pipeline-config defaults apply untouched.

    Empty *lists* are kept — e.g. an explicit ``image_fields=[]`` from the
    Image tab means "no image field detection", which must not fall back to the
    text-field default.
    """
    return {k: v for k, v in raw.items() if v is not None and v != ""}
