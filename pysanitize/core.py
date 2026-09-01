"""Frontend-agnostic entry facade over the sanitize pipeline.

Interactive frontends (TUI today, WebUI later) must not import pipeline
internals: they collect parameters from the user and hand them to
:func:`run_sanitizer`, which owns the one extra concern a UI has beyond the
plain CLI — free-text ``extra_requirements`` that extend the LLM system
prompt (see ``pysanitize.prompts``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pysanitize.pipeline import SanitizeResult, sanitize_document
from pysanitize.prompts import set_extra_requirements

__all__ = ["run_sanitizer", "SanitizeResult"]


def run_sanitizer(
    doc_path: str | Path,
    *,
    extra_requirements: str | None = None,
    skip_existing: bool = True,
    **params: Any,
) -> SanitizeResult:
    """Run one sanitize job with UI-supplied parameters.

    Args:
        doc_path: PDF / image / docx / pptx / xlsx file.
        extra_requirements: free-text detection requirements from the user,
            appended to the LLM system prompt for this run only (cleared in a
            ``finally`` so it never leaks into later runs).
        skip_existing: reuse cached MinerU parse output (pass ``False`` to
            force a re-parse after the user changes parse settings).
        **params: forwarded verbatim to
            :func:`pysanitize.pipeline.sanitize_document` — ``detector``,
            ``fields``, ``llm_model``, ``llm_provider``, ``mask_images``,
            ``image_classes``, ``image_fields``, ``image_backend``,
            ``image_model_path``, ``score_threshold``, ``mosaic_factor``,
            ``redact_pdf``, ``redaction_style``, ``audit``, ``recoverable``,
            ``recover_key``, ``out_dir``, ``mineru_backend``,
            ``mineru_out_dir``, ``lang``.

    Returns:
        :class:`pysanitize.pipeline.SanitizeResult`.
    """
    set_extra_requirements(extra_requirements)
    try:
        return sanitize_document(doc_path, skip_existing=skip_existing, **params)
    finally:
        set_extra_requirements(None)
