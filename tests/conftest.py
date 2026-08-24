"""Shared fixtures: build a ``ParsedDocument`` without invoking MinerU."""

from __future__ import annotations

from pathlib import Path

import pytest

from pysanitize.parser.blocks import Block
from pysanitize.parser.document import build_document


@pytest.fixture
def make_doc(tmp_path: Path):
    """Build a ParsedDocument from ``[(block_type, text, page, level?)]`` tuples.

    Meta-type blocks (headers/footers) can be included; they are excluded from
    ``doc.text`` exactly as real MinerU output would be.
    """

    def _make(
        specs: list[tuple[str, str, int] | tuple[str, str, int, int]],
        doc_id: str = "doc",
        suffix: str = ".pdf",
        out_dir: Path | None = None,
    ):
        blocks = []
        for i, spec in enumerate(specs):
            level = spec[3] if len(spec) > 3 else None
            blocks.append(
                Block(
                    block_id=f"b{i}",
                    type=spec[0],
                    text=spec[1],
                    page=spec[2],
                    order=i,
                    level=level,
                )
            )
        parse_dir = out_dir or (tmp_path / "md")
        return build_document(
            doc_id, tmp_path / f"{doc_id}{suffix}", blocks, parse_dir
        )

    return _make
