"""Placeholder splicing: ordering, duplicate ranges, and the many-hit case."""

from __future__ import annotations

from pysanitize.utils.splice import apply_repls


def test_no_repls_returns_text_unchanged():
    assert apply_repls("abc", []) == "abc"


def test_repls_apply_regardless_of_input_order():
    text = "短信 13812345678 已发 13900001111"
    repls = [(18, 29, "139****1111"), (3, 14, "138****5678")]
    assert apply_repls(text, repls) == "短信 138****5678 已发 139****1111"


def test_duplicate_range_last_recorded_wins():
    """An ID card also matches the bank-card rule — two repls, one range. The
    per-replacement splice applied the later one, so the join must agree."""
    text = "110101199003078515"
    repls = [(0, 18, "110101********8515"), (0, 18, "1101**********8515")]
    assert apply_repls(text, repls) == "1101**********8515"


def test_replacement_containing_a_lookalike_placeholder_survives():
    """Restoring by position must not depend on the replacement's content."""
    text = "*** 张三 ***"
    assert apply_repls(text, [(4, 6, "***")]) == "*** *** ***"


def test_many_hits_in_one_long_leaf_are_exact():
    """The shape that made redaction quadratic: one huge leaf, many spans."""
    n = 5000
    plain = "定性检查未见异常。"
    sensitive = [f"138{i:08d}" for i in range(n)]
    text = plain + "".join(f"客户 {s} 已回访。" for s in sensitive) + plain
    repls = []
    pos = len(plain)
    for s in sensitive:
        seg = f"客户 {s} 已回访。"
        at = pos + 3
        repls.append((at, at + 11, s[:3] + "****" + s[-4:]))
        pos += len(seg)
    out = apply_repls(text, repls)
    assert len(repls) == n
    assert out.startswith(plain) and out.endswith(plain)
    assert not any(s in out for s in sensitive)
    assert out.count("****") == n
