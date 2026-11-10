"""Restore originals from sanitized artifacts (``sanitized.md`` / ``redacted.*``).

Pure consumers: they read the artifact + its ``audit.json`` + a passphrase and
write ``<stem>_recovered.<ext>`` beside the input. The sanitize pipeline is
never imported — recovery only needs the audit's ``recovery`` block and the
per-span data recorded next to each placeholder (``encrypted_value`` + ``md``
range + ``rects`` + ``cells`` / ``json`` positions).

Markdown recovery is exact: each span records where its placeholder sits, so
restoration splices the decrypted value back at that range (verified against
the placeholder text). XLSX and JSON recovery are exact too, and positional:
each span records the cell ``(sheet, row, col)`` / leaf path it touched plus
where its placeholder sits inside that cell/leaf's masked value, so
restoration splices by recorded position — a cell or string holding several
look-alike placeholders (two masked names both ``***``) still restores each
detection to its own spot, and the original python/JSON type comes back for
whole-value numeric/date/bool masks. PDF recovery is best-effort: the
original glyphs were truly *deleted* at sanitize time, so recovery clears the
redacted region and re-inserts the decrypted text with a fitted font — the
values come back, the original layout does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pysanitize.utils.splice import apply_repls  # pure string helper, no pipeline
from pysanitize.recover.crypto import (
    KEYFILE_NAME,
    TokenCipher,
    derive_key,
    obtain_passphrase,
)

AUDIT_NAME = "audit.json"


@dataclass
class RecoverResult:
    """Outcome of one :func:`recover_file` call."""

    output: Path
    restored: int  # placeholders replaced with plaintext
    unresolved: int  # audit spans missing from the artifact / undecryptable
    kind: str  # "markdown" | "pdf" | "xlsx" | "json"


def load_audit(input_path: Path, audit_path: Path | None = None) -> dict:
    """Read the run's audit (default: ``audit.json`` beside the input)."""
    path = Path(audit_path) if audit_path else input_path.parent / AUDIT_NAME
    if not path.is_file():
        raise FileNotFoundError(f"no audit report at {path}; recovery needs it")
    return json.loads(path.read_text(encoding="utf-8"))


def cipher_from_audit(audit: dict, passphrase: str) -> TokenCipher:
    """Rebuild the cipher from the audit's public ``recovery`` parameters."""
    rec = audit.get("recovery")
    if not rec or not rec.get("enabled"):
        raise ValueError(
            "audit.json has no recovery block — this run was not sanitized "
            "with recoverable masking"
        )
    salt = rec["kdf_salt"]
    return TokenCipher(
        derive_key(passphrase, salt, rec.get("kdf_params")), salt, rec.get("kdf_params")
    )


def audit_spans(audit: dict) -> list[dict]:
    """Recoverable ``masked_spans`` — those carrying a ciphertext."""
    return [s for s in audit.get("masked_spans", []) if s.get("encrypted_value")]


def recover_file(
    input_path: str | Path,
    *,
    audit_path: str | Path | None = None,
    passphrase: str | None = None,
    out_path: str | Path | None = None,
) -> RecoverResult:
    """Recover one sanitized artifact. Dispatches on the file suffix."""
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"no such document: {input_path}")
    audit = load_audit(input_path, audit_path)
    keyfile = Path(audit_path or input_path.parent / AUDIT_NAME).parent / KEYFILE_NAME
    secret, _ = obtain_passphrase(passphrase, keyfile, allow_generate=False)
    cipher = cipher_from_audit(audit, secret)

    if input_path.suffix.lower() == ".pdf":
        return _recover_pdf(input_path, cipher, audit, out_path)
    if input_path.suffix.lower() == ".xlsx":
        return _recover_xlsx(input_path, cipher, audit, out_path)
    if input_path.suffix.lower() == ".json":
        return _recover_json(input_path, cipher, audit, out_path)
    return _recover_markdown(input_path, cipher, audit, out_path)


def _target_for(input_path: Path, out_path) -> Path:
    if out_path:
        return Path(out_path)
    return input_path.with_name(f"{input_path.stem}_recovered{input_path.suffix}")


def _recover_markdown(md_path: Path, cipher: TokenCipher, audit: dict, out_path) -> RecoverResult:
    text = md_path.read_text(encoding="utf-8")
    spans = [s for s in audit_spans(audit) if "md" in s]
    restored = 0
    # One ascending pass, joined at the end — splicing the whole document per
    # span was quadratic in (spans × document length), minutes on a large pdf.
    parts: list[str] = []
    prev = 0
    for span in sorted(spans, key=lambda s: s["md"][0]):
        start, end = span["md"]
        if start < prev:
            continue  # overlapping/corrupt range — never re-splice text twice
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:  # wrong key / tampered ciphertext
            continue
        if text[start:end] != span.get("masked_value"):
            continue  # placeholder gone — the file was edited after sanitizing
        parts.append(text[prev:start])
        parts.append(value)
        prev = end
        restored += 1
    parts.append(text[prev:])
    text = "".join(parts)
    target = _target_for(md_path, out_path)
    target.write_text(text, encoding="utf-8")
    return RecoverResult(target, restored, len(audit_spans(audit)) - restored, "markdown")


def _recover_pdf(pdf_path: Path, cipher: TokenCipher, audit: dict, out_path) -> RecoverResult:
    import pymupdf  # AGPL — the only writer, same as the redact stage

    spans = audit_spans(audit)
    placed = [s for s in spans if s.get("rects")]
    doc = pymupdf.open(pdf_path)
    restored = 0
    by_page: dict[int, list[dict]] = {}
    for span in placed:
        by_page.setdefault(int(span.get("page", 1)) - 1, []).append(span)
    for page_idx, page in enumerate(doc):
        page_spans = by_page.get(page_idx)
        if not page_spans:
            continue
        values: list[str | None] = []
        for span in page_spans:
            try:
                values.append(cipher.decrypt_token(span["encrypted_value"]))
            except Exception:  # wrong key / tampered ciphertext — leave redacted
                values.append(None)
        # Wipe the redacted regions (mosaic/box pixels included)…
        for span, value in zip(page_spans, values):
            if value is None:
                continue
            for r in span["rects"]:
                page.add_redact_annot(pymupdf.Rect(*r))
        page.apply_redactions(images=2, graphics=0, text=0)
        # …then write each decrypted value back. Values that share a rect (a
        # table's cells all map to the whole-block bbox) are stacked top-down
        # in reading order instead of overwriting each other.
        restored += _insert_values(page, page_spans, values)
    target = _target_for(pdf_path, out_path)
    doc.save(target)
    unresolved = len(spans) - restored
    return RecoverResult(target, restored, unresolved, "pdf")


def _recover_xlsx(xlsx_path: Path, cipher: TokenCipher, audit: dict, out_path) -> RecoverResult:
    """Restore original cell values in a ``redacted.xlsx`` — exact, per cell.

    Each recoverable span records the cells its detection touched (sheet index
    + row/col + type tag) and, since the offset upgrade, where its placeholder
    sits inside the masked cell value. Restoration splices by that recorded
    position — a cell holding several look-alike placeholders (two masked
    names both ``***``) puts each decrypted value back at its own spot;
    string matching could not tell them apart. Spans from audits recorded
    before the upgrade (no positions) fall back to left-to-right matching.
    A span whose placeholder no longer sits where it was recorded counts
    unresolved — the file was edited after sanitizing. Whole-cell numeric/
    date masks come back with their original python type, not as strings.
    """
    import datetime as dt

    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, data_only=False)
    spans = audit_spans(audit)
    placed = [s for s in spans if s.get("cells")]
    # Cells with a single span that covered the whole cell: restore the type.
    per_cell: dict[tuple, list[int]] = {}
    # Offset splices grouped per cell: every span's edit for one cell applies
    # together, highest position first, so recorded ranges stay valid and
    # look-alike placeholders can never cross-restore.
    by_cell: dict[tuple, list[tuple[int, int, str, str, int]]] = {}
    for i, span in enumerate(placed):
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:  # wrong key / tampered ciphertext
            continue
        masked = span.get("masked_value", "")
        for cell in span["cells"]:
            key = (cell.get("idx", -1), cell["row"], cell["col"])
            per_cell.setdefault(key, []).append(i)
            ms, me = cell.get("masked_start"), cell.get("masked_end")
            if ms is not None and me is not None:
                by_cell.setdefault(key, []).append((ms, me, value, masked, i))

    ok_spans: set[int] = set()
    for (idx, row, col), items in by_cell.items():
        if not 0 <= idx < len(wb.worksheets):
            continue
        ws = wb.worksheets[idx]
        current = ws.cell(row=row, column=col).value
        text = "" if current is None else str(current)
        if any(not 0 <= ms <= me <= len(text) or text[ms:me] != masked
               for ms, me, _, masked, _ in items):
            continue  # placeholder moved — the file was edited after sanitizing
        text = apply_repls(text, items)
        ws.cell(row=row, column=col).value = text
        ok_spans.update(i for _, _, _, _, i in items)

    # Legacy audits (pre-offset): match the placeholder text left to right.
    for i, span in enumerate(placed):
        if i in ok_spans:
            continue
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:
            continue
        masked = span.get("masked_value", "")
        for cell in span["cells"]:
            if cell.get("masked_start") is not None:
                continue
            idx, row, col = cell.get("idx", -1), cell["row"], cell["col"]
            if not 0 <= idx < len(wb.worksheets):
                continue
            ws = wb.worksheets[idx]
            current = ws.cell(row=row, column=col).value
            text = "" if current is None else str(current)
            if masked and masked in text:
                ws.cell(row=row, column=col).value = text.replace(masked, value, 1)
                ok_spans.add(i)

    # Type restore: one span, one cell, current value == decrypted value.
    for (idx, row, col), span_idxs in per_cell.items():
        if len(set(span_idxs)) != 1 or not 0 <= idx < len(wb.worksheets):
            continue
        span = placed[span_idxs[0]]
        key_cell = next(
            (c for c in span["cells"] if (c.get("idx", -1), c["row"], c["col"]) == (idx, row, col)),
            {},
        )
        kind = key_cell.get("kind", "str")
        if kind in ("str", "formula"):
            continue
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:
            continue
        cell = wb.worksheets[idx].cell(row=row, column=col)
        if cell.value != value:
            continue
        try:
            if kind == "int":
                cell.value = int(value)
            elif kind == "float":
                cell.value = float(value)
            elif kind == "bool":
                cell.value = value == "TRUE"
            elif kind == "date":
                cell.value = dt.date.fromisoformat(value)
            elif kind == "datetime":
                cell.value = dt.datetime.fromisoformat(value)
            elif kind == "time":
                cell.value = dt.time.fromisoformat(value)
        except ValueError:  # keep the string when it no longer parses
            continue
    target = _target_for(xlsx_path, out_path)
    wb.save(target)
    unresolved = len(placed) - len(ok_spans)
    return RecoverResult(target, len(ok_spans), unresolved, "xlsx")


def _recover_json(json_path: Path, cipher: TokenCipher, audit: dict, out_path) -> RecoverResult:
    """Restore original leaf values in a ``redacted.json`` — exact, by position.

    Each recoverable span records the leaf's traversal path plus where its
    placeholder sits inside the leaf's masked value, so a string holding
    several look-alike placeholders restores each detection to its own spot
    (splice highest position first; every slice is verified against the
    recorded placeholder before anything is written). Whole-leaf number/bool
    masks come back with their JSON type, not as strings. Output formatting
    follows the redacted file the same way redaction followed the source.
    """
    raw = json_path.read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    spans = audit_spans(audit)
    placed = [s for s in spans if s.get("json")]
    per_leaf: dict[tuple, list[tuple[int, int, str, str, int]]] = {}
    for i, span in enumerate(placed):
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:  # wrong key / tampered ciphertext
            continue
        loc = span["json"]
        per_leaf.setdefault(tuple(loc["path"]), []).append(
            (loc["masked_start"], loc["masked_end"], value, span.get("masked_value", ""), i)
        )

    ok_spans: set[int] = set()
    for path, items in per_leaf.items():
        try:
            node = data
            for k in path:
                node = node[k]
            parent = data
            for k in path[:-1]:
                parent = parent[k]
        except (KeyError, IndexError, TypeError):
            continue  # structure changed after sanitizing
        if not isinstance(node, str) or isinstance(parent, (str, int, float, bool)):
            continue
        if any(not 0 <= ms <= me <= len(node) or node[ms:me] != masked
               for ms, me, _, masked, _ in items):
            continue  # placeholder moved — the file was edited after sanitizing
        text = apply_repls(node, items)
        # Whole-leaf number/bool masks come back with their JSON type.
        if len(items) == 1:
            kind = placed[items[0][4]]["json"].get("kind", "str")
            value = items[0][2]
            if kind == "int" and text == value:
                try:
                    text = int(text)
                except ValueError:
                    pass
            elif kind == "float" and text == value:
                try:
                    text = float(text)
                except ValueError:
                    pass
            elif kind == "bool" and text == value:
                text = text == "true"
        parent[path[-1]] = text
        ok_spans.update(i for _, _, _, _, i in items)

    target = _target_for(json_path, out_path)
    # Formatting follows the artifact, same sniff as pysanitize.redact.json.
    newline = "\n" if raw.endswith("\n") else ""
    second = raw.split("\n", 1)[1] if "\n" in raw else ""
    indent = None
    if second[:1] in (" ", "\t"):
        indent = second[: len(second) - len(second.lstrip(" \t"))]
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent) + newline, encoding="utf-8"
    )
    unresolved = len(placed) - len(ok_spans)
    return RecoverResult(target, len(ok_spans), unresolved, "json")


def _insert_values(page, spans, values) -> int:
    """Write decrypted ``values`` back beside their redacted rects.

    Font size follows the page's body text — a whole-table rect is tall, but a
    masked cell's original size is the *cell* size, so sizing by the rect blows
    the value up to fill the table. Values that share one rect (all cells of a
    table map to its whole-block bbox) are stacked top-down in reading order
    (``md`` position) so they never overwrite each other. Returns how many
    values were written.
    """
    import pymupdf

    groups: dict[tuple[float, ...], list[tuple[str, float]]] = {}
    for span, value in zip(spans, values):
        if value is None:
            continue
        key = tuple(round(c, 2) for c in span["rects"][0])
        groups.setdefault(key, []).append((value, span.get("md", (0, 0))[0]))
    written = 0
    for key, members in groups.items():
        members.sort(key=lambda m: m[1])  # reading order
        rect = pymupdf.Rect(*key)
        units = max((sum(_em(c) for c in v) for v, _ in members), default=1.0)
        body = _body_fontsize(page, rect)
        fontsize = max(
            1.0,
            min(
                body or rect.height * 0.72,
                rect.width * 0.96 / units,
                rect.height / len(members) * 0.72,
            ),
        )
        line_h = fontsize * 1.25
        y = rect.y1 - fontsize * 0.25 if len(members) == 1 else rect.y0 + line_h
        for value, _ in members:
            page.insert_text(
                pymupdf.Point(rect.x0 + 1, y),
                value,
                fontsize=fontsize,
                fontname="china-s",
            )
            y += line_h
            written += 1
    return written


def _body_fontsize(page, rect) -> float | None:
    """Median size of the page's text spans in ``rect``'s y-range (the page-wide
    median when none overlap; None on an empty page)."""
    import pymupdf

    sizes, page_sizes = [], []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")
                if not size:
                    continue
                page_sizes.append(size)
                bbox = pymupdf.Rect(span["bbox"])
                if bbox.y1 >= rect.y0 and bbox.y0 <= rect.y1:
                    sizes.append(size)
    pool = sizes or page_sizes
    if not pool:
        return None
    pool.sort()
    return pool[len(pool) // 2]


def _em(ch: str) -> float:
    """Glyph width in ems: full-width (CJK, full-width punct) vs ASCII."""
    return 1.0 if ord(ch) >= 0x2E80 else 0.55
