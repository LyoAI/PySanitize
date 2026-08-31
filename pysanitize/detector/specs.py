"""Field specifications: what to detect and how to mask it (config/fields.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pysanitize.config import FIELDS_CONFIG
from pysanitize.utils import get_logger

logger = get_logger()


@dataclass
class MaskSpec:
    """How a field's value is replaced.

    Either a fixed ``template`` (used verbatim) or a partial mask built from
    ``keep_head`` / ``keep_tail`` / ``mask_char``. Fixed-length output keeps
    markdown tables aligned.
    """

    template: str = ""
    keep_head: int = 0
    keep_tail: int = 0
    mask_char: str = "*"

    def mask(self, value: str) -> str:
        """Return the replacement string for ``value``."""
        if self.template:
            return self.template
        if len(value) <= self.keep_head + self.keep_tail:
            # head+tail would expose the whole value — never emit it unmasked.
            # Keep up to ``keep_head`` chars, masking at least one char.
            keep = min(self.keep_head, max(0, len(value) - 1))
            return value[:keep] + self.mask_char * (len(value) - keep)
        return (
            value[: self.keep_head]
            + self.mask_char * (len(value) - self.keep_head - self.keep_tail)
            + value[-self.keep_tail:]
        )


@dataclass
class FieldSpec:
    """One configurable sensitive field."""

    name: str
    label: str  # human-readable description shown to the LLM (English)
    pattern: str  # regex source; empty for heuristic-only fields
    mask: MaskSpec
    confidence: float = 1.0
    enabled: bool = True
    heuristic: str = ""  # checksum_id / checksum_uscc / surname_context / company_suffix
    compiled: re.Pattern[str] | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if self.pattern:
            try:
                self.compiled = re.compile(self.pattern)
            except re.error as e:
                logger.error("bad regex for field %r: %s", self.name, e)
                self.compiled = None


# Built-in field specs used when ``config/fields.yaml`` is absent (fresh clone /
# CI has no ``config/``). Mirrors the shipped fields.yaml so a machine without a
# local config behaves identically to one that has it.
DEFAULT_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "phone": {
        "label": "phone number",
        "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "confidence": 1.0,
        "mask": {"keep_head": 3, "keep_tail": 4, "mask_char": "*"},
    },
    "id_card": {
        "label": "national ID number",
        "pattern": r"(?<![0-9A-Za-z])[1-9]\d{16}[\dXx](?![0-9A-Za-z])",
        "heuristic": "checksum_id",
        "confidence": 1.0,
        "mask": {"keep_head": 6, "keep_tail": 4, "mask_char": "*"},
    },
    "email": {
        "label": "email address",
        "pattern": r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])",
        "confidence": 1.0,
        "mask": {"template": "****@***"},
    },
    "credit_code": {
        "label": "unified social credit code",
        "pattern": r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Za-z])",
        "heuristic": "checksum_uscc",
        "confidence": 1.0,
        "mask": {"keep_head": 0, "keep_tail": 4, "mask_char": "*"},
    },
    "stock_code": {
        "label": "stock code",
        "pattern": r"(?<!\d)(?:60\d{4}|68\d{4}|00\d{4}|30\d{4})(?!\d)",
        "confidence": 0.8,
        "mask": {"template": "******"},
    },
    "bank_account": {
        "label": "bank account number",
        "pattern": r"(?<!\d)\d{16,19}(?!\d)",
        "confidence": 0.5,
        "enabled": False,
        "mask": {"keep_head": 4, "keep_tail": 4, "mask_char": "*"},
    },
    "person_name": {
        "label": "person name",
        "pattern": "",
        "heuristic": "surname_context",
        "confidence": 0.9,
        "mask": {"template": "***"},
    },
    "company_name": {
        "label": "company name",
        "pattern": "",
        "heuristic": "company_suffix",
        "confidence": 0.9,
        "mask": {"template": "****"},
    },
}


def _build_specs(data: dict[str, Any]) -> dict[str, FieldSpec]:
    specs: dict[str, FieldSpec] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            continue
        mask_raw = raw.get("mask") or {}
        if isinstance(mask_raw, dict):
            mask = MaskSpec(**mask_raw)
        else:
            mask = MaskSpec(template=str(mask_raw))
        specs[name] = FieldSpec(
            name=name,
            label=raw.get("label", name),
            pattern=raw.get("pattern", ""),
            mask=mask,
            confidence=float(raw.get("confidence", 1.0)),
            enabled=bool(raw.get("enabled", True)),
            heuristic=raw.get("heuristic", ""),
        )
    return specs


def load_field_specs(path: Path | str = FIELDS_CONFIG) -> dict[str, FieldSpec]:
    """Load field specs from ``config/fields.yaml``, falling back to the
    built-in :data:`DEFAULT_FIELD_SPECS` when the file is absent."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = DEFAULT_FIELD_SPECS
        path = "<built-in defaults>"
    specs = _build_specs(data)
    logger.debug("loaded %d field specs from %s", len(specs), path)
    return specs


def select_specs(
    specs: dict[str, FieldSpec], fields: list[str] | None = None
) -> dict[str, FieldSpec]:
    """Filter to the named ``fields`` (or all enabled ones when None).

    Explicitly naming a disabled field (e.g. ``bank_account``) re-enables it.
    """
    if fields is None:
        return {n: s for n, s in specs.items() if s.enabled}
    wanted = set(fields)
    unknown = wanted - set(specs)
    if unknown:
        raise ValueError(f"unknown field type(s): {', '.join(sorted(unknown))}")
    return {n: s for n, s in specs.items() if n in wanted}
