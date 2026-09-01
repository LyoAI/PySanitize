"""TUI tab panes.

Each module builds one pane of the app's ``TabbedContent`` — "screens" in the
wizard sense (① Fields → ② Options → ③ Image → ④ Run → ⑤ Results, plus ⑥
Recover as the standalone reverse operation), kept as tabs so the user can
move back and forth without losing state.
"""

from __future__ import annotations

from pysanitize.tui.screens.fields import FieldsPane
from pysanitize.tui.screens.image import ImagePane
from pysanitize.tui.screens.options import OptionsPane
from pysanitize.tui.screens.recover import RecoverPane
from pysanitize.tui.screens.results import ResultsPane
from pysanitize.tui.screens.run import RunPane

__all__ = [
    "FieldsPane",
    "ImagePane",
    "OptionsPane",
    "RecoverPane",
    "ResultsPane",
    "RunPane",
]
