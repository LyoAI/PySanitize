"""Text masking: fixed templates, partial masks, overlap merge, no leakage."""

from __future__ import annotations

from pysanitize.detector.base import Detection
from pysanitize.detector.specs import MaskSpec
from pysanitize.masker.text import mask_text

SPECS = {
    "phone": MaskSpec(keep_head=3, keep_tail=4, mask_char="*"),
    "company": MaskSpec(template="****"),
    "email": MaskSpec(template="****@***"),
    "person_name": MaskSpec(template="***"),
}


def D(field, value, start, end):
    return Detection(field_type=field, value=value, start=start, end=end, page=1)


def test_template_and_partial():
    text = "公司 北京某某科技有限公司 电话 13812345678"
    i = text.index("北京某某科技有限公司")
    out = mask_text(text, [D("company", "北京某某科技有限公司", i, i + 10)], SPECS)
    assert out == "公司 **** 电话 13812345678"
    # partial keep-head/tail
    j = text.index("13812345678")
    out2 = mask_text(text, [D("phone", "13812345678", j, j + 11)], SPECS)
    assert out2 == "公司 北京某某科技有限公司 电话 138****5678"


def test_multiple_spans_left_to_right():
    text = "13812345678 然后 a@b.com"
    i = text.index("13812345678")
    j = text.index("a@b.com")
    dets = [D("phone", "13812345678", i, i + 11), D("email", "a@b.com", j, j + len("a@b.com"))]
    out = mask_text(text, dets, SPECS)
    assert out == "138****5678 然后 ****@***"


def test_overlapping_spans_merge_not_double_mask():
    # company contains person span — person mask must not appear
    text = "北京某某科技有限公司"
    dets = [
        D("company", "北京某某科技有限公司", 0, 10),
        D("person_name", "某某", 2, 4),
    ]
    out = mask_text(text, dets, SPECS)
    assert out == "****"


def test_masked_value_filled_for_audit():
    text = "电话 13812345678"
    i = text.index("13812345678")
    det = D("phone", "13812345678", i, i + 11)
    mask_text(text, [det], SPECS)
    assert det.masked_value == "138****5678"


def test_no_leak_end_to_end():
    text = "联系人：张三，电话 13812345678，公司 北京某某科技有限公司。"
    a = text.index("张三")
    b = text.index("13812345678")
    c = text.index("北京某某科技有限公司")
    dets = [
        D("person_name", "张三", a, a + 2),
        D("phone", "13812345678", b, b + 11),
        D("company", "北京某某科技有限公司", c, c + 10),
    ]
    out = mask_text(text, dets, SPECS)
    assert "张三" not in out
    assert "13812345678" not in out
    assert "北京某某科技有限公司" not in out


def test_short_value_never_emitted_unmasked():
    # keep_head+keep_tail can cover a short value entirely (e.g. a 7-digit
    # hotline with keep 3+4); the mask must still hide something.
    assert SPECS["phone"].mask("9555526") == "955****"
    # a value shorter than keep_head still masks at least one char
    assert SPECS["phone"].mask("12") == "1*"
    assert SPECS["phone"].mask("9") == "*"
    # the normal long-value mask is unchanged
    assert SPECS["phone"].mask("13812345678") == "138****5678"
