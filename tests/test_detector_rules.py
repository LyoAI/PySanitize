"""Rule-based detector: regex fields, checksum gates, name/company heuristics."""

from __future__ import annotations

from pysanitize.detector.rules import RuleDetector
from pysanitize.detector.specs import load_field_specs
from pysanitize.masker.text import TextMasker


def _by_type(doc, **kw):
    """Run the rules detector, then mask — so ``masked_value`` is populated."""
    detections = RuleDetector(**kw).detect(doc)
    mask_map = {name: spec.mask for name, spec in load_field_specs().items()}
    TextMasker(mask_map).mask(doc.text, detections)
    return {d.field_type: d for d in detections}


def test_load_field_specs_falls_back_to_builtin_defaults():
    # a fresh clone / CI has no config/fields.yaml → built-in defaults apply
    specs = load_field_specs("/nonexistent/fields.yaml")
    assert set(specs) >= {
        "phone", "id_card", "email", "credit_code",
        "stock_code", "person_name", "company_name",
    }
    assert specs["phone"].mask.mask("13812345678") == "138****5678"
    assert specs["id_card"].compiled is not None  # regex compiled from the default


def test_phone(make_doc):
    doc = make_doc([("paragraph", "联系电话 13812345678 谢谢", 1)])
    d = _by_type(doc)["phone"]
    assert d.value == "13812345678"
    assert d.masked_value == "138****5678"
    assert doc.text[d.start : d.end] == "13812345678"


def test_id_card_checksum_gate(make_doc):
    doc = make_doc([("paragraph", "身份证号 110105199003071239。", 1)])
    d = _by_type(doc)["id_card"]
    assert d.masked_value == "110105********1239"

    # A structurally-valid but checksum-failing ID must be rejected.
    bad = make_doc([("paragraph", "身份证号 110105199003071234。", 1)])
    assert "id_card" not in _by_type(bad)

    # ...unless checksum verification is disabled.
    off = _by_type(bad, verify_checksums=False)
    assert "id_card" in off


def test_email(make_doc):
    doc = make_doc([("paragraph", "联系 user.name@example.com 尽快", 1)])
    d = _by_type(doc)["email"]
    assert d.masked_value == "****@***"
    assert doc.text[d.start : d.end] == "user.name@example.com"


def test_credit_code(make_doc):
    doc = make_doc([("paragraph", "信用代码 91310000MA1FL0000N 有效", 1)])
    d = _by_type(doc)["credit_code"]
    assert d.masked_value == "*" * 14 + "000N"  # keep_tail=4 of an 18-char code


def test_stock_code(make_doc):
    doc = make_doc([("paragraph", "贵州茅台 600519 大涨", 1)])
    d = _by_type(doc)["stock_code"]
    assert d.masked_value == "******"


def test_bank_account_disabled_by_default(make_doc):
    doc = make_doc([("paragraph", "账号 6222021000012345678 余额", 1)])
    assert "bank_account" not in _by_type(doc)
    # re-enable via fields
    d = _by_type(doc, fields=["bank_account"])["bank_account"]
    assert d.masked_value == "6222" + "*" * 11 + "5678"  # 19 digits, keep 4+4


def test_person_name_context(make_doc):
    doc = make_doc([("paragraph", "联系人：张三，电话 13812345678", 1)])
    d = _by_type(doc)["person_name"]
    assert d.value == "张三"
    assert d.masked_value == "***"


def test_person_name_blacklist_roles(make_doc):
    # "经办人" without a following name is a role label, not a person name
    doc = make_doc([("paragraph", "由经办人办理", 1)])
    assert "person_name" not in _by_type(doc)
    # with a real name after the label, the name IS detected
    doc2 = make_doc([("paragraph", "经办人：王五", 1)])
    d = _by_type(doc2).get("person_name")
    assert d is not None and d.value == "王五"


def test_company_name(make_doc):
    doc = make_doc([("paragraph", "甲方：北京某某科技有限公司", 1)])
    d = _by_type(doc)["company_name"]
    assert d.value == "北京某某科技有限公司"
    assert d.masked_value == "****"


def test_company_name_inside_curly_quotes(make_doc):
    """Regression: “” must be boundary chars — a mangled string literal once
    dropped them, silently missing companies wrapped in Chinese quotes."""
    doc = make_doc([("paragraph", "甲方为“北京某某科技有限公司”，乙方为张三。", 1)])
    d = _by_type(doc)["company_name"]
    assert d.value == "北京某某科技有限公司"


def test_company_industry_noise_rejected(make_doc):
    # industry nouns like 银行/证券 only count when followed by a company tail
    for noise in ("银行业", "保险产品", "网上银行", "银行卡号", "全资子公司"):
        doc = make_doc([("paragraph", f"讨论{noise}的情况", 1)])
        assert "company_name" not in _by_type(doc), noise
