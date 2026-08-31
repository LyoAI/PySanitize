"""PDF redaction: real pymupdf documents, detections → rects → glyph removal."""

from __future__ import annotations

import pymupdf

from pysanitize.detector.rules import RuleDetector
from pysanitize.detector.specs import load_field_specs, select_specs
from pysanitize.parser.blocks import BBox, LineBox
from pysanitize.redact import redact_pdf, resolve_rects, verify_redaction


def _write_pdf(path, lines):
    """A CJK PDF with one ``insert_text`` per ``(y, text)``; returns the measured
    line bboxes (china-s is full-width monospaced, so the sub-box mapper is exact)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    boxes = []
    for y, text in lines:
        page.insert_text((72, y), text, fontsize=12, fontname="china-s")
        boxes.append(BBox(*page.search_for(text)[0]))
    doc.save(path)
    return boxes


def _doc(make_doc, text, line_boxes):
    doc = make_doc([("paragraph", text, 1)])
    doc.page_dimensions = [(595.0, 842.0)]
    doc.blocks[0].line_boxes = line_boxes
    return doc


def _detect(doc):
    specs = select_specs(load_field_specs(), ["phone"])
    return RuleDetector(specs=specs).detect(doc)


def test_redact_pdf_removes_glyphs_and_preserves_neighbor_line(tmp_path, make_doc):
    src = tmp_path / "src.pdf"
    boxes = _write_pdf(src, [(100, "13812345678"), (160, "保留行不受影响")])
    doc = _doc(
        make_doc,
        "13812345678\n保留行不受影响",
        [
            LineBox("13812345678", boxes[0], 0, 11),
            LineBox("保留行不受影响", boxes[1], 12, 19),
        ],
    )
    dets = _detect(doc)
    assert dets and dets[0].value == "13812345678"

    rects = resolve_rects(doc, dets, doc.page_dimensions)
    assert len(rects) == 1  # only the phone line, never the neighbor line

    out = redact_pdf(src, rects, tmp_path / "redacted.pdf")
    text = "\n".join(p.get_text() for p in pymupdf.open(out))
    assert "13812345678" not in text
    assert "保留行不受影响" in text
    assert verify_redaction(out, [d.value for d in dets]) == []


def test_resolve_rects_sub_regions_within_line(tmp_path, make_doc):
    # The phone sits mid-line; the sub-rect must cover exactly the phone glyph
    # run (full-width china-s chars make the proportional estimate exact).
    src = tmp_path / "src.pdf"
    text = "联系电话13812345678微信同号"
    _write_pdf(src, [(100, text)])
    line_box = BBox(72, 88, 72 + len(text) * 12, 102.4)
    doc = _doc(make_doc, text, [LineBox(text, line_box, 0, len(text))])
    dets = _detect(doc)
    assert dets and dets[0].value == "13812345678"

    rects = resolve_rects(doc, dets, doc.page_dimensions)
    assert len(rects) == 1
    phone = pymupdf.open(src)[0].search_for("13812345678")[0]
    r = rects[0].rect
    # the padded sub-rect covers the whole phone glyph run
    assert r.x0 - 1 <= phone.x0 <= r.x1
    assert r.x0 <= phone.x1 <= r.x1 + 1
    # and stays inside the line's y-band (padded)
    assert r.y0 - 1 <= phone.y0 and phone.y1 <= r.y1 + 1


def test_redact_pdf_block_style(tmp_path, make_doc):
    src = tmp_path / "src.pdf"
    boxes = _write_pdf(src, [(100, "13812345678")])
    doc = _doc(make_doc, "13812345678", [LineBox("13812345678", boxes[0], 0, 11)])
    dets = _detect(doc)
    out = redact_pdf(
        src, resolve_rects(doc, dets, doc.page_dimensions),
        tmp_path / "block.pdf", style="block",
    )
    assert "13812345678" not in "\n".join(p.get_text() for p in pymupdf.open(out))


def test_redact_pdf_skips_pages_without_rects(tmp_path, make_doc):
    # an untouched page must be copied byte-identical to the source region
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "13812345678", fontsize=12, fontname="china-s")
    page = doc.new_page(width=595, height=842)  # blank page — no redaction
    page.insert_text((72, 100), "无敏感内容", fontsize=12, fontname="china-s")
    doc.save(src)

    boxes = [BBox(*pymupdf.open(src)[0].search_for("13812345678")[0])]
    det_doc = _doc(make_doc, "13812345678", [LineBox("13812345678", boxes[0], 0, 11)])
    dets = _detect(det_doc)
    out = redact_pdf(src, resolve_rects(det_doc, dets, det_doc.page_dimensions),
                     tmp_path / "out.pdf")
    pages = pymupdf.open(out)
    assert "13812345678" not in pages[0].get_text()
    assert "无敏感内容" in pages[1].get_text()
