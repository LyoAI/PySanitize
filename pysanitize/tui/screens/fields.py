"""① Fields tab: pick the sensitive field types to detect."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, SelectionList, Static
from textual.widgets.selection_list import Selection

from pysanitize.detector.specs import load_field_specs


class FieldsPane(VerticalScroll):
    """Checkbox list of field types, mirroring ``config/fields.yaml``.

    The pipeline detects the checked types; a type the config has disabled
    (e.g. ``bank_account``) shows unchecked but stays selectable, matching the
    CLI's ``--fields`` semantics of explicitly naming a disabled field.
    """

    def compose(self) -> ComposeResult:
        yield Static("Sensitive fields to detect", classes="pane-title")
        yield SelectionList(id="field-list")
        with Horizontal(classes="button-row"):
            yield Button("Select all", id="fields-all")
            yield Button("Deselect all", id="fields-none")
        yield Static(
            "Enabled-by-default fields are pre-checked. Deselect everything to "
            "be asked for at least one field before running.",
            classes="hint",
        )

    def on_mount(self) -> None:
        sel = self.query_one("#field-list", SelectionList)
        sel.add_options(
            Selection(
                f"{name:<14} {spec.label}",
                value=index,
                initial_state=spec.enabled,
            )
            for index, (name, spec) in enumerate(load_field_specs().items())
        )

    def selected_fields(self) -> list[str]:
        """Names of the checked field types (order follows fields.yaml)."""
        names = list(load_field_specs())
        return [names[i] for i in self.query_one("#field-list", SelectionList).selected]

    @on(Button.Pressed, "#fields-all")
    def _select_all(self) -> None:
        self.query_one("#field-list", SelectionList).select_all()

    @on(Button.Pressed, "#fields-none")
    def _deselect_all(self) -> None:
        self.query_one("#field-list", SelectionList).deselect_all()
