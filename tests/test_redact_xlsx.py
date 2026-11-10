"""XLSX redaction: cell resolve, write-back, scrubbing, byte verify, recovery."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment

from pysanitize.parser.document import parse_document
from pysanitize.pipeline import sanitize_document
from pysanitize.redact import (
    redact_xlsx,
    resolve_cells,
    verify_redaction_xlsx,
)
from pysanitize.recover import recover_file


@pytest.fixture
def xlsx(tmp_path: Path) -> Path:
    """The adversarial workbook: text + number phones, an ID card, a formula
    echoing a name, a PII comment, PII docProps, a hidden sheet, an empty
    sheet wedged between (worksheet-index trap) and a PII sheet name."""
    wb = Workbook()
    ws = wb.active
    ws.title = "张三工资表"
    ws.append(["姓名", "手机号", "身份证号", "入职日期", "备注"])
    ws.append(["张三", 13812345678, "110101199003078515", dt.date(2024, 3, 1), "手机号同 13812345678"])
    ws["B2"].number_format = "0"
    ws["F1"] = '=CONCATENATE(B2," 发给张三")'
    ws["E2"].comment = Comment("紧急联系人张三 13900001111", "hr")
    wb.properties.creator = "刘佳楠"
    wb.properties.title = "张三的工资"
    wb.create_sheet("空表")
    hidden = wb.create_sheet("薪资明细")
    hidden.sheet_state = "hidden"
    hidden.append(["姓名", "工资"])
    hidden.append(["张三", 25000.5])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path


def _detect(doc):
    from pysanitize.detector.rules import RuleDetector
    from pysanitize.detector.specs import load_field_specs

    return RuleDetector(specs=load_field_specs()).detect(doc)


def _mask_map():
    from pysanitize.detector.specs import load_field_specs

    return {n: s.mask for n, s in load_field_specs().items()}


def _run_redact(xlsx: Path, out: Path):
    doc = parse_document(xlsx)
    detections = _detect(doc)
    edits = resolve_cells(doc, detections, _mask_map())
    redact_xlsx(xlsx, edits, out, values=[d.value for d in detections])
    return doc, detections, edits


def test_resolve_targets_exact_cells_including_hidden_sheet(xlsx: Path, tmp_path: Path):
    doc, detections, edits = _run_redact(xlsx, tmp_path / "out.xlsx")
    by_key = {(e.sheet_idx, e.row, e.col): e for e in edits}
    # 员工 = worksheet 0 (张三 A2, phone B2, id C2, phone-in-remark E2);
    # 薪资明细 = worksheet 2 — the empty sheet in between must not shift it
    assert (0, 2, 1) in by_key and (0, 2, 2) in by_key and (0, 2, 3) in by_key
    assert (2, 2, 1) in by_key
    assert all(e.sheet_idx != 1 for e in edits)  # nothing lands on the empty sheet


def test_redacted_workbook_values_and_types(xlsx: Path, tmp_path: Path):
    _run_redact(xlsx, tmp_path / "out.xlsx")
    out = load_workbook(tmp_path / "out.xlsx")
    emp, sal = out["□□□工资表"], out["薪资明细"]  # sheet name scrubbed, Excel-legal
    assert emp["A2"].value == "***"
    assert emp["B2"].value == "138****5678"  # number cell → masked string
    assert emp["C2"].value == "110101********8515"
    assert emp["E2"].value == "手机号同 138****5678"
    assert emp["D2"].value == dt.datetime(2024, 3, 1)  # untouched keeps its type
    assert sal["A2"].value == "***"
    assert sal["B2"].value == 25000.5  # non-sensitive number untouched
    assert sal.sheet_state == "hidden"  # visibility preserved
    # formula literal echoing a detected name is scrubbed by value
    assert emp["F1"].value == '=CONCATENATE(B2," 发给***")'
    # comment echoing detected values is scrubbed too
    assert emp["E2"].comment.text == "紧急联系人*** 13900001111"
    # docProps are always scrubbed (empty strings round-trip as None)
    assert out.properties.creator == "PySanitize"
    assert not out.properties.title


def test_verify_is_byte_level_and_unesccapes(xlsx: Path, tmp_path: Path):
    _, detections, _ = _run_redact(xlsx, tmp_path / "out.xlsx")
    values = [d.value for d in detections]
    assert verify_redaction_xlsx(tmp_path / "out.xlsx", values) == []

    # Plant a leak: a chart cache member (openpyxl-built files have none, so
    # inject one) carrying a value as numeric character references — the form
    # a raw grep silently misses.
    out = tmp_path / "out.xlsx"
    planted = tmp_path / "planted.xlsx"
    with zipfile.ZipFile(out) as zf, zipfile.ZipFile(planted, "w") as zw:
        for item in zf.infolist():
            if item.filename == "xl/charts/chart1.xml":
                continue
            zw.writestr(item, zf.read(item.filename))
        refs = "".join(f"&#{ord(c)};" for c in "张三")
        zw.writestr(
            "xl/charts/chart1.xml",
            f'<c:chart xmlns:c="r"><c:strCache><c:pt><c:v>{refs}</c:v></c:pt></c:strCache></c:chart>',
        )
    leftovers = verify_redaction_xlsx(planted, values)
    assert ("张三", "xl/charts/chart1.xml") in leftovers


def test_verify_ignores_binary_members(xlsx: Path, tmp_path: Path):
    _, detections, _ = _run_redact(xlsx, tmp_path / "out.xlsx")
    out = tmp_path / "out.xlsx"
    with zipfile.ZipFile(out) as zf, zipfile.ZipFile(tmp_path / "bin.xlsx", "w") as zw:
        for item in zf.infolist():
            zw.writestr(item, zf.read(item.filename))
        zw.writestr("xl/media/image1.png", b"\x89PNG" + "张三".encode("utf-8"))
    assert verify_redaction_xlsx(tmp_path / "bin.xlsx", [d.value for d in detections]) == []


def test_drop_chart_caches():
    """Excel-written charts cache source labels/values (strCache/numCache) —
    copies that survive cell masking. The scrub nulls both cache shapes."""
    from openpyxl.chart.data_source import (
        AxDataSource,
        NumData,
        NumDataSource,
        NumRef,
        NumVal,
        StrData,
        StrRef,
        StrVal,
    )

    num_ref = NumRef(f="S!$B$2")
    num_ref.numCache = NumData(pt=[NumVal(idx=0, v="3")], formatCode="General")
    str_ref = StrRef(f="S!$A$2")
    str_ref.strCache = StrData(pt=[StrVal(idx=0, v="张三部")])

    class _Ser:  # the shape _drop_chart_caches walks (chart.series entry)
        val = NumDataSource(numRef=num_ref)
        cat = AxDataSource(strRef=str_ref)

    class _Chart:
        series = [_Ser()]

    class _WS:
        _charts = [_Chart()]

    class _WB:
        worksheets = [_WS()]

    from pysanitize.redact.xlsx import _drop_chart_caches

    _drop_chart_caches(_WB())
    assert num_ref.numCache is None
    assert str_ref.strCache is None


def test_sheet_name_scrub_keeps_uniqueness(tmp_path: Path):
    wb = Workbook()
    wb.active.title = "张三工资"
    wb.create_sheet("□□□工资")  # collides once 张三 → □□□
    wb.save(tmp_path / "n.xlsx")
    from pysanitize.redact.xlsx import _scrub_sheet_names

    n = _scrub_sheet_names(wb, ["张三"])
    assert n == 1
    titles = [ws.title for ws in wb.worksheets]
    assert len(titles) == len(set(titles))  # Excel rejects duplicate tabs
    assert "□□□工资" in titles


# -- end to end: the real pipeline (native parse, rules detector, no MinerU) --


def test_sanitize_document_xlsx_end_to_end(xlsx: Path, tmp_path: Path):
    result = sanitize_document(xlsx, detector="rules", out_dir=tmp_path / "out")
    assert result.redacted_xlsx.is_file()
    assert result.redacted_pdf is None
    # structured sources ship no markdown — the redacted workbook IS the output
    assert result.sanitized_md is None
    assert not (tmp_path / "out" / "sanitized.md").exists()

    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert "13812345678" not in json.dumps(audit, ensure_ascii=False)
    assert audit["redaction"]["xlsx"] == "redacted.xlsx"
    assert audit["redaction"]["regions"] == len(result.detections)

    # the source file is never modified in place
    src = load_workbook(xlsx)
    assert src["张三工资表"]["A2"].value == "张三"


def test_recoverable_round_trip_is_exact(xlsx: Path, tmp_path: Path):
    result = sanitize_document(
        xlsx,
        detector="rules",
        recoverable=True,
        recover_key="pw",
        out_dir=tmp_path / "out",
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    spans = audit["masked_spans"]
    assert spans and all("cells" in s for s in spans)
    assert all("idx" in c and "kind" in c for s in spans for c in s["cells"])
    # every cell carries the placeholder's exact position in the masked value —
    # recovery splices by offset, never by string matching
    assert all(
        isinstance(c["masked_start"], int) and c["masked_end"] == c["masked_start"] + len(s["masked_value"])
        for s in spans for c in s["cells"]
    )

    rec = recover_file(result.redacted_xlsx, passphrase="pw")
    assert rec.kind == "xlsx" and rec.unresolved == 0 and rec.restored > 0

    rb = load_workbook(rec.output)
    emp, sal = rb.worksheets[0], rb.worksheets[2]
    assert emp["A2"].value == "张三"
    assert emp["B2"].value == 13812345678  # int cell restored as int, not "13812345678"
    assert isinstance(emp["B2"].value, int)
    assert emp["C2"].value == "110101199003078515"
    assert emp["D2"].value == dt.datetime(2024, 3, 1)
    assert sal["A2"].value == "张三" and sal["B2"].value == 25000.5


def test_multi_span_cell_restores_by_offset(xlsx: Path, tmp_path: Path):
    """One cell, two detections, plus a literal ``***`` planted *before* them.

    String matching would restore the literal's first occurrence and corrupt
    the cell; recorded offsets put each value back at its own spot.
    """
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "备注:*** 原始占位 张三 13812345678"
    path = tmp_path / "m.xlsx"
    wb.save(path)

    result = sanitize_document(
        path, detector="rules", recoverable=True, recover_key="pw", out_dir=tmp_path / "out"
    )
    values = [d.value for d in result.detections]
    assert "张三" in values and "13812345678" in values
    masked = load_workbook(result.redacted_xlsx)["Sheet"]["A1"].value
    assert masked.count("***") == 3  # the literal + two placeholders, indistinguishable

    rec = recover_file(result.redacted_xlsx, passphrase="pw")
    assert rec.unresolved == 0
    restored = load_workbook(rec.output)["Sheet"]["A1"].value
    assert restored == "备注:*** 原始占位 张三 13812345678"


def test_resolve_repl_masked_offsets_are_consistent(xlsx: Path, tmp_path: Path):
    """Each repl's masked span in the final value is pure arithmetic — splicing
    by relative offsets and by masked offsets yields the same string."""
    doc, detections, edits = _run_redact(xlsx, tmp_path / "out.xlsx")
    assert edits
    for e in edits:
        rel_spliced = e.text
        for rs, re_, masked, *_ in sorted(e.repls, key=lambda r: -r[0]):
            rel_spliced = rel_spliced[:rs] + masked + rel_spliced[re_:]
        for rs, re_, masked, ds, de, ms, me in e.repls:
            assert ms >= 0 and me == ms + len(masked)
            assert rel_spliced[ms:me] == masked  # both splice paths agree


def test_legacy_audit_without_offsets_still_recovers(xlsx: Path, tmp_path: Path):
    """Audits recorded before the offset upgrade (no masked_start) recover via
    the old left-to-right match — old artifacts stay restorable."""
    result = sanitize_document(
        xlsx, detector="rules", recoverable=True, recover_key="pw", out_dir=tmp_path / "out"
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    for s in audit["masked_spans"]:
        for c in s.get("cells", []):
            c.pop("masked_start", None)
            c.pop("masked_end", None)
    stripped = tmp_path / "legacy_audit.json"
    stripped.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    rec = recover_file(result.redacted_xlsx, audit_path=stripped, passphrase="pw")
    assert rec.unresolved == 0
    rb = load_workbook(rec.output)
    assert rb.worksheets[0]["A2"].value == "张三"
    assert rb.worksheets[0]["B2"].value == 13812345678
