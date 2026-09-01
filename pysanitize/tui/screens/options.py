"""② Options tab: input file, detection mode, LLM endpoint, images, output."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Select, Static, Switch


class OptionsPane(VerticalScroll):
    """Collects the run parameters; :meth:`collect` shapes them for the pipeline."""

    def compose(self) -> ComposeResult:
        yield Static("Document", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Input(placeholder="/path/to/document.pdf", id="file-input")
            yield Button("Browse…", id="browse", flat=True)
        yield Static(
            "pdf / png / jpg / docx / pptx / xlsx — parsed locally by MinerU",
            classes="hint",
        )

        yield Static("Detection mode", classes="pane-title")
        with RadioSet(id="detector-mode"):
            yield RadioButton("Rules (offline regex + heuristics)", value=False)
            yield RadioButton("LLM only (locate via model)", value=False)
            yield RadioButton("Hybrid (rules + LLM)", value=True)

        yield Static("LLM endpoint", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Label("Model")
            yield Input(placeholder="deepseek-v4-flash", id="llm-model")
        with Horizontal(classes="field-row"):
            yield Label("Provider")
            yield Input(placeholder="openai", id="llm-provider")

        yield Static("Output", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Label("Audit report")
            yield Switch(id="audit")
        with Horizontal(classes="field-row"):
            yield Label("Redacted PDF")
            yield Switch(id="redact-pdf")
        with Horizontal(classes="field-row"):
            yield Label("Out dir")
            yield Input(placeholder="output/<doc-name>/", id="out-dir")

    # -- parameter collection --------------------------------------------------

    def _radio(self, radio_set: RadioSet) -> str:
        label = next(b.label.plain for b in radio_set.query(RadioButton) if b.value)
        return label.split(" ")[0].lower()  # "Hybrid (rules + LLM)" → "hybrid"

    def collect(self) -> dict:
        """User-supplied options only; blanks stay absent so config defaults hold."""
        return {
            "detector": self._radio(self.query_one("#detector-mode", RadioSet)),
            "llm_model": self.query_one("#llm-model", Input).value.strip(),
            "llm_provider": self.query_one("#llm-provider", Input).value.strip(),
            "audit": self.query_one("#audit", Switch).value or None,
            "redact_pdf": self.query_one("#redact-pdf", Switch).value or None,
            "out_dir": self.query_one("#out-dir", Input).value.strip(),
        }

    def file_path(self) -> Path | None:
        raw = self.query_one("#file-input", Input).value.strip()
        return Path(raw).expanduser() if raw else None

    # -- actions ---------------------------------------------------------------

    @on(Button.Pressed, "#browse")
    def _browse(self) -> None:
        """Focus the input with a tilde-expanded home prefix as a head start."""
        inp = self.query_one("#file-input", Input)
        if not inp.value:
            inp.value = str(Path.home() / "")
        inp.focus()
