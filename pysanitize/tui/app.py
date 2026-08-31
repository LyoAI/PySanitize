"""The PySanitize TUI app: a tabbed frontend over ``core.run_sanitizer``.

Five tabs — ① Fields (what to detect), ② Options (document / mode / endpoint /
output), ③ Image (image masking targets), ④ Run (custom LLM requirements +
live log), ⑤ Results (summary). The run itself never blocks the UI:
``sanitize_document`` executes on a ``@work(thread=True)`` worker, the pipeline
log streams into the Run tab via :class:`TuiLogHandler`, and the result lands
on the Results tab.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, TabbedContent, TabPane

from pysanitize import __version__
from pysanitize.core import run_sanitizer
from pysanitize.pipeline import SanitizeResult
from pysanitize.tui.run_worker import attach_log_handler, detach_log_handler, shape_params
from pysanitize.tui.screens import FieldsPane, ImagePane, OptionsPane, ResultsPane, RunPane

_PANE = {
    "fields": "tab-fields",
    "options": "tab-options",
    "run": "tab-run",
    "results": "tab-results",
    "image": "tab-image",
}


class _FileBrowser(ModalScreen[Path | None]):
    """Minimal modal file picker (DirectoryTree); dismisses with the pick or Cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, start: Path) -> None:
        super().__init__()
        self._start = start

    def compose(self) -> ComposeResult:
        yield DirectoryTree(str(self._start), id="tree")
        with Horizontal(classes="button-row"):
            yield Button("Cancel", id="browse-cancel")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path))

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#browse-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class PySanitizeApp(App[None]):
    """Tabbed PySanitize frontend (launch with ``pysanitize --launch tui``)."""

    TITLE = f"🛡️ PySanitize {__version__}"
    SUB_TITLE = "document desensitization"

    BINDINGS = [
        # Textual's default quit is ctrl+q, which several terminals swallow
        # (macOS flow control / VS Code). ctrl+c quits here — except inside
        # Input/TextArea, where the widget's own copy binding wins.
        Binding("ctrl+c", "quit", "Quit"),
    ]

    CSS = """
    #tabs { height: 1fr; }
    TabPane { padding: 1 2; }
    .pane-title { text-style: bold; color: $accent; margin-top: 1; }
    .field-row { height: 3; align-vertical: middle; }
    .field-row Label { width: 14; padding-top: 1; }
    .field-row Input, .field-row Select { width: 1fr; }
    .button-row { height: 3; margin-top: 1; }
    .button-row Button { margin-right: 1; }
    .hint { color: $text-muted; margin-top: 1; }
    #action-bar { height: auto; align-horizontal: right; }
    #quit-btn { margin-right: 1; }
    #requirements { height: 6; border: round $primary; }
    #run-log { height: 1fr; border: round $primary; margin-top: 1; }
    #field-list, #image-field-list { height: auto; max-height: 60vh; border: round $primary; }
    #tree { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="action-bar"):
            yield Button("✕ Quit", id="quit-btn", variant="error")
        with TabbedContent(id="tabs"):
            with TabPane("① Fields", id="tab-fields"):
                yield FieldsPane()
            with TabPane("② Options", id="tab-options"):
                yield OptionsPane()
            with TabPane("③ Image", id="tab-image"):
                yield ImagePane()
            with TabPane("④ Run", id="tab-run"):
                yield RunPane()
            with TabPane("⑤ Results", id="tab-results"):
                yield ResultsPane()
        yield Footer()

    @property
    def fields_pane(self) -> FieldsPane:
        return self.query_one(FieldsPane)

    @property
    def options_pane(self) -> OptionsPane:
        return self.query_one(OptionsPane)

    @property
    def image_pane(self) -> ImagePane:
        return self.query_one(ImagePane)

    @property
    def run_pane(self) -> RunPane:
        return self.query_one(RunPane)

    @property
    def results_pane(self) -> ResultsPane:
        return self.query_one(ResultsPane)

    # -- run orchestration -----------------------------------------------------

    @on(Button.Pressed, "#run-btn")
    def start_run(self) -> None:
        """Validate the panes' input, then hand off to the worker thread."""
        doc = self.options_pane.file_path()
        if doc is None:
            self._error("Pick a document on the ② Options tab first.")
            return
        if not doc.is_file():
            self._error(f"No such file: {doc}")
            return
        fields = self.fields_pane.selected_fields()
        if not fields:
            self._error("Select at least one sensitive field on the ① Fields tab.")
            return

        raw: dict[str, Any] = self.options_pane.collect()
        raw.update(self.image_pane.collect())
        raw.update(doc_path=str(doc), fields=fields)
        extra = self.run_pane.requirements()

        self.run_pane.set_running(True)
        self._switch("run")
        self._log(f"▶ {doc.name} · {raw['detector']} · fields: {', '.join(fields)}")
        self._sanitize_worker(shape_params(raw), extra)

    @work(exclusive=True, thread=True)
    def _sanitize_worker(self, params: dict[str, Any], extra: str | None) -> None:
        handler = attach_log_handler(
            lambda line: self.call_from_thread(self.run_pane.log_line, line)
        )
        try:
            result = run_sanitizer(params.pop("doc_path"), extra_requirements=extra, **params)
            error: Exception | None = None
        except Exception as e:
            result, error = None, e
        finally:
            self.call_from_thread(detach_log_handler, handler)
        self.call_from_thread(self._run_finished, result, error)

    def _run_finished(self, result: SanitizeResult | None, error: Exception | None) -> None:
        self.run_pane.set_running(False)
        if error is not None:
            self._error(f"Run failed: {error}")
            return
        self.results_pane.show(result)
        self._log(f"✓ done in {result.duration_s:.1f}s → {result.out_dir}")
        self._switch("results")

    # -- quit ---------------------------------------------------------------------

    @on(Button.Pressed, "#quit-btn")
    def _quit(self) -> None:
        self.exit()

    # -- small helpers -----------------------------------------------------------

    def _switch(self, pane: str) -> None:
        self.query_one(TabbedContent).active = _PANE[pane]

    def _log(self, line: str) -> None:
        self.run_pane.log_line(line)

    def _error(self, line: str) -> None:
        self.run_pane.log_line(f"✗ {line}")
        self._switch("run")

    # -- file browsing -----------------------------------------------------------

    @on(Button.Pressed, "#browse")
    def _browse(self) -> None:
        def _set(path: Path | None) -> None:
            if path is not None:
                self.options_pane.query_one("#file-input", Input).value = str(path)

        start = self.options_pane.file_path() or Path.home()
        self.push_screen(_FileBrowser(start.parent if start.is_file() else start), _set)
