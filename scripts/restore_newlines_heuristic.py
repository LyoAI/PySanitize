#!/usr/bin/env python
"""TEMPORARY heuristic: rebuild line breaks a pre-fix sanitize flattened.

The pre-fix build flattened every ``\\r``/``\\n`` to one space (1:1) on the
detection surface and then wrote that surface back into rewritten JSON leaves
and xlsx cells, so those values lost their line breaks. This script rebuilds
them by rule:

* prose — any run of two or more spaces becomes one line break (the default).
  ``--space-rule pair`` instead reads every pair as one flattened ``\\r\\n``:
  four spaces -> two breaks, three spaces -> one break plus a space. A single
  space is never touched — it cannot be told apart from a real space in the
  text (``2. 互联网``).
* markdown tables — row breaks are rebuilt from the table's own structure: the
  ``| --- | --- |`` delimiter row pins the column count, rows are regrouped by
  that count, and the single spaces between ``|`` that replaced row breaks come
  back as newlines. Empty cells (which also contain runs of spaces) stay inside
  their row, and ``\\|`` is not mistaken for a cell border.

This is a GUESS, not exact recovery. Prefer ``--restore-newlines-from
<original>`` (single file) or ``batch_recover.py --source-root`` (batch) when
the originals still exist — those restore line breaks with certainty. Use this
only when the originals are gone.

Scope is deliberately narrow: only leaves/cells the audit records as rewritten
are touched (the only places a newline can have been lost). A leaf that still
holds an unresolved placeholder is left alone (partial recovery), and a leaf
that already contains a line break is never touched, so repeated runs are safe.

Usage:
    uv run python scripts/restore_newlines_heuristic.py output/a/redacted_recovered.json
    uv run python scripts/restore_newlines_heuristic.py output --dry-run
    uv run python scripts/restore_newlines_heuristic.py output --json-only
    uv run python scripts/restore_newlines_heuristic.py output --space-rule pair
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# A run of spaces on a flattened value. Under the pair rule each pair is one
# flattened ``\r\n``; under ``run`` the whole run is one break.
_SPACES = re.compile(r" {2,}")

# A markdown table delimiter row: | --- | :---: | etc. The cell pattern keeps
# the match exact, so ordinary prose containing pipes does not match.
_CELL = r"[ \t]*:?-{3,}:?[ \t]*"
_DELIM = re.compile(rf"\|(?:{_CELL}\|)+")
_DASHES = re.compile(r"-{3,}")


# ------------------------------------------------------------------ audit map


def _entries(span: dict, key: str) -> list[dict]:
    """The span's ``json``/``cells`` payload as a list (dict or list in audits)."""
    value = span.get(key)
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _json_targets(audit: dict) -> dict[tuple, set[str]]:
    """path → masked values that could still mark the leaf unresolved."""
    out: dict[tuple, set[str]] = {}
    for span in audit.get("masked_spans", []):
        if not span.get("encrypted_value"):
            continue
        masked = span.get("masked_value", "")
        for loc in _entries(span, "json"):
            path = loc.get("path")
            if isinstance(path, (list, tuple)) and path:
                out.setdefault(tuple(path), set()).add(masked)
    return out


def _cell_targets(audit: dict) -> dict[tuple[int, int, int], set[str]]:
    """(sheet_idx, row, col) → masked values that could still mark it unresolved."""
    out: dict[tuple[int, int, int], set[str]] = {}
    for span in audit.get("masked_spans", []):
        if not span.get("encrypted_value"):
            continue
        masked = span.get("masked_value", "")
        for cell in _entries(span, "cells"):
            key = (cell.get("idx", -1), cell["row"], cell["col"])
            out.setdefault(key, set()).add(masked)
    return out


# ------------------------------------------------------------- text rebuilding


def _escaped(text: str, i: int) -> bool:
    """True when the pipe at ``i`` is escaped (``\\|``)."""
    n = 0
    j = i - 1
    while j >= 0 and text[j] == "\\":
        n += 1
        j -= 1
    return n % 2 == 1


def _pipes(text: str, start: int, step: int, count: int) -> list[int] | None:
    """``count`` unescaped-pipe positions from ``start``, walking by ``step``."""
    out: list[int] = []
    i = start
    while 0 <= i < len(text) and len(out) < count:
        if text[i] == "|" and not _escaped(text, i):
            out.append(i)
        i += step
    return out if len(out) == count else None


def _skip_blanks(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _find_tables(text: str) -> list[tuple[int, int, str, str]]:
    """Flattened markdown tables as ``(start, end, rebuilt, prefix)`` spans.

    The delimiter row pins the column count; the header before it and the rows
    after it are rows of ``cols`` cells, i.e. ``cols + 1`` unescaped pipes.
    Regrouping by that count restores row breaks without disturbing the spaces
    that belong to cell padding or empty cells. ``prefix`` is the line break
    that separates the table from preceding text, kept separate so a caller can
    drop it when the previous span already consumed the same gap.
    """
    tables: list[tuple[int, int, str, str]] = []
    pos = 0
    while True:
        m = _DELIM.search(text, pos)
        if m is None:
            break

        cols = len(_DASHES.findall(m.group()))
        j = m.start() - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j < 0 or text[j] != "|":
            pos = m.end()
            continue
        header = _pipes(text, j, -1, cols + 1)
        if header is None:
            pos = m.end()
            continue

        rows = [(header[-1], j + 1), (m.start(), m.end())]
        scan = m.end()
        stopped_on_delim = False
        while True:
            k = _skip_blanks(text, scan)
            if k >= len(text) or text[k] != "|":
                break
            if _DELIM.match(text, k):
                stopped_on_delim = True  # the next table's delimiter row
                break
            row = _pipes(text, k, 1, cols + 1)
            if row is None:
                break
            rows.append((k, row[-1] + 1))
            scan = row[-1] + 1
        if stopped_on_delim and len(rows) > 2:
            rows.pop()  # that row is the next table's header, not ours

        end = rows[-1][1]
        # A gap before/after the table was a line break; restore it as one.
        lead = header[-1]
        b = lead - 1
        while b >= 0 and text[b] in " \t":
            b -= 1
        prefix = ""
        if b >= 0:
            lead = b + 1
            prefix = "\n"
        tail = _skip_blanks(text, end)
        suffix = ""
        if tail < len(text):
            suffix = "\n"
            end = tail

        rebuilt = "\n".join(text[a:b2] for a, b2 in rows) + suffix
        tables.append((lead, end, rebuilt, prefix))
        pos = end
    return tables


def _restore_spaces(segment: str, rule: str) -> str:
    """Rebuild line breaks from runs of spaces in non-table text."""
    if rule == "run":
        return _SPACES.sub("\n", segment)

    def repl(m: re.Match) -> str:
        n = len(m.group())
        return "\n" * (n // 2) + (" " if n % 2 else "")

    return _SPACES.sub(repl, segment)


def _restore_text(text: str, rule: str) -> str | None:
    """The repaired text, or None when it already has line breaks / needs none."""
    if "\n" in text or "\r" in text:
        return None  # already correct, or produced by the fixed redactor
    tables = _find_tables(text)
    if not tables:
        fixed = _restore_spaces(text, rule)
    else:
        parts: list[str] = []
        pos = 0
        for start, end, rebuilt, prefix in tables:
            if end <= pos:  # already covered by an earlier span
                continue
            if start < pos:  # the gap was consumed by the previous table
                start, prefix = pos, ""
            parts.append(_restore_spaces(text[pos:start], rule))
            parts.append(prefix + rebuilt)
            pos = end
        parts.append(_restore_spaces(text[pos:], rule))
        fixed = "".join(parts)
    return fixed if fixed != text else None


def _touch(text: str, masked: set[str], rule: str) -> str | None:
    """The rewritten text, or None when this leaf must be left alone."""
    if any(m and m in text for m in masked):
        return None  # unresolved placeholder — partial restore, do not guess
    return _restore_text(text, rule)


# ----------------------------------------------------------------------- json


def _json_style(raw: str) -> tuple[str | None, str]:
    newline = "\n" if raw.endswith("\n") else ""
    second = raw.split("\n", 1)[1] if "\n" in raw else ""
    indent = None
    if second[:1] in (" ", "\t"):
        indent = second[: len(second) - len(second.lstrip(" \t"))]
    return indent, newline


def _at(data, path: tuple):
    node = data
    for k in path:
        node = node[k]
    return node


def _write_atomic(path: Path, write) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def _fix_json(path: Path, audit: dict, dry_run: bool, rule: str) -> tuple[int, int, int]:
    raw = path.read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    touched = changed = breaks = 0
    for leaf_path, masked in _json_targets(audit).items():
        try:
            node = _at(data, leaf_path)
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(node, str):
            continue
        touched += 1
        new = _touch(node, masked, rule)
        if new is None or new == node:
            continue
        breaks += new.count("\n")
        changed += 1
        parent = data
        for k in leaf_path[:-1]:
            parent = parent[k]
        parent[leaf_path[-1]] = new
    if changed and not dry_run:
        indent, newline = _json_style(raw)

        def write(tmp: Path) -> None:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=indent) + newline,
                encoding="utf-8",
            )

        _write_atomic(path, write)
    return touched, changed, breaks


# ---------------------------------------------------------------------- xlsx


def _fix_xlsx(path: Path, audit: dict, dry_run: bool, rule: str) -> tuple[int, int, int]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=False)
    touched = changed = breaks = 0
    for (idx, row, col), masked in _cell_targets(audit).items():
        if not 0 <= idx < len(wb.worksheets):
            continue
        cell = wb.worksheets[idx].cell(row=row, column=col)
        if not isinstance(cell.value, str):
            continue
        touched += 1
        new = _touch(cell.value, masked, rule)
        if new is None or new == cell.value:
            continue
        breaks += new.count("\n")
        changed += 1
        cell.value = new
    if changed and not dry_run:
        _write_atomic(path, wb.save)
    return touched, changed, breaks


# ---------------------------------------------------------------------- main


def _artifacts(arg: str, json_only: bool = False) -> list[Path]:
    """Explicit files, or recovered JSON/XLSX anywhere under a folder."""
    exts = (".json",) if json_only else (".json", ".xlsx")
    p = Path(arg)
    if p.is_file():
        return [p] if p.suffix.lower() in exts else []
    return sorted(
        q for q in p.rglob("*") if q.is_file()
        and q.suffix.lower() in exts
        and "recovered" in q.stem.lower()
        and not q.stem.lower().endswith("_fixed")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TEMPORARY heuristic: rebuild line breaks flattened by a "
                    "pre-fix sanitize in recovered JSON/XLSX values.",
    )
    parser.add_argument("paths", nargs="+",
                        help="recovered .json/.xlsx files, or folders to scan")
    parser.add_argument("--audit", help="audit.json (default: beside each artifact)")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--json-only", action="store_true",
                        help="repair JSON string leaves only; leave .xlsx cells alone")
    parser.add_argument("--space-rule", choices=("run", "pair"), default="run",
                        help="run: any run of 2+ spaces -> one break (default); "
                             "pair: every 2 spaces -> one break (4 -> 2 breaks, "
                             "3 -> one break + one space)")
    args = parser.parse_args(argv)

    targets = [a for arg in args.paths for a in _artifacts(arg, args.json_only)]
    if not targets:
        print("no recovered .json/.xlsx found (pass files explicitly if named "
              "differently)", file=sys.stderr)
        return 2

    total = [0, 0, 0]
    for path in targets:
        audit_path = Path(args.audit) if args.audit else path.parent / "audit.json"
        if not audit_path.is_file():
            print(f"{path}: no audit.json beside it (or pass --audit) — skipped")
            continue
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"{path}: audit.json is not valid JSON — skipped")
            continue
        if path.suffix.lower() == ".json":
            touched, changed, breaks = _fix_json(path, audit, args.dry_run, args.space_rule)
        else:
            touched, changed, breaks = _fix_xlsx(path, audit, args.dry_run, args.space_rule)
        total[0] += touched
        total[1] += changed
        total[2] += breaks
        mark = "would change" if args.dry_run else "changed"
        print(f"{path}: {mark} {changed}/{touched} rewritten values, "
              f"{breaks} line break(s)")

    verb = "would restore" if args.dry_run else "restored"
    print(f"\n{verb} {total[2]} line break(s) in {total[1]} of {total[0]} "
          f"rewritten values (rule: {args.space_rule})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
