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

    ``start``/``end`` are global char offsets into ``ParsedDocument.text``; the
    masker replaces that span, the M2 renderer maps it back to a page/box.
    """

    field_type: str  # person_name / company_name / phone / ...
    value: str  # the sensitive text exactly as it appears in the document
    start: int
    end: int
    page: int  # 1-based page, derived from the containing block
    source: str = "rules"  # "rules" | "llm"
    confidence: float = 1.0
    bbox: BBox | None = None  # geometry for the M2 renderer
    masked_value: str = ""  # filled by the masker for the audit report


class TextDetector(ABC):
    """Detects sensitive spans in a parsed document's text."""

    @abstractmethod
    def detect(self, doc: "ParsedDocument") -> list[Detection]: ...
