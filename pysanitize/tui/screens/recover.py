"""⑥ Recover tab: restore a sanitized artifact via its audit + passphrase."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static


class RecoverPane(Vertical):
    """The recovery side of the pipeline, independent of the sanitize options.

    Mirrors ``pysanitize <file> --recover``: point at a ``sanitized.md`` /
    ``redacted.pdf`` whose ``audit.json`` sits beside it (or give the path),
    type the passphrase unless the environment or the run's ``.recover.key``
    provides one, and run.
    """

    def compose(self) -> ComposeResult:
        yield Static("Sanitized document", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Input(
                placeholder="output/<doc>/sanitized.md (or redacted.pdf)",
                id="recover-file",
            )
            yield Button("Browse…", id="recover-browse", flat=True)
        yield Static(
            "audit.json must sit beside it (or fill in the path below)",
            classes="hint",
        )

        yield Static("Audit report (optional)", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Input(
                placeholder="default: audit.json next to the document",
                id="recover-audit",
            )

        yield Static("Recovery key (optional)", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Input(
                password=True,
                placeholder="blank: $PY_SANITIZE_RECOVER_KEY / .recover.key",
                id="recover-pass",
            )

        with Horizontal(classes="button-row"):
            yield Button("⟲ Recover", id="recover-btn", variant="success")

    def file_path(self) -> Path | None:
        raw = self.query_one("#recover-file", Input).value.strip()
        return Path(raw).expanduser() if raw else None

    def audit_path(self) -> str | None:
        raw = self.query_one("#recover-audit", Input).value.strip()
        return raw or None

    def passphrase(self) -> str | None:
        raw = self.query_one("#recover-pass", Input).value.strip()
        return raw or None

    def set_running(self, running: bool) -> None:
        self.query_one("#recover-btn", Button).disabled = running

    @on(Button.Pressed, "#recover-browse")
    def _browse_focus(self) -> None:
        """Focus the input; the app owns the file-browser modal."""
        self.query_one("#recover-file", Input).focus()
