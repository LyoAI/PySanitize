"""Audit artifacts for one sanitize run.

``audit.json`` is always written — a public, raw-free summary (masked spans
only). ``sensitive_report.json`` is opt-in (``--audit``) and carries raw values
+ offsets for local review — never ship it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pysanitize import __version__
from pysanitize.detector.base import Detection


def _count_by_field(detections: list[Detection]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in detections:
        counts[d.field_type] = counts.get(d.field_type, 0) + 1
    return counts


@dataclass
class AuditInfo:
    """Everything the reporters need about one sanitize run."""

    doc_id: str
    source: str
    detector: str
    fields: list[str]
    pages: int
    blocks: int
    text_chars: int
    images_total: int
    images_masked: int
    detections: list[Detection]
    duration_s: float
    started_at: str = ""
    version: str = __version__
    redacted_pdf: str | None = None  # filename under out_dir, or None
    redacted_pages: int = 0
    redaction_regions: int = 0


def write_audit(info: AuditInfo, out_dir: Path) -> Path:
    """Write ``audit.json`` — the public, raw-free summary."""
    audit = {
        "version": info.version,
        "doc": {
            "id": info.doc_id,
            "source": info.source,
            "pages": info.pages,
            "blocks": info.blocks,
            "text_chars": info.text_chars,
        },
        "detector": info.detector,
        "fields": info.fields,
        "findings": {
            "total": len(info.detections),
            "by_field": _count_by_field(info.detections),
        },
        "images": {"total": info.images_total, "masked": info.images_masked},
        "redaction": {
            "pdf": info.redacted_pdf,
            "pages": info.redacted_pages,
            "regions": info.redaction_regions,
        },
        # Only masked values appear here — the raw spans go to the opt-in
        # sensitive_report.json.
        "masked_spans": [
            {
                "field_type": d.field_type,
                "page": d.page,
                "source": d.source,
                "masked_value": d.masked_value,
            }
            for d in info.detections
        ],
        "timing": {
            "started_at": info.started_at,
            "duration_s": round(info.duration_s, 3),
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "audit.json"
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_sensitive_report(info: AuditInfo, out_dir: Path) -> Path:
    """Write ``sensitive_report.json`` — raw values + offsets, local review only."""
    payload = {
        "version": info.version,
        "doc_id": info.doc_id,
        "detections": [
            {
                "field_type": d.field_type,
                "value": d.value,
                "start": d.start,
                "end": d.end,
                "page": d.page,
                "source": d.source,
                "confidence": d.confidence,
                "masked_value": d.masked_value,
            }
            for d in info.detections
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "sensitive_report.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
