"""PDF layout redaction (the M2 renderer)."""

from __future__ import annotations

from .pdf import Redaction, redact_pdf, resolve_rects, verify_redaction

__all__ = ["Redaction", "redact_pdf", "resolve_rects", "verify_redaction"]
