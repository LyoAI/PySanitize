"""TUI smoke tests: the app mounts, tabs exist, panes collect their input."""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Switch, TabbedContent

from pysanitize.tui import PySanitizeApp
from pysanitize.tui.screens import FieldsPane, ImagePane, OptionsPane, RunPane


def _run(coro):
    return asyncio.run(coro)


def test_app_mounts_five_tabs():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            tabs = app.query_one(TabbedContent)
            assert tabs.tab_count == 5
            assert app.query(FieldsPane) and app.query(OptionsPane)
            assert app.query(ImagePane) and app.query(RunPane)

    _run(_test())


def test_fields_pane_lists_enabled_fields_checked():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            fields = app.query_one(FieldsPane)
            selected = fields.selected_fields()
            assert "phone" in selected
            assert "bank_account" not in selected  # disabled by default
            fields._select_all()
            assert "bank_account" in fields.selected_fields()
            fields._deselect_all()
            assert fields.selected_fields() == []

    _run(_test())


def test_options_pane_collects_defaults():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            opts = app.query_one(OptionsPane)
            params = opts.collect()
            assert params["detector"] == "hybrid"  # the pre-checked radio
            assert params["audit"] is None         # switch off → config decides
            assert params["redact_pdf"] is None
            opts.query_one("#redact-pdf", Switch).value = True
            assert opts.collect()["redact_pdf"] is True
            assert opts.file_path() is None        # nothing typed yet

    _run(_test())


def test_image_pane_follows_text_fields_by_default():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            pane = app.query_one(ImagePane)
            params = pane.collect()
            assert params["mask_images"] is None      # not enabled
            assert params["image_classes"] is None    # blank → dropped, config default
            assert params["image_fields"] is None     # null = follow text fields

    _run(_test())


def test_image_pane_splits_classes_into_list():
    """Regression: a raw string is iterated char-by-char by the pipeline."""
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            pane = app.query_one(ImagePane)
            pane.query_one("#image-classes", Input).value = " face, text ,person"
            assert pane.collect()["image_classes"] == ["face", "text", "person"]

    _run(_test())


def test_image_pane_picks_fields_when_not_following():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            pane = app.query_one(ImagePane)
            pane.query_one("#image-follow", Switch).value = False
            assert "phone" in pane.collect()["image_fields"]
            pane._deselect_all()
            assert pane.collect()["image_fields"] == []  # explicit none survives

    _run(_test())


def test_image_pane_all_text_switch_subsumes_fields():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            pane = app.query_one(ImagePane)
            pane.query_one("#image-text-all", Switch).value = True
            params = pane.collect()
            assert "text" in params["image_classes"]   # implicit all-text class
            assert params["image_fields"] == []        # field detection redundant

    _run(_test())


def test_run_pane_ignores_placeholder_requirements():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            run_pane = app.query_one(RunPane)
            assert run_pane.requirements() is None  # only the "#" placeholder

    _run(_test())


def test_quit_button_exits_cleanly():
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            await pilot.click("#quit-btn")
            await pilot.pause()  # let the Button.Pressed message reach its handler
        assert app.return_code == 0  # set once the app has shut down

    _run(_test())


def test_ctrl_c_quits():
    """ctrl+c must quit — several terminals swallow Textual's default ctrl+q."""
    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
        assert app.return_code == 0

    _run(_test())


def test_file_browser_escape_cancels():
    async def _test():
        from pysanitize.tui.app import _FileBrowser

        app = PySanitizeApp()
        async with app.run_test() as pilot:
            app._browse()
            await pilot.pause()
            assert isinstance(app.screen, _FileBrowser)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, _FileBrowser)
            assert app.options_pane.file_path() is None  # cancelled, nothing picked

    _run(_test())
