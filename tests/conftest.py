"""Shared fixtures: build a ``ParsedDocument`` without invoking MinerU."""

from __future__ import annotations

from pathlib import Path

import pytest

from pysanitize.parser.blocks import Block
from pysanitize.parser.document import build_document


@pytest.fixture
def make_doc(tmp_path: Path):
    """Build a ParsedDocument from ``[(block_type, text, page)]`` tuples.

    Meta-type blocks (headers/footers) can be included; they are excluded from
    ``doc.text`` exactly as real MinerU output would be.
    """

    def _make(
        specs: list[tuple[str, str, int]],
        doc_id: str = "doc",
        suffix: str = ".pdf",
        out_dir: Path | None = None,
    ):
        blocks = [
            Block(block_id=f"b{i}", type=bt, text=tx, page=pg, order=i)
            for i, (bt, tx, pg) in enumerate(specs)
        ]
        parse_dir = out_dir or (tmp_path / "md")
        return build_document(
            doc_id, tmp_path / f"{doc_id}{suffix}", blocks, parse_dir
        )

    return _make
