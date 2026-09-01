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
        # …then write each decrypted value back into its first rectangle.
        for span, value in zip(page_spans, values):
            if value is None:
                continue
            _insert_fitted(page, pymupdf.Rect(*span["rects"][0]), value)
            restored += 1
    target = _target_for(pdf_path, out_path)
    doc.save(target)
    unresolved = len(spans) - restored
    return RecoverResult(target, restored, unresolved, "pdf")


def _insert_fitted(page, rect, value: str) -> None:
    """Insert ``value`` into ``rect``, shrinking the font until it fits.

    ``china-s`` is pymupdf's built-in CJK font, so recovered Chinese values
    render correctly. The size is bounded by the *estimated glyph width* —
    CJK/full-width chars are ~1.0em, ASCII ~0.55em — so a long value on a wide
    rect can never run past the page edge. (A flat ``1.55 / len`` estimate once
    truncated recovered values: the last glyphs fell off the page and the
    value came back incomplete.)
    """
    import pymupdf

    def em(ch: str) -> float:
        """Glyph width in ems: full-width (CJK, full-width punct) vs ASCII."""
        return 1.0 if ord(ch) >= 0x2E80 else 0.55

    units = sum(em(ch) for ch in value) or 1.0
    fontsize = max(1.0, min(rect.height * 0.72, rect.width * 0.96 / units))
    page.insert_text(
        pymupdf.Point(rect.x0 + 1, rect.y1 - rect.height * 0.18),
        value,
        fontsize=fontsize,
        fontname="china-s",
    )
