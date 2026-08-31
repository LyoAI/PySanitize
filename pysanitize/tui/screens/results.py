"""⑤ Results tab: post-run summary (counts, images, artifacts)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from pysanitize.pipeline import SanitizeResult


class ResultsPane(VerticalScroll):
    """Read-only summary of the last ``SanitizeResult``."""

    def compose(self) -> ComposeResult:
        yield Static("No run yet — configure Options and press Run.", id="results-body")

    def show(self, result: SanitizeResult) -> None:
        by_field: dict[str, int] = {}
        for d in result.detections:
            by_field[d.field_type] = by_field.get(d.field_type, 0) + 1
        rows = "\n".join(
            f"  [b]{name}[/b] × {count}" for name, count in sorted(by_field.items())
        )
        audit = (
            "sensitive_report.json (raw values — keep local)"
            if result.sensitive_report_path
            else "audit.json (masked summary)"
        )
        redacted = (
            f"  redacted.pdf\n"
            if result.redacted_pdf
            else ""
        )
        self.query_one("#results-body", Static).update(
            f"[b]Document[/b]  {result.doc_id}\n"
            f"[b]Detector[/b]  {result.detector}   "
            f"[b]Duration[/b]  {result.duration_s:.1f}s\n\n"
            f"[b]Text findings[/b]  {len(result.detections)}\n"
            f"{rows or '  (none)'}\n\n"
            f"[b]Images[/b]  {result.images_masked}/{result.images_total} mosaiced\n\n"
            f"[b]Output[/b]  {result.out_dir}\n"
            f"  sanitized.md\n"
            f"{redacted}"
            f"  {audit}"
        )
