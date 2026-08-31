"""PySanitize TUI (Textual): tabbed interactive frontend over the pipeline.

Entry: ``pysanitize --launch tui`` → :class:`PySanitizeApp`. Optional extra
(``pip install pysanitize[tui]``); the CLI never imports this package except
behind that flag's lazy-import guard.
"""

from __future__ import annotations

from pysanitize.tui.app import PySanitizeApp

__all__ = ["PySanitizeApp"]
