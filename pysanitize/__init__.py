"""PySanitize — multi-format document desensitization tool.

Desensitization pipeline for PDF / DOCX / Excel / scanned documents:
MinerU parse -> rules / LLM locate sensitive fields -> text masking + image
face mosaicing.
"""

__version__ = "0.2.0"
