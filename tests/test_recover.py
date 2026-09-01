"""Recoverable masking: ciphertext crypto, --recover restoration, CLI/TUI wiring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pysanitize.recover.crypto import (
    ALGORITHM,
    ENV_KEY,
    KDF_NAME,
    TOKEN_RE,
    TokenCipher,
    obtain_passphrase,
)
from pysanitize.recover.restore import (
    audit_spans,
    cipher_from_audit,
    load_audit,
    recover_file,
)

PASSPHRASE = "correct horse battery staple"


# ---- crypto ------------------------------------------------------------------


def _cipher(salt: str | None = None) -> TokenCipher:
    return TokenCipher.from_passphrase(PASSPHRASE, salt=salt)


def test_ciphertext_format():
    tok = _cipher().token("13812345678")
    assert TOKEN_RE.fullmatch(tok), tok
    assert tok.startswith("ENC(v1:")


def test_roundtrip_restores_value():
    cipher = _cipher()
    assert cipher.decrypt_token(cipher.token("王小明")) == "王小明"


def test_same_value_maps_to_same_token():
    cipher = _cipher()
    assert cipher.token("a@b.com") == cipher.token("a@b.com")


def test_separate_runs_produce_different_tokens():
    # Fresh nonce per cipher: two runs of the same document never share tokens.
    assert _cipher().token("x") != _cipher().token("x")


def test_wrong_key_fails():
    tok = _cipher().token("13812345678")
    other = TokenCipher.from_passphrase("a different passphrase", salt=_cipher().salt)
    with pytest.raises(Exception):
        other.decrypt_token(tok)


def test_tampered_token_fails():
    tok = _cipher().token("13812345678")
    flipped = tok[:-2] + ("AA" if tok[-2:] != "AA" else "BB") + ")"
    with pytest.raises(Exception):
        _cipher().decrypt_token(flipped)


def test_non_token_string_rejected():
    with pytest.raises(ValueError, match="not a recovery token"):
        _cipher().decrypt_token("hello world")


def test_meta_has_no_key_material():
    meta = _cipher().meta()
    assert meta["algorithm"] == ALGORITHM and meta["kdf"] == KDF_NAME
    assert meta["enabled"] is True and meta["kdf_salt"]
    assert PASSPHRASE not in json.dumps(meta)


# ---- passphrase resolution ---------------------------------------------------


def test_env_key_name():
    # Locks the documented name — the docs / CLI help / TUI placeholder all
    # refer to it, so a rename-back would silently break the .env path.
    assert ENV_KEY == "PYSANITIZE_RECOVER_KEY"


def test_passphrase_arg_beats_env_and_keyfile(tmp_path, monkeypatch):
    keyfile = tmp_path / ".recover.key"
    keyfile.write_text("from-file\n")
    monkeypatch.setenv(ENV_KEY, "from-env")
    secret, generated = obtain_passphrase("from-arg", keyfile)
    assert (secret, generated) == ("from-arg", False)


def test_passphrase_env_before_keyfile(tmp_path, monkeypatch):
    keyfile = tmp_path / ".recover.key"
    keyfile.write_text("from-file\n")
    monkeypatch.setenv(ENV_KEY, "from-env")
    assert obtain_passphrase(None, keyfile)[0] == "from-env"


def test_passphrase_read_from_keyfile(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    keyfile = tmp_path / ".recover.key"
    keyfile.write_text("from-file\n")
    assert obtain_passphrase(None, keyfile) == ("from-file", False)


def test_passphrase_generated_with_private_keyfile(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    keyfile = tmp_path / ".recover.key"
    secret, generated = obtain_passphrase(None, keyfile)
    assert generated and secret
    assert keyfile.stat().st_mode & 0o777 == 0o600  # never world-readable


def test_recovery_never_generates_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(ValueError, match="no recovery passphrase"):
        obtain_passphrase(None, tmp_path / ".recover.key", allow_generate=False)


# ---- placeholder locating (exact, recorded while building) -------------------


def test_mask_text_placed_records_exact_ranges():
    from pysanitize.detector.base import Detection
    from pysanitize.detector.specs import MaskSpec
    from pysanitize.masker.text import mask_text_placed

    specs = {
        "phone": MaskSpec(keep_head=3, keep_tail=4),
        "person_name": MaskSpec(template="***"),  # shorter than the value
    }
    # 电话(2) 13812345678(2..13) 联系人(13..16) 张三(16..18) 。
    dets = [
        Detection("phone", "13812345678", 2, 13, 1),
        Detection("person_name", "张三", 16, 18, 1),
    ]
    masked, places = mask_text_placed("电话13812345678联系人张三。", dets, specs)
    assert masked == "电话138****5678联系人***。"
    # ranges follow the *output* string, absorbing the length change above
    assert places[(2, 13)] == (2, 13)
    assert places[(16, 18)] == (16, 19)


def test_recovery_exact_when_document_contains_placeholder_lookalike(
    make_doc, monkeypatch, tmp_path
):
    """Regression: a literal ``***`` between two names must not steal a
    recovery range — recovery is 1:1, the look-alike stays untouched."""
    from pysanitize import pipeline as pl

    doc = make_doc([
        ("paragraph", "联系人：张三", 1),
        ("paragraph", "***", 1),  # e.g. a markdown rule in the source
        ("paragraph", "联系人：李四", 1),
    ])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    r = pl.sanitize_document(
        "doc.pdf", detector="rules", fields=["person_name"],
        recoverable=True, recover_key=PASSPHRASE, out_dir=tmp_path / "out",
    )
    recovered = recover_file(
        r.sanitized_md, passphrase=PASSPHRASE
    ).output.read_text(encoding="utf-8")
    # identical to the markdown built with masking disabled
    assert recovered == "联系人：张三\n\n***\n\n联系人：李四"


def test_recovery_exact_for_title_heading_prefix(make_doc, monkeypatch, tmp_path):
    """A placeholder inside a title sits after the ``## `` prefix — the range
    must account for it (and any length change from an earlier mask)."""
    from pysanitize import pipeline as pl

    doc = make_doc([
        ("title", "张三的借款协议", 1, 2),
        ("paragraph", "联系人：李四", 1),
    ])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    r = pl.sanitize_document(
        "doc.pdf", detector="rules", fields=["person_name"],
        recoverable=True, recover_key=PASSPHRASE, out_dir=tmp_path / "out",
    )
    recovered = recover_file(
        r.sanitized_md, passphrase=PASSPHRASE
    ).output.read_text(encoding="utf-8")
    assert recovered == "## 张三的借款协议\n\n联系人：李四"


def test_straddling_detection_is_split_masked_and_recovered(
    make_doc, monkeypatch, tmp_path
):
    """A value spanning the block separator (LLM verbatim match) is split into
    per-block pieces: both fragments are masked — nothing leaks — and recovery
    restores the exact original text."""
    from pysanitize import pipeline as pl
    from pysanitize.detector.base import Detection

    doc = make_doc([
        ("paragraph", "联系人张三", 1),
        ("paragraph", "李四王五很忙", 1),
    ])
    straddle = Detection(
        "person_name", doc.text[3:12], 3, 12, 1, source="llm"
    )
    assert straddle.value == "张三\n\n李四王五很"  # spans the "\n\n" separator

    class FixedDetector:
        def __init__(self, **kwargs): ...
        def detect(self, doc):
            return [straddle]

    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    monkeypatch.setattr(pl, "RuleDetector", FixedDetector)
    r = pl.sanitize_document(
        "doc.pdf", detector="rules", fields=["person_name"],
        recoverable=True, recover_key=PASSPHRASE, out_dir=tmp_path / "out",
    )
    md = r.sanitized_md.read_text(encoding="utf-8")
    assert "张三" not in md and "李四" not in md  # no fragment leaks
    assert md == "联系人***\n\n***忙"

    recovered = recover_file(
        r.sanitized_md, passphrase=PASSPHRASE
    ).output.read_text(encoding="utf-8")
    assert recovered == "联系人张三\n\n李四王五很忙"


def test_detection_in_dropped_block_gets_no_md_range(tmp_path, monkeypatch):
    """An image block with no local copy is dropped from sanitized.md — its
    detection must carry no ``md`` range (there is nothing to splice into)."""
    from pysanitize import pipeline as pl
    from pysanitize.parser.blocks import Block
    from pysanitize.parser.document import build_document

    blocks = [
        Block(block_id="b0", type="paragraph", text="联系人：张三", page=1, order=0),
        Block(  # image file was never copied → block dropped from the md
            block_id="b1", type="image", text="配图：李四", page=1, order=1,
            image_path=Path("images/gone.jpg"),
        ),
    ]
    doc = build_document("doc", tmp_path / "doc.pdf", blocks, tmp_path)
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    r = pl.sanitize_document(
        "doc.pdf", detector="rules", fields=["person_name"],
        recoverable=True, recover_key=PASSPHRASE, out_dir=tmp_path / "out",
    )
    assert r.sanitized_md.read_text(encoding="utf-8") == "联系人：***"
    spans = json.loads(r.audit_path.read_text(encoding="utf-8"))["masked_spans"]
    by_end = {s["end"]: s for s in spans}  # original doc.text offsets
    assert "md" in by_end[6]          # 张三 [4,6): placeholder emitted
    assert "md" not in by_end[13]     # 李四 [11,13): block dropped → nothing


def test_recovery_is_character_exact_despite_placeholder_lookalikes(
    tmp_path, monkeypatch
):
    """The 1:1 property: for any document, ``recover(sanitize(doc))`` equals
    the markdown built with masking disabled — even when the document itself
    contains strings identical to every placeholder template."""
    from pysanitize import pipeline as pl
    from pysanitize.parser.blocks import Block
    from pysanitize.parser.document import build_document

    blocks = [
        Block(block_id="b0", type="title", text="张三的借款协议", page=1, order=0, level=2),
        Block(block_id="b1", type="paragraph", text="联系人：张三，电话 13812345678。", page=1, order=1),
        Block(block_id="b2", type="paragraph", text="138****5678", page=1, order=2),  # lookalike
        Block(block_id="b3", type="paragraph", text="****", page=1, order=3),         # lookalike
        Block(block_id="b4", type="paragraph", text="甲方：北京某某科技有限公司", page=1, order=4),
        Block(block_id="b5", type="paragraph", text="联系人：李四，电话 13998887777。", page=1, order=5),
        Block(block_id="b6", type="paragraph", text="******", page=1, order=6),       # lookalike
    ]
    doc = build_document("doc", tmp_path / "doc.pdf", blocks, tmp_path)
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)

    masked = pl.sanitize_document(
        "doc.pdf", detector="rules", recoverable=True, recover_key=PASSPHRASE,
        out_dir=tmp_path / "out",
    )

    class _NoDetections:
        def add(self, detector): ...
        def detect(self, doc):
            return []

    monkeypatch.setattr(pl, "DetectionRegistry", _NoDetections)
    reference = pl.sanitize_document(
        "doc.pdf", detector="rules", out_dir=tmp_path / "ref"
    )

    result = recover_file(masked.sanitized_md, passphrase=PASSPHRASE)
    recovered = result.output.read_text(encoding="utf-8")
    assert result.restored == 6 and result.unresolved == 0
    assert recovered == reference.sanitized_md.read_text(encoding="utf-8")


def test_insert_fitted_long_value_never_runs_off_the_page(tmp_path):
    """Regression: a flat ``width * 1.55 / len`` size estimate truncated long
    values on wide rects — the tail glyphs fell off the page edge and the
    recovered value came back incomplete (“一政策性银” without 行)."""
    import pymupdf

    from pysanitize.recover.restore import _insert_fitted

    doc = pymupdf.open()  # A4: 595 × 842
    page = doc.new_page()
    rect = pymupdf.Rect(93, 184, 540, 396)  # a whole-block table bbox
    _insert_fitted(page, rect, "一政策性银行")
    out = tmp_path / "fitted.pdf"
    doc.save(out)

    text = pymupdf.open(out)[0].get_text()
    assert "一政策性银行" in text  # complete — nothing clipped off the page


# ---- pipeline integration ----------------------------------------------------


def _sanitize_recoverable(make_doc, monkeypatch, tmp_path, *, redact_pdf=False):
    """One sanitize run with recoverable masking; returns (result, doc_text)."""
    from pysanitize import pipeline as pl

    doc = make_doc([
        ("title", "借款协议", 1),
        ("paragraph", "甲方：北京某某科技有限公司 电话 13812345678", 1),
    ])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    return pl.sanitize_document(
        "doc.pdf",
        detector="rules",
        recoverable=True,
        recover_key=PASSPHRASE,
        redact_pdf=redact_pdf,
        out_dir=tmp_path / "out",
    )


def test_recoverable_md_keeps_normal_placeholders(make_doc, monkeypatch, tmp_path):
    r = _sanitize_recoverable(make_doc, monkeypatch, tmp_path)
    md = r.sanitized_md.read_text(encoding="utf-8")
    # The document is indistinguishable from a non-recoverable run: normal
    # placeholders, raw values gone, no ciphertext anywhere.
    assert "138****5678" in md and "13812345678" not in md
    assert "甲方：****" in md  # company name fully masked, as in a normal run
    assert "ENC(" not in md and "借款协议" in md


def test_recoverable_audit_schema(make_doc, monkeypatch, tmp_path):
    r = _sanitize_recoverable(make_doc, monkeypatch, tmp_path)
    raw = r.audit_path.read_text(encoding="utf-8")
    audit = json.loads(raw)
    assert PASSPHRASE not in raw  # the key itself never lands on disk
    rec = audit["recovery"]
    assert rec["enabled"] and rec["algorithm"] == ALGORITHM and rec["kdf_salt"]
    assert "encrypted_value" not in rec  # ciphertext lives per span, not here
    for span in audit["masked_spans"]:
        assert not span["masked_value"].startswith("ENC(")  # placeholder is the normal mask
        assert span["encrypted_value"].startswith("ENC(")   # ciphertext under its own name
        assert {"start", "end", "md"} <= span.keys()        # original + sanitized.md positions
    phone = next(s for s in audit["masked_spans"] if s["field_type"] == "phone")
    assert phone["masked_value"] == "138****5678"


def test_non_recoverable_audit_has_no_recovery_block(make_doc, monkeypatch, tmp_path):
    from pysanitize import pipeline as pl

    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", out_dir=tmp_path / "out")
    audit = json.loads(r.audit_path.read_text(encoding="utf-8"))
    assert "recovery" not in audit
    assert "encrypted_value" not in audit["masked_spans"][0]


def test_markdown_recovery_roundtrip(make_doc, monkeypatch, tmp_path):
    r = _sanitize_recoverable(make_doc, monkeypatch, tmp_path)
    result = recover_file(
        r.sanitized_md, audit_path=r.audit_path, passphrase=PASSPHRASE
    )
    assert result.kind == "markdown"
    recovered = result.output.read_text(encoding="utf-8")
    assert "13812345678" in recovered
    assert "北京某某科技有限公司" in recovered  # values back, placeholders gone
    assert "ENC(" not in recovered
    assert result.restored == 2 and result.unresolved == 0


def test_markdown_recovery_rejects_edited_document(make_doc, monkeypatch, tmp_path):
    # Offsets no longer match → the span counts as unresolved, nothing corrupts.
    r = _sanitize_recoverable(make_doc, monkeypatch, tmp_path)
    edited = "开头新加的一段\n\n" + r.sanitized_md.read_text(encoding="utf-8")
    r.sanitized_md.write_text(edited, encoding="utf-8")
    result = recover_file(r.sanitized_md, passphrase=PASSPHRASE)
    assert result.restored == 0 and result.unresolved == 2


def test_markdown_recovery_with_generated_keyfile(make_doc, monkeypatch, tmp_path):
    # No --recover-key and no env: sanitize generated .recover.key next to the
    # audit, and recovery picks it up — end-to-end with zero key handling.
    from pysanitize import pipeline as pl

    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    monkeypatch.delenv(ENV_KEY, raising=False)
    r = pl.sanitize_document(
        "doc.pdf", detector="rules", recoverable=True, out_dir=tmp_path / "out"
    )
    assert (r.out_dir / ".recover.key").is_file()
    result = recover_file(r.sanitized_md)
    assert "13812345678" in result.output.read_text(encoding="utf-8")


def test_recovery_needs_recovery_block(make_doc, monkeypatch, tmp_path):
    from pysanitize import pipeline as pl

    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", out_dir=tmp_path / "out")
    with pytest.raises(ValueError, match="no recovery block"):
        recover_file(r.sanitized_md, passphrase=PASSPHRASE)


def test_recovery_needs_passphrase(make_doc, monkeypatch, tmp_path):
    r = _sanitize_recoverable(make_doc, monkeypatch, tmp_path)
    (r.out_dir / ".recover.key").unlink(missing_ok=True)
    with pytest.raises(ValueError, match="no recovery passphrase"):
        recover_file(r.sanitized_md, passphrase=None)


def test_audit_without_document_fails_cleanly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_audit(tmp_path / "missing.md")


# ---- PDF roundtrip -----------------------------------------------------------


def _write_phone_pdf(path):
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "13812345678", fontsize=12, fontname="china-s")
    pdf.save(path)
    return pymupdf.open(path)[0].search_for("13812345678")[0]


def test_pdf_recovery_roundtrip(make_doc, monkeypatch, tmp_path):
    """Sanitize a PDF with normal redaction, then restore via the audit rects."""
    import pymupdf
    from pysanitize import pipeline as pl
    from pysanitize.parser.blocks import LineBox

    src = tmp_path / "doc.pdf"
    box = _write_phone_pdf(src)
    doc = make_doc([("paragraph", "13812345678", 1)])
    doc.page_dimensions = [(595.0, 842.0)]
    doc.blocks[0].line_boxes = [LineBox("13812345678", box, 0, 11)]
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)

    r = pl.sanitize_document(
        src,
        detector="rules",
        recoverable=True,
        recover_key=PASSPHRASE,
        redact_pdf=True,
        out_dir=tmp_path / "out",
    )
    redacted = "\n".join(p.get_text() for p in pymupdf.open(r.redacted_pdf))
    # The redacted PDF looks like any other run: glyphs deleted, mosaic only —
    # no plaintext, no ciphertext.
    assert "13812345678" not in redacted
    assert "ENC(" not in redacted

    audit = json.loads(r.audit_path.read_text(encoding="utf-8"))
    span = next(s for s in audit["masked_spans"] if "rects" in s)
    assert len(span["rects"][0]) == 4  # [x0, y0, x1, y1] recorded per span

    result = recover_file(r.redacted_pdf, passphrase=PASSPHRASE)
    assert result.kind == "pdf"
    recovered = "\n".join(p.get_text() for p in pymupdf.open(result.output))
    assert "13812345678" in recovered  # value restored at the recorded rect
    assert result.restored == 1 and result.unresolved == 0


# ---- CLI / TUI wiring --------------------------------------------------------


def test_cli_flags_parse():
    from pysanitize.cli import build_parser

    a = build_parser().parse_args(["a.pdf", "--recoverable", "--recover-key", "s3cret"])
    assert a.recoverable is True and a.recover_key == "s3cret"
    a = build_parser().parse_args(
        ["out/sanitized.md", "--recover", "--recover-audit", "x/audit.json"]
    )
    assert a.recover is True and a.recover_audit == "x/audit.json"
    assert a.file == "out/sanitized.md"
    plain = build_parser().parse_args(["a.pdf"])
    assert plain.recoverable is None and plain.recover is False


def test_cli_recover_dispatch(monkeypatch, capsys):
    import pysanitize.recover
    from pysanitize.cli import main
    from pysanitize.recover.restore import RecoverResult

    seen = {}

    def fake_recover(file, *, audit_path=None, passphrase=None, out_path=None):
        seen["file"], seen["audit"], seen["key"] = file, audit_path, passphrase
        return RecoverResult(Path(file).parent / "recovered.md", 3, 1, "markdown")

    monkeypatch.setattr(pysanitize.recover, "recover_file", fake_recover)
    with pytest.raises(SystemExit) as e:
        main(["out/sanitized.md", "--recover", "--recover-key", "k"])
    assert e.value.code == 0
    assert seen == {"file": "out/sanitized.md", "audit": None, "key": "k"}
    out = capsys.readouterr().out
    assert "Recovered document" in out and "unresolved spans 1" in out


def test_tui_recoverable_switch():
    from textual.widgets import Switch

    from pysanitize.tui import PySanitizeApp
    from pysanitize.tui.screens import OptionsPane

    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            opts = app.query_one(OptionsPane)
            assert opts.collect()["recoverable"] is None  # config default holds
            opts.query_one("#recoverable", Switch).value = True
            assert opts.collect()["recoverable"] is True
            # blank key stays absent so env / .recover.key / auto-generate apply
            assert opts.collect()["recover_key"] is None
            opts.query_one("#recover-key").value = "s3cret"
            assert opts.collect()["recover_key"] == "s3cret"

    asyncio.run(_test())


def test_tui_recover_pane_collects_input(tmp_path):
    from pysanitize.tui import PySanitizeApp
    from pysanitize.tui.screens import RecoverPane

    async def _test():
        app = PySanitizeApp()
        async with app.run_test() as pilot:
            pane = app.query_one(RecoverPane)
            assert pane.file_path() is None and pane.passphrase() is None
            pane.query_one("#recover-file").value = str(tmp_path / "sanitized.md")
            pane.query_one("#recover-audit").value = "  "
            pane.query_one("#recover-pass").value = " s3cret "
            assert pane.file_path() == tmp_path / "sanitized.md"
            assert pane.audit_path() is None  # blank → audit.json beside the file
            assert pane.passphrase() == "s3cret"

    asyncio.run(_test())
