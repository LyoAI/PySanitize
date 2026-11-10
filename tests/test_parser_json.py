"""JSON native producer: leaf provenance, path traversal, page semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pysanitize.parser.json import format_path, parse_json_blocks


@pytest.fixture
def json_file(tmp_path: Path) -> Path:
    data = {
        "公司": "杭州某某科技有限公司",
        "员工": [
            {
                "姓名": "张三",
                "手机号": 13812345678,
                "身份证号": "110101199003078515",
                "多行": "第一行\n第二行",
            },
            {"姓名": "李四", "手机号": "13912345678"},
            {},  # empty element — must not shift later pages
        ],
        "配置": {"等级": 5, "比例": 0.25, "开关": True, "空值": None},
    }
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_boxes_line_up_with_block_text(json_file: Path):
    """The invariant everything downstream rests on: block.text slices out
    exactly each box's text."""
    blocks = parse_json_blocks(json_file)
    assert blocks
    for b in blocks:
        for box in b.json_boxes:
            assert b.text[box.start : box.end] == box.text
            assert "\n" not in box.text


def test_paths_are_traversal_routes(json_file: Path):
    blocks = parse_json_blocks(json_file)
    paths = {box.path for b in blocks for box in b.json_boxes}
    assert ("公司",) in paths
    assert ("员工", 0, "姓名") in paths
    assert ("员工", 0, "手机号") in paths
    assert ("员工", 1, "手机号") in paths
    assert ("配置", "等级") in paths


def test_scalar_rendering(json_file: Path):
    """One leaf per line, xlsx-style rendering: int (and integral float) has
    no .0, bool is true/false, null and empty strings render nothing,
    newlines flatten (a rewritten value loses them, as a rewritten cell does)."""
    blocks = parse_json_blocks(json_file)
    by_path = {box.path: box for b in blocks for box in b.json_boxes}
    assert by_path[("员工", 0, "手机号")].text == "13812345678"
    assert by_path[("员工", 0, "手机号")].kind == "int"
    assert by_path[("配置", "等级")].kind == "int"
    assert by_path[("配置", "比例")].text == "0.25"
    assert by_path[("配置", "比例")].kind == "float"
    assert by_path[("配置", "开关")].text == "true"
    assert by_path[("配置", "开关")].kind == "bool"
    assert by_path[("员工", 0, "多行")].text == "第一行 第二行"
    assert ("配置", "空值") not in by_path  # null — nothing to detect


def test_integral_float_renders_as_int(tmp_path: Path):
    path = tmp_path / "f.json"
    path.write_text('{"价格": 5.0, "重量": 0.5}', encoding="utf-8")
    boxes = {b.path: b for blk in parse_json_blocks(path) for b in blk.json_boxes}
    assert boxes[("价格",)].text == "5" and boxes[("价格",)].kind == "int"
    assert boxes[("重量",)].text == "0.5" and boxes[("重量",)].kind == "float"


def test_top_level_array_pages_survive_empty_elements(tmp_path: Path):
    """page is the element's 1-based position — an empty element emits no
    block but never shifts the numbering (the xlsx empty-sheet lesson)."""
    data = [{"手机号": "13812345678"}, {}, {"手机号": "13912345678"}]
    path = tmp_path / "arr.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    blocks = parse_json_blocks(path)
    assert [b.page for b in blocks] == [1, 3]


def test_top_level_array_paths_start_at_the_element_index(tmp_path: Path):
    """Paths are walked from the ROOT by redact and recover, so under a
    top-level array every one must begin with the element's 0-based index —
    a bare ``("手机号",)`` indexes the root list with a string and raised
    "list indices must be integers or slices, not str" on every such file.
    Empties never shift numbering, so index 1 is missing but index 2 is not.
    """
    data = [{"手机号": "13812345678"}, {}, ["13812345678", "张三"]]
    path = tmp_path / "arr.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    boxes = {(blk.page, b.path): b for blk in parse_json_blocks(path) for b in blk.json_boxes}
    assert set(boxes) == {(1, (0, "手机号")), (3, (2, 0)), (3, (2, 1))}
    assert boxes[(3, (2, 0))].text == "13812345678"
    assert boxes[(1, (0, "手机号"))].text == "13812345678"


def test_top_level_object_is_single_block(json_file: Path):
    blocks = parse_json_blocks(json_file)
    assert len(blocks) == 1 and blocks[0].page == 1


def test_scalar_top_level_document(tmp_path: Path):
    path = tmp_path / "s.json"
    path.write_text('"13812345678"', encoding="utf-8")
    blocks = parse_json_blocks(path)
    assert len(blocks) == 1
    assert blocks[0].json_boxes[0].text == "13812345678"


def test_format_path():
    assert format_path(("员工", 0, "手机号")) == "$.员工[0].手机号"
    assert format_path(()) == "$"
