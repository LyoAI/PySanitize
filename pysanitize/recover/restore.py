"""Restore originals from sanitized artifacts (``sanitized.md`` / ``redacted.pdf``).

Pure consumers: they read the artifact + its ``audit.json`` + a passphrase and
write ``<stem>_recovered.<ext>`` beside the input. The sanitize pipeline is
never imported — recovery only needs the audit's ``recovery`` block and the
per-span data recorded next to each placeholder (``encrypted_value`` + ``md``
range + ``rects``).

Markdown recovery is exact: each span records where its placeholder sits, so
restoration splices the decrypted value back at that range (verified against
the placeholder text). PDF recovery is best-effort: the original glyphs were
truly *deleted* at sanitize time, so recovery clears the redacted region and
re-inserts the decrypted text with a fitted font — the values come back, the
original layout does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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
    kind: str  # "markdown" | "pdf"


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
    return _recover_markdown(input_path, cipher, audit, out_path)


def _target_for(input_path: Path, out_path) -> Path:
    if out_path:
        return Path(out_path)
    return input_path.with_name(f"{input_path.stem}_recovered{input_path.suffix}")


def _recover_markdown(md_path: Path, cipher: TokenCipher, audit: dict, out_path) -> RecoverResult:
    text = md_path.read_text(encoding="utf-8")
    spans = [s for s in audit_spans(audit) if "md" in s]
    restored = 0
    # Splice from the back so ranges recorded earlier stay valid.
    for span in sorted(spans, key=lambda s: -s["md"][0]):
        try:
            value = cipher.decrypt_token(span["encrypted_value"])
        except Exception:  # wrong key / tampered ciphertext
            continue
        start, end = span["md"]
        if text[start:end] != span.get("masked_value"):
            continue  # placeholder gone — the file was edited after sanitizing
        text = text[:start] + value + text[end:]
        restored += 1
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
