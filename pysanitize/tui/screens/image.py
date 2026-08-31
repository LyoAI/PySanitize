"""③ Image tab: image masking targets — class-driven + field-driven."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, SelectionList, Select, Static, Switch
from textual.widgets.selection_list import Selection

from pysanitize.detector.specs import load_field_specs


class ImagePane(VerticalScroll):
    """Image masking controls.

    Two complementary targets:

    - **classes** (``face`` / ``text`` / a YOLO class) — mask whatever the
      class detectors find;
    - **fields** — mask only the sensitive *fields* OCR finds in the image text
      (company names on logos/seals, registered addresses, …). Defaults to the
      same field set as the ① Fields tab; flip "Same as text" off to pick a
      different (possibly larger) subset.
    """

    def compose(self) -> ComposeResult:
        yield Static("Image masking", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Label("Enable")
            yield Switch(id="image-mask")
        with Horizontal(classes="field-row"):
            yield Label("Classes")
            yield Input(placeholder="face, text, person, …", id="image-classes")
        with Horizontal(classes="field-row"):
            yield Label("Face backend")
            yield Select(
                options=[("auto", "auto"), ("yunet", "yunet"), ("haar", "haar"), ("yolo", "yolo")],
                value="auto",
                id="image-backend",
                allow_blank=False,
            )

        yield Static("Sensitive fields in images", classes="pane-title")
        with Horizontal(classes="field-row"):
            yield Label("Same as text")
            yield Switch(id="image-follow", value=True)
        yield SelectionList(id="image-field-list")
        with Horizontal(classes="button-row"):
            yield Button("Select all", id="image-fields-all")
            yield Button("Deselect all", id="image-fields-none")
        yield Static(
            "On = use the ① Fields selection; off = pick image-specific fields "
            "below (e.g. company_name for logos/seals). Empty + off = no "
            "field-driven image masking.",
            classes="hint",
        )

    def on_mount(self) -> None:
        sel = self.query_one("#image-field-list", SelectionList)
        sel.add_options(
            Selection(
                f"{name:<14} {spec.label}",
                value=index,
                initial_state=spec.enabled,
            )
            for index, (name, spec) in enumerate(load_field_specs().items())
        )

    def selected_fields(self) -> list[str]:
        """Checked field-type names (order follows fields.yaml)."""
        names = list(load_field_specs())
        return [
            names[i]
            for i in self.query_one("#image-field-list", SelectionList).selected
        ]

    def collect(self) -> dict:
        """User-supplied options only; blanks/None stay absent so config defaults hold."""
        follow = self.query_one("#image-follow", Switch).value
        return {
            "mask_images": self.query_one("#image-mask", Switch).value or None,
            # Comma-separated input → list; blank → None (config default).
            # The pipeline expects list[str] — a raw string would be iterated
            # character by character ("face" → f/a/c/e).
            "image_classes": _split_classes(
                self.query_one("#image-classes", Input).value
            ),
            "image_backend": self.query_one("#image-backend", Select).value,
            # None = follow the text fields; [] = explicitly none
            "image_fields": None if follow else self.selected_fields(),
        }

    @on(Button.Pressed, "#image-fields-all")
    def _select_all(self) -> None:
        self.query_one("#image-field-list", SelectionList).select_all()

    @on(Button.Pressed, "#image-fields-none")
    def _deselect_all(self) -> None:
        self.query_one("#image-field-list", SelectionList).deselect_all()


def _split_classes(raw: str) -> list[str] | None:
    """Comma-separated classes → clean list (None when blank → config default)."""
    raw = raw.strip()
    if not raw:
        return None
    return [c.strip() for c in raw.split(",") if c.strip()]
