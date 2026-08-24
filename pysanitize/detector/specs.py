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
        keep = len(value) - self.keep_head - self.keep_tail
        return (
            value[: self.keep_head]
            + self.mask_char * max(0, keep)
            + (value[-self.keep_tail:] if self.keep_tail else "")
        )


@dataclass
class FieldSpec:
    """One configurable sensitive field."""

    name: str
    label_zh: str
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


def load_field_specs(path: Path | str = FIELDS_CONFIG) -> dict[str, FieldSpec]:
    """Load field specs from a YAML file (see ``config/fields.yaml``)."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
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
            label_zh=raw.get("label_zh", name),
            pattern=raw.get("pattern", ""),
            mask=mask,
            confidence=float(raw.get("confidence", 1.0)),
            enabled=bool(raw.get("enabled", True)),
            heuristic=raw.get("heuristic", ""),
        )
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
