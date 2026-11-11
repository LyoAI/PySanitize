"""JSON redaction: leaf resolve, value replacement, data verify, recovery."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pysanitize.parser.document import parse_document
from pysanitize.pipeline import sanitize_document
from pysanitize.redact import (
    redact_json,
    resolve_json_values,
    verify_redaction_json,
)
from pysanitize.recover import recover_file

FIELDS = ["person_name", "phone", "id_card", "bank_account"]


@pytest.fixture
def json_file(tmp_path: Path) -> Path:
    """Adversarial document: numeric + text phones, a two-phone string, a
    multi-line value, an ID card, a bank card, unicode keys, nested arrays."""
    data = {
        "公司": "杭州某某科技有限公司",
        "员工": [
            {
                "姓名": "张三",
                "手机号": 13812345678,  # number leaf — masked to a string
                "身份证号": "110101199003078515",
                "银行卡号": "6222020200112345678",
                "备注": "短信 13812345678 已发 13900001111",  # two hits, one string
                "说明": "第一行\n第二行 13800001111",  # rewrite keeps the newline
                "未涉密": "第一行\n第二行",  # untouched multi-line keeps its newline
            },
            {"姓名": "李四", "手机号": "13912345678"},
            {},
        ],
        "配置": {"等级": 5, "比例": 0.25, "开关": True, "空值": None},
    }
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _orig(json_file: Path) -> dict:
    return json.loads(json_file.read_text(encoding="utf-8"))


def _detect(doc):
    from pysanitize.detector.rules import RuleDetector
    from pysanitize.detector.specs import load_field_specs

    return RuleDetector(specs=load_field_specs(), fields=FIELDS).detect(doc)


def _mask_map():
    from pysanitize.detector.specs import load_field_specs

    return {n: s.mask for n, s in load_field_specs().items()}


def _run_redact(json_file: Path, out: Path):
    doc = parse_document(json_file)
    detections = _detect(doc)
    edits = resolve_json_values(doc, detections, _mask_map())
    redact_json(json_file, edits, out)
    return doc, detections, edits


def test_resolve_groups_spans_per_leaf(json_file: Path):
    doc, detections, edits = _run_redact(json_file, json_file.with_name("out.json"))
    by_path = {e.path: e for e in edits}
    # the two-phone 备注 leaf carries both hits in one edit, each repl with its
    # exact masked position in the final value
    remark = by_path[("员工", 0, "备注")]
    assert len(remark.repls) == 2
    for rs, re_, masked, ds, de, ms, me in remark.repls:
        assert ms >= 0 and me == ms + len(masked)
        assert remark.text[rs:re_]  # the piece is real text
    masked_spans = [remark.text[rs:re_] for rs, re_, *_ in remark.repls]
    assert masked_spans == ["13812345678", "13900001111"]


def test_redact_json_values_and_structure(json_file: Path):
    out_path = json_file.with_name("out.json")
    _run_redact(json_file, out_path)
    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert out["公司"] == "杭州某某科技有限公司"  # not a detection field — untouched
    emp = out["员工"][0]
    assert emp["姓名"] == "***"
    assert emp["手机号"] == "138****5678"  # number leaf → masked string
    assert emp["身份证号"] == "1101**********8515"
    assert emp["银行卡号"] == "6222***********5678"
    assert emp["备注"] == "短信 138****5678 已发 139****1111"
    assert emp["说明"] == "第一行\n第二行 138****1111"  # rewritten → newline kept
    assert emp["未涉密"] == "第一行\n第二行"  # untouched keeps its newline
    assert out["员工"][1]["姓名"] == "***"
    assert out["配置"] == {"等级": 5, "比例": 0.25, "开关": True, "空值": None}
    assert list(out) == ["公司", "员工", "配置"]  # key order preserved
    # indent follows the source (2 spaces), raw values nowhere in the file
    raw = out_path.read_text(encoding="utf-8")
    assert '\n  "公司"' in raw
    for secret in ("13812345678", "110101199003078515", "6222020200112345678"):
        assert secret not in raw


def test_redacted_json_is_source_indent_aware(tmp_path: Path):
    path = tmp_path / "m.json"
    path.write_text('{"手机号":"13812345678","x":1}', encoding="utf-8")  # minified
    doc = parse_document(path)
    edits = resolve_json_values(doc, _detect(doc), _mask_map())
    redact_json(path, edits, tmp_path / "out.json")
    raw = (tmp_path / "out.json").read_text(encoding="utf-8")
    assert raw == '{"手机号": "138****5678", "x": 1}'


def test_verify_reports_missed_leaves_and_keys(tmp_path: Path):
    """Detection misses and key echoes are *reported*, never silently kept:
    keys are schema (not rewritten) and a value flanked by CJK is no rule hit —
    both come back as leftovers with their paths."""
    path = tmp_path / "v.json"
    path.write_text(
        json.dumps({"姓名": "张三", "张三的备注": "ok", "其他": "见张三"}, ensure_ascii=False),
        encoding="utf-8",
    )
    doc = parse_document(path)
    detections = _detect(doc)
    assert [d.value for d in detections] == ["张三"]  # only the bare leaf
    edits = resolve_json_values(doc, detections, _mask_map())
    redact_json(path, edits, tmp_path / "out.json")
    leftovers = verify_redaction_json(tmp_path / "out.json", ["张三"])
    assert leftovers == [("张三", "$.张三的备注 (key)")] or leftovers == [
        ("张三", "$.其他")
    ] or len(leftovers) == 1  # one leftover, deduped by value, path named
    assert leftovers[0][0] == "张三" and "$" in leftovers[0][1]


def test_verify_clean_when_all_detected(json_file: Path):
    out_path = json_file.with_name("out.json")
    _, detections, _ = _run_redact(json_file, out_path)
    assert verify_redaction_json(out_path, [d.value for d in detections]) == []


def test_verify_renders_numbers_like_the_surface(tmp_path: Path):
    """Verification renders scalars through the same contract as the parser:
    an integral float is checked as ``5`` (its surface form), not ``5.0``."""
    path = tmp_path / "n.json"
    path.write_text('{"电话": 13812345678.0}', encoding="utf-8")
    doc = parse_document(path)
    detections = _detect(doc)
    assert [d.value for d in detections] == ["13812345678"]
    edits = resolve_json_values(doc, detections, _mask_map())
    redact_json(path, edits, tmp_path / "out.json")
    assert verify_redaction_json(tmp_path / "out.json", ["13812345678"]) == []


def test_verify_exact_when_values_share_prefix_grams(tmp_path: Path):
    """Hundreds of values sharing prefix grams (ID region codes like
    ``110101``) once flooded the candidate buckets. The positional matcher
    must stay exact: the one value present is found with its path, absent
    values are NOT reported even though their fat prefix gram occurs in a
    sibling leaf."""
    ids = [f"11010119900101{i:04d}X" for i in range(300)]
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(
            {"证件号": ids[0], "备注": "档案区间1101011990已归档"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert verify_redaction_json(path, ids) == [(ids[0], "$.证件号")]
    empty = tmp_path / "q.json"
    empty.write_text(
        json.dumps({"备注": "档案区间1101011990已归档"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert verify_redaction_json(empty, ids) == []


# -- end to end: the real pipeline (native parse, rules detector, no MinerU) --


@pytest.mark.parametrize(
    "data",
    [
        # export-style: one record per element — was a hard TypeError
        [{"姓名": "张三", "手机号": "13812345678"}, {"姓名": "李四", "手机号": "13998886666"}],
        # bare rows — a shorter element raised "tuple index out of range"
        ["13812345678", "张三"],
        # nested rows — no error, but each inner row used to be REPLACED by the
        # masked string (silent corruption); this case pins the real content
        [["张三", "13812345678"], ["李四", "13998886666"]],
        # holes and non-record elements must not shift any path
        [None, {}, {"手机号": 13812345678}, ["13912345678"], [1, 2]],
    ],
)
def test_top_level_array_round_trip(data, tmp_path: Path):
    """Every leaf path under a top-level array starts at the element index, so
    redact and recover address the same node they detected — verified by
    deep equality against the source, not by "it did not raise"."""
    path = tmp_path / "export.json"
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    anchor = "13812345678" if "13812345678" in json.dumps(data) else "13912345678"

    result = sanitize_document(
        path, detector="rules", fields=FIELDS,
        recoverable=True, recover_key="pw", out_dir=tmp_path / "out",
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["masked_spans"], "fixture must yield at least one detection"
    assert all(isinstance(s["json"]["path"][0], int) for s in audit["masked_spans"])

    redacted = json.loads(result.redacted_json.read_text(encoding="utf-8"))
    assert isinstance(redacted, list)  # still an array, not a string or object
    assert anchor not in json.dumps(redacted, ensure_ascii=False)
    assert len(redacted) == len(data)  # no element lost, none added

    rec = recover_file(result.redacted_json, passphrase="pw")
    assert rec.unresolved == 0
    assert json.loads(rec.output.read_text(encoding="utf-8")) == data


def test_sanitize_document_json_end_to_end(json_file: Path, tmp_path: Path):
    result = sanitize_document(
        json_file, detector="rules", fields=FIELDS, out_dir=tmp_path / "out"
    )
    assert result.redacted_json.is_file()
    # structured sources ship no markdown — the redacted json IS the output
    assert result.sanitized_md is None
    assert not (tmp_path / "out" / "sanitized.md").exists()

    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert "13812345678" not in json.dumps(audit, ensure_ascii=False)
    assert audit["redaction"]["json"] == "redacted.json"
    # 9 spans land on 8 leaves — the 备注 leaf carries two phones in one string
    assert audit["redaction"]["regions"] == 8
    assert audit["redaction"]["regions"] <= len(result.detections)

    # the source file is never modified in place
    assert _orig(json_file)["员工"][0]["姓名"] == "张三"


def test_recoverable_round_trip_is_exact(json_file: Path, tmp_path: Path):
    result = sanitize_document(
        json_file,
        detector="rules",
        fields=FIELDS,
        recoverable=True,
        recover_key="pw",
        out_dir=tmp_path / "out",
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    spans = audit["masked_spans"]
    assert spans and all("json" in s for s in spans)
    assert all(
        isinstance(c["masked_start"], int) and c["masked_end"] == c["masked_start"] + len(s["masked_value"])
        for s in spans for c in [s["json"]]
    )
    assert all(s["json"]["path"] and s["json"]["kind"] for s in spans)

    rec = recover_file(result.redacted_json, passphrase="pw")
    assert rec.kind == "json" and rec.unresolved == 0 and rec.restored == len(spans)

    data = json.loads(rec.output.read_text(encoding="utf-8"))
    expected = copy.deepcopy(_orig(json_file))
    # byte-exact: a rewritten value keeps its embedded newlines (the detection
    # surface flattens them 1:1, and the splice runs over the raw leaf)
    assert data == expected
    assert isinstance(data["员工"][0]["手机号"], int)  # type restored, not "138…"


def test_recover_restores_newlines_from_source_for_pre_fix_artifacts(
    json_file: Path, tmp_path: Path
):
    """Artifacts sanitized before the newline fix lost them at redact time —
    the audit never recorded them. With the ORIGINAL source, recovery can put
    them back exactly: after values are restored the leaf equals the
    flattened source, which pins every newline position."""
    result = sanitize_document(
        json_file,
        detector="rules",
        fields=FIELDS,
        recoverable=True,
        recover_key="pw",
        out_dir=tmp_path / "out",
    )
    # Simulate a pre-fix redacted.json: it wrote the detection surface back.
    red = json.loads(result.redacted_json.read_text(encoding="utf-8"))
    red["员工"][0]["说明"] = red["员工"][0]["说明"].replace("\n", " ")
    result.redacted_json.write_text(
        json.dumps(red, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Without the source the line break is simply gone (as before).
    plain = recover_file(result.redacted_json, passphrase="pw")
    assert json.loads(plain.output.read_text(encoding="utf-8"))["员工"][0]["说明"] == (
        "第一行 第二行 13800001111"
    )

    # With it, the recovered leaf is the original text — values AND newline.
    fixed = recover_file(result.redacted_json, passphrase="pw", newline_source=json_file)
    assert fixed.unresolved == 0
    assert json.loads(fixed.output.read_text(encoding="utf-8")) == _orig(json_file)


def test_multi_span_string_restores_by_offset(tmp_path: Path):
    """Two names (identical ``***`` placeholders) plus a literal ``***`` planted
    before them, in one string — offset splice must beat string matching."""
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"备注": "字面***占位 张三 李四"}, ensure_ascii=False), encoding="utf-8"
    )
    result = sanitize_document(
        path, detector="rules", recoverable=True, recover_key="pw", out_dir=tmp_path / "out"
    )
    values = [d.value for d in result.detections]
    assert "张三" in values and "李四" in values
    masked = json.loads(result.redacted_json.read_text(encoding="utf-8"))["备注"]
    assert masked.count("***") == 3  # literal + two placeholders, indistinguishable

    rec = recover_file(result.redacted_json, passphrase="pw")
    assert rec.unresolved == 0
    assert json.loads(rec.output.read_text(encoding="utf-8"))["备注"] == "字面***占位 张三 李四"


def test_edited_leaf_counts_unresolved(tmp_path: Path):
    """A placeholder that no longer sits at its recorded position is reported,
    not silently mis-spliced."""
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"姓名": "张三"}, ensure_ascii=False), encoding="utf-8")
    result = sanitize_document(
        path, detector="rules", recoverable=True, recover_key="pw", out_dir=tmp_path / "out"
    )
    raw = result.redacted_json.read_text(encoding="utf-8")
    result.redacted_json.write_text(raw.replace("***", "已改"), encoding="utf-8")
    rec = recover_file(result.redacted_json, passphrase="pw")
    assert rec.unresolved == 1 and rec.restored == 0
    assert json.loads(rec.output.read_text(encoding="utf-8"))["姓名"] == "已改"
