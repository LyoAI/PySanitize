"""Native json producer: leaf values → one ``Block`` with :class:`JsonBox` provenance.

Structured data has no layout for MinerU to find, so ``.json`` bypasses it
like ``.xlsx`` does: the file is ``json.loads``-ed and every *leaf* value —
string, number, bool (``null`` renders nothing) — becomes one line of a
block's text, in document (depth-first) order. Each line records a
:class:`~pysanitize.parser.blocks.JsonBox`: the leaf's rendered text, its
traversal path from the root, and the exact char range it occupies, so
detection runs on exactly the strings the redactor writes back and the
offset→leaf mapping is exact by construction.

Keys are deliberately NOT on the detection surface: they are schema, not
data, and masking a key would rename fields. A key echoing a detected value
is reported by the data-level verification instead (see
``pysanitize.redact.json.verify_redaction_json``). A top-level array becomes
one block per element — the block's ``page`` is the element's 1-based
position, so audit "pages" address array elements, and every leaf path
starts at the element's 0-based index (paths are walked from the root by
the redactor and recovery); any other document is a single block. Rendering mirrors the xlsx producer: integral floats render as
ints, strings flatten newlines (a rewritten value loses them, as a rewritten
xlsx cell does), bools render ``true``/``false``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .blocks import Block, JsonBox


def parse_json_blocks(path: Path) -> list[Block]:
    """Parse ``path`` into blocks with per-leaf :class:`JsonBox` provenance.

    Duplicate keys follow ``json.loads`` (last wins) — the redactor emits a
    re-serialization of the loaded view, so that view is the truth for every
    stage (detect, redact, recover).
    """
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    top_list = isinstance(data, list)
    units = data if top_list else [data]
    blocks: list[Block] = []
    for i, unit in enumerate(units):  # page = i + 1: skipped empties never shift it
        # Under a top-level array every path must start at the element's own
        # index — the redactor walks these paths from the ROOT (a list), so a
        # path without the index walks the wrong node (str keys index a list)
        # or, for nested arrays, silently lands in some other element.
        boxes = _leaf_boxes(unit, (i,) if top_list else ())
        if not boxes:
            continue
        text = "\n".join(b.text for b in boxes)
        offset = 0
        placed: list[JsonBox] = []
        for box in boxes:
            placed.append(box._replace(start=offset, end=offset + len(box.text)))
            offset += len(box.text) + 1
        blocks.append(
            Block(block_id="", type="json", text=text, page=i + 1, order=0, json_boxes=placed)
        )
    return blocks


def _leaf_boxes(node, path: tuple["str | int", ...] = ()) -> list[JsonBox]:
    """Depth-first leaf collection; positions are filled by the caller."""
    box = render_scalar(node)
    if box is not None:
        return [JsonBox(box[0], path, 0, 0, box[1])]
    out: list[JsonBox] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_leaf_boxes(v, path + (k,)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_leaf_boxes(v, path + (i,)))
    return out  # null (None) — nothing to detect


def render_scalar(value) -> tuple[str, str] | None:
    """One leaf → ``(rendered_text, kind)``; ``None`` for containers and null.

    Shared by the redactor's data-level verification so a leaf's text means
    the same thing on both sides of the round-trip (an integral float renders
    ``138`` on the surface but serializes ``138.0`` — rendering is the
    contract, ``str()`` is not).
    """
    if isinstance(value, str):
        text = value.replace("\r", " ").replace("\n", " ")
        return (text, "str") if text else None
    if isinstance(value, bool):
        return ("true" if value else "false"), "bool"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value)), "int"
        return str(value), "float"
    return None


def format_path(path: tuple) -> str:
    """Human-readable JSONPath-ish ``$.users[3].phone`` for audit/log output."""
    out = "$"
    for k in path:
        out += f"[{k}]" if isinstance(k, int) else f".{k}"
    return out
