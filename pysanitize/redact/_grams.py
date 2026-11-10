"""N-gram substring prefilter shared by the json/xlsx redactors.

Checking every detection value against every text is quadratic and dominates
large documents (36k values × 72k json leaves ≈ minutes; 63k values × 15
xlsx zip members ≈ more). The index maps each value's grams to the value;
any text containing a value necessarily contains that value's indexed grams,
so walking the text's grams yields a candidate superset — exact, with the
per-text work dropping from all-values to hits-only.

Long values (≥5 chars — phones, IDs, bank cards) index 4-grams, short ones
(2–4 chars — CJK names) 2-grams. The split matters: every digit value shares
2-grams like ``13``, so digit buckets under a 2-gram index stay fat enough
to go quadratic again; 4-gram digit buckets are tiny.
"""

from __future__ import annotations


def gram_index(
    values: list[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """Build ``(grams2, grams4, short)`` from deduped ``values``.

    ``grams2``/``grams4`` map each 2-/4-gram to the list of values containing
    it; values shorter than 2 chars come back in ``short`` for direct
    checking. Lists are append-ordered with a tail-dedup, so a value never
    repeats inside one bucket.
    """
    grams2: dict[str, list[str]] = {}
    grams4: dict[str, list[str]] = {}
    short: list[str] = []
    for v in values:
        if len(v) < 2:
            short.append(v)
            continue
        if len(v) >= 5:
            for i in range(len(v) - 3):
                bucket = grams4.setdefault(v[i : i + 4], [])
                if not bucket or bucket[-1] != v:
                    bucket.append(v)
        else:
            for i in range(len(v) - 1):
                bucket = grams2.setdefault(v[i : i + 2], [])
                if not bucket or bucket[-1] != v:
                    bucket.append(v)
    return grams2, grams4, short


def gram_candidates(
    text: str, grams2: dict[str, list[str]], grams4: dict[str, list[str]]
) -> set[str]:
    """Values whose indexed grams occur in ``text``.

    A superset of the values that are substrings of ``text`` — membership
    still needs the real ``v in text`` check.
    """
    cands: set[str] = set()
    for i in range(len(text) - 1):
        bucket = grams2.get(text[i : i + 2])
        if bucket:
            cands.update(bucket)
        if i + 4 <= len(text):
            bucket = grams4.get(text[i : i + 4])
            if bucket:
                cands.update(bucket)
    return cands
