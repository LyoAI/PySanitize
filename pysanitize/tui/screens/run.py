"""④ Run tab: custom LLM requirements, the run button, live progress log."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, RichLog, Static, TextArea


class RunPane(Vertical):
    """Free-text requirements + run trigger + the streaming pipeline log."""

    def compose(self) -> ComposeResult:
        yield Static(
            "Custom requirements (free text, appended to the LLM prompt)",
            classes="pane-title",
        )
        yield TextArea(
            "# e.g. Also locate contract numbers like HT-2024-####",
            id="requirements",
        )
        with Horizontal(classes="button-row"):
            yield Button("▶ Run desensitization", id="run-btn", variant="success")
            yield Button("Clear log", id="clear-log")
        yield RichLog(id="run-log", markup=False, wrap=True)

    def requirements(self) -> str | None:
        text = self.query_one("#requirements", TextArea).text.strip()
        if not text or text.startswith("#"):
            return None
        return text

    def log_line(self, line: str) -> None:
        """Thread-safe via the app's ``call_from_thread``; appends one line."""
        self.query_one("#run-log", RichLog).write(line)

    def set_running(self, running: bool) -> None:
        self.query_one("#run-btn", Button).disabled = running

    @on(Button.Pressed, "#clear-log")
    def _clear(self) -> None:
        self.query_one("#run-log", RichLog).clear()
