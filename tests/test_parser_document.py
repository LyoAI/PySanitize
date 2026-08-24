"""ParsedDocument assembly: text join, char offsets, offset→block lookup."""

from __future__ import annotations


def test_text_joins_nonmeta_blocks(make_doc):
    doc = make_doc([
        ("title", "借款协议", 1),
        ("paragraph", "第一条 甲方", 1),
        ("table", "| A | B |\n| - | - |", 2),
    ])
    assert doc.text == "借款协议\n\n第一条 甲方\n\n| A | B |\n| - | - |"


def test_meta_blocks_excluded_from_text(make_doc):
    doc = make_doc([
        ("page_header", "内部资料", 1),
        ("paragraph", "正文", 1),
        ("page_footer", "第1页", 1),
    ])
    assert doc.text == "正文"
    # meta blocks keep -1 offsets so they can't be masked by accident
    header = next(b for b in doc.blocks if b.type == "page_header")
    assert header.char_start == header.char_end == -1


def test_char_ranges_are_contiguous(make_doc):
    doc = make_doc([("paragraph", "甲乙丙", 1), ("paragraph", "丁戊", 1)])
    b0, b1 = doc.blocks
    assert doc.text[b0.char_start : b0.char_end] == "甲乙丙"
    assert doc.text[b1.char_start : b1.char_end] == "丁戊"
    assert b1.char_start == b0.char_end + 2  # the "\n\n" separator


def test_block_at_and_span(make_doc):
    doc = make_doc([("paragraph", "一二三四", 1), ("paragraph", "五六七八", 2)])
    b0, b1 = doc.blocks
    assert doc.block_at(1) is b0
    assert doc.block_at(b1.char_start) is b1
    assert doc.block_at(-1) is None
    assert doc.block_at(len(doc.text) + 10) is None
    span = doc.span(b0.char_start, b1.char_end)
    assert [b.block_id for b in span] == ["b0", "b1"]


def test_pages_is_max_page(make_doc):
    doc = make_doc([("paragraph", "a", 1), ("paragraph", "b", 3)])
    assert doc.pages == 3
