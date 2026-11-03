"""Native xlsx producer: worksheets → table blocks with CellBox provenance."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from openpyxl import Workbook

from pysanitize.parser.blocks import CellBox
from pysanitize.parser.document import parse_document
from pysanitize.parser.xlsx import parse_xlsx_blocks, _render


@pytest.fixture
def xlsx(tmp_path: Path) -> Path:
    """A workbook covering the rendering cases: numbers, dates, formulas,
    a hidden sheet, an empty sheet (between others — index drift trap), a
    merged range and a multiline cell."""
    wb = Workbook()
    ws = wb.active
    ws.title = "员工"
    ws.append(["姓名", "手机号", "工资", "入职日期", "备注"])
    ws.append(["张三", 13812345678, 25000.5, dt.date(2024, 3, 1), "第一行\n第二行"])
    ws["B2"].number_format = "0"  # phone as a *number*, like real exports
    ws.merge_cells("C3:D3")
    ws["C3"] = "合并单元格"
    wb.create_sheet("空表")  # no block, but shifts later sheets' index
    hidden = wb.create_sheet("薪资")
    hidden.sheet_state = "hidden"
    hidden.append(["姓名", "工资"])
    hidden.append(["张三", 25000.5])
    wb["员工"]["F1"] = '=CONCATENATE(B2," 发给张三")'  # no cache → formula text
    wb.save(tmp_path / "book.xlsx")
    return tmp_path / "book.xlsx"


def test_cellbox_invariant_and_blocks(xlsx: Path):
    blocks = parse_xlsx_blocks(xlsx)
    assert [b.type for b in blocks] == ["table", "table"]
    # empty sheet emits no block; hidden sheet still participates; page is the
    # 1-based worksheet position (薪资 is worksheet #3, not block #2)
    assert [b.page for b in blocks] == [1, 3]
    for block in blocks:
        assert block.cells
        for cell in block.cells:
            assert block.text[cell.start : cell.end] == cell.text
            assert isinstance(cell, CellBox)


def test_cellbox_worksheet_index_survives_empty_sheets(xlsx: Path):
    blocks = parse_xlsx_blocks(xlsx)
    薪资 = blocks[1]
    assert all(c.idx == 2 for c in 薪资.cells)  # 0-based: 员工=0, 空表=1, 薪资=2
    assert {c.sheet for c in 薪资.cells} == {"薪资"}


def test_rendered_surface(xlsx: Path):
    doc = parse_document(xlsx)
    assert doc.source_suffix == ".xlsx"
    assert doc.pages == 3
    text = doc.text
    # int-typed phone renders without ".0"; float keeps its repr; the
    # date-formatted cell renders date-only (openpyxl's default yyyy-mm-dd —
    # no phantom midnight); newlines flatten; merged cell renders at its anchor
    assert "| 13812345678 | 25000.5 | 2024-03-01 |" in text
    assert "第一行 第二行" in text
    assert "合并单元格" in text
    # formula without a cached value renders its literal text (detectable)
    assert '=CONCATENATE(B2," 发给张三")' in text
    assert "\n" not in text.split("\n")[2]  # every table row stays one line


def test_hidden_sheet_is_detected_surface(xlsx: Path):
    doc = parse_document(xlsx)
    assert "薪资" not in doc.text  # sheet names are not emitted
    assert doc.blocks[1].cells[0].text == "姓名"
    assert doc.blocks[1].cells[2].text == "张三"


def test_render_value_kinds():
    assert _render("a\nb", None) == ("a b", "str")
    assert _render(True, None) == ("TRUE", "bool")
    assert _render(13812345678, None) == ("13812345678", "int")
    assert _render(13812345678.0, None) == ("13812345678", "int")  # integral float
    assert _render(0.25, None) == ("0.25", "float")
    assert _render(dt.date(2024, 3, 1), None)[1] == "date"
    # same datetime value, distinguished by the cell's number format
    dtv = dt.datetime(2024, 3, 1, 9, 30)
    assert _render(dtv, None, "yyyy-mm-dd") == ("2024-03-01", "date")
    assert _render(dtv, None, "yyyy-mm-dd h:mm") == ("2024-03-01 09:30:00", "datetime")
    assert _render(dt.time(9, 30), None) == ("09:30:00", "time")
    assert _render(None, None) == (None, "str")  # truly empty
    assert _render(None, "=SUM(A1)") == ("=SUM(A1)", "formula")  # no cache → literal


def test_empty_run_guard(tmp_path: Path):
    wb = Workbook()
    ws = wb.active
    ws.append(["姓名"])
    for _ in range(100):  # under the 200-guard: parsing must reach the data
        ws.append([])
    ws.append(["张三"])
    wb.save(tmp_path / "g.xlsx")
    doc = parse_document(tmp_path / "g.xlsx")
    assert "张三" in doc.text


def test_all_empty_workbook(tmp_path: Path):
    wb = Workbook()
    wb.create_sheet("也是空")
    wb.save(tmp_path / "e.xlsx")
    doc = parse_document(tmp_path / "e.xlsx")
    assert doc.blocks == []
    assert doc.text == ""
