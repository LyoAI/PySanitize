"""middle.json projection: line geometry, flatten offsets, TOC skip, table alignment."""

from __future__ import annotations

from pathlib import Path

from pysanitize.parser.blocks import BBox, LineBox
from pysanitize.parser.middle import load_middle, locate_middle_json, project_middle


def _page(blocks, size=(595.0, 842.0)):
    return {"page_idx": 0, "page_size": list(size), "para_blocks": blocks}


def test_project_line_geometry_and_flattened_offsets():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "text",
                        "bbox": [10, 40, 200, 60],
                        "lines": [
                            {"bbox": [10, 40, 100, 50], "spans": [{"content": "第一行"}]},
                            {"bbox": [10, 52, 100, 60], "spans": [{"content": "第二行"}]},
                        ],
                    }
                ]
            )
        ]
    }
    blocks, dims = project_middle(middle)
    assert dims == [(595.0, 842.0)]
    block = blocks[0]
    assert block.type == "paragraph"  # middle "text" maps to v2 "paragraph"
    assert block.text == "第一行\n第二行"  # physical lines joined with "\n"
    assert block.bbox == BBox(10, 40, 200, 60)
    # char offsets account for the "\n" separator between lines
    assert block.line_boxes == [
        LineBox("第一行", BBox(10, 40, 100, 50), 0, 3),
        LineBox("第二行", BBox(10, 52, 100, 60), 4, 7),
    ]


def test_inline_equation_gets_spaces():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "text",
                        "lines": [
                            {
                                "spans": [
                                    {"content": "总"},
                                    {"type": "span_equation_inline", "content": "E"},
                                    {"content": "计"},
                                ]
                            }
                        ],
                    }
                ]
            )
        ]
    }
    blocks, _ = project_middle(middle)
    assert blocks[0].text == "总 E 计"


def test_title_level_preserved_and_toc_dropped():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "title",
                        "level": 2,
                        "bbox": [0, 0, 100, 10],
                        "lines": [{"spans": [{"content": "第二章"}]}],
                    },
                    {
                        "type": "index",
                        "bbox": [0, 20, 300, 200],
                        "lines": [{"spans": [{"content": "1.1 第一节"}]}],
                    },
                ]
            )
        ]
    }
    blocks, _ = project_middle(middle)
    assert blocks[0].type == "title"
    assert blocks[0].level == 2
    assert blocks[0].text == "第二章"
    # TOC: dropped from text (v2-era output), kept as an empty placeholder so
    # per-page order still aligns with the v2 records used for table recovery.
    assert blocks[1].type == "index"
    assert blocks[1].text == ""


def test_table_alignment_reuses_v2_html():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "table",
                        "bbox": [0, 100, 300, 200],
                        "blocks": [
                            {
                                "type": "table_body",
                                "lines": [{"spans": [{"type": "span_table", "content": ""}]}],
                            },
                            {
                                "type": "table_caption",
                                "lines": [{"spans": [{"content": "表1"}]}],
                            },
                        ],
                    }
                ]
            )
        ]
    }
    v2 = [[{"type": "table", "content": {"html": "<table><tr><td>1</td><td>2</td></tr></table>"}}]]
    blocks, _ = project_middle(middle, v2_pages=v2)
    assert "| 1 | 2 |" in blocks[0].text
    assert blocks[0].bbox == BBox(0, 100, 300, 200)


def test_table_alignment_retries_off_by_one():
    # v2 has an extra non-table record in the same slot; the ±1 retry finds the
    # table's html one position on.
    middle = {"pdf_info": [_page([{"type": "table", "bbox": [0, 0, 50, 50], "blocks": []}])]}
    v2 = [
        [
            {"type": "paragraph", "content": {"paragraph_content": "shim"}},
            {"type": "table", "content": {"html": "<table><tr><td>a</td></tr></table>"}},
        ]
    ]
    blocks, _ = project_middle(middle, v2_pages=v2)
    assert "| a |" in blocks[0].text


def test_table_without_v2_match_keeps_caption():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "table",
                        "bbox": [0, 0, 50, 50],
                        "blocks": [
                            {"type": "table_caption", "lines": [{"spans": [{"content": "表A"}]}]}
                        ],
                    }
                ]
            )
        ]
    }
    blocks, _ = project_middle(middle, v2_pages=[[{"type": "paragraph", "content": {"paragraph_content": "x"}}]])
    assert blocks[0].text == "表A"  # caption-only fallback


def test_image_block_carries_path_and_box():
    middle = {
        "pdf_info": [
            _page(
                [
                    {
                        "type": "image",
                        "bbox": [0, 300, 100, 400],
                        "blocks": [
                            {
                                "type": "image_body",
                                "lines": [
                                    {
                                        "bbox": [0, 300, 100, 400],
                                        "spans": [
                                            {
                                                "type": "span_image",
                                                "content": "",
                                                "image_path": "images/a.png",
                                                "bbox": [0, 300, 100, 400],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            )
        ]
    }
    blocks, _ = project_middle(middle)
    block = blocks[0]
    assert block.type == "image"
    assert block.image_path == Path("images/a.png")
    assert block.image_bbox == BBox(0, 300, 100, 400)


def test_unprojectable_blocks_skipped():
    middle = {
        "pdf_info": [
            _page(
                [
                    {"type": "text", "bbox": [0, 0, 10, 10]},  # no lines
                    {"type": "junk"},
                ]
            )
        ]
    }
    blocks, _ = project_middle(middle)
    assert blocks == []


def test_office_no_geometry():
    # office backend emits no pdf_info → ([], None); page_dims only when pages exist.
    assert project_middle({}) == ([], None)
    assert project_middle({"pdf_info": []}) == ([], [])  # no pages, no dims
    blocks, dims = project_middle({"pdf_info": [{"para_blocks": []}]})
    assert blocks == [] and dims == [(0.0, 0.0)]  # page present but unmeasured


def test_locate_and_load_middle_json(tmp_path):
    doc = tmp_path / "年报.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    out = tmp_path / "md"
    sub = out / "年报" / "auto"
    sub.mkdir(parents=True)
    (sub / "年报_middle.json").write_text('{"pdf_info": []}', encoding="utf-8")

    found = locate_middle_json(doc, out)
    assert found is not None and found.name == "年报_middle.json"
    assert load_middle(doc, out) == {"pdf_info": []}
    assert locate_middle_json(doc, tmp_path / "nope") is None
    assert load_middle(doc, tmp_path / "nope") is None
