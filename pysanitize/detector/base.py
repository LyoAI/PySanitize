"""Detection data model and text-detector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pysanitize.parser.blocks import BBox
    from pysanitize.parser.document import ParsedDocument


@dataclass
class Detection:
    """A located sensitive field in the document text.

    ``start``/``end`` are global char offsets into ``ParsedDocument.text`` that
    the masker replaces.
    """

    field_type: str
    value: str  # the sensitive text exactly as it appears in the document
    start: int
    end: int
    page: int  # 1-based, from the containing block
    source: str = "rules"
    confidence: float = 1.0
    bbox: BBox | None = None
    masked_value: str = ""  # filled by the masker for the audit report


class TextDetector(ABC):
    """Detects sensitive spans in a parsed document's text."""

    @abstractmethod
    def detect(self, doc: "ParsedDocument") -> list[Detection]: ...
