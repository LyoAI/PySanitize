"""Substring prefilter shared by the json/xlsx redactors.

Checking every detection value against every text is quadratic and dominates
large documents (200k values × 36k long json leaves ≈ minutes). The first
attempt collected per-text candidates from n-gram buckets — but real value
sets collide on prefix grams: ID cards share the region prefix (``1101``),
phones share carrier prefixes, names share surname bigrams, so one bucket
can hold thousands of values and every text position hitting it pulled all
of them into full-text substring searches — quadratic again, one level down.

So candidates are verified *positionally* instead. Each value is indexed by
its globally RAREST gram (4-grams for values ≥5 chars — phones, IDs, bank
cards; 2-grams for 2–4-char CJK values — names; the split matters because
every digit value shares 2-grams like ``13``, which would keep digit buckets
fat). A text is scanned once: a gram hit names only the values whose rarest
gram sits at exactly that position, and each is confirmed with one slice
compare at the single offset where it could start (every offset at which the
gram repeats inside the value is tried). A value present in the text
necessarily has its rarest gram present at ``start + offset`` — no false
negatives; absent values cost only the slice-compare misses.
"""

from __future__ import annotations

from collections import Counter


class ValueMatcher:
    """Exact "which of these values occur in this text", linear per text.

    ``scan`` records ``value -> where`` for every indexed value occurring in
    ``text`` into ``found`` (first ``where`` wins); ``matches`` is the
    one-shot form returning the value set. Values shorter than 2 chars have
    no grams and are checked directly.
    """

    def __init__(self, values) -> None:
        self._short: list[str] = []
        seen = set(values)
        size_of = lambda v: 4 if len(v) >= 5 else 2
        counts: Counter[str] = Counter()
        for v in seen:
            if len(v) < 2:
                self._short.append(v)
                continue
            size = size_of(v)
            counts.update(v[i : i + size] for i in range(len(v) - size + 1))
        # Each value joins the bucket of its rarest gram — the one least
        # likely to occur in text that does not contain the value itself.
        rarest: dict[str, list[str]] = {}
        for v in seen:
            if len(v) < 2:
                continue
            size = size_of(v)
            best_g = best_c = None
            for i in range(len(v) - size + 1):
                g = v[i : i + size]
                c = counts[g]
                if best_c is None or c < best_c:
                    best_g, best_c = g, c
            rarest.setdefault(best_g, []).append(v)
        self._grams = rarest
        # Positions whose char cannot start any indexed gram are skipped
        # without slicing — prose rarely starts a value's gram.
        self._firsts = frozenset(g[0] for g in rarest)

    def scan(self, text: str, where, found: dict) -> None:
        """Record every indexed value occurring in ``text`` into ``found``."""
        for v in self._short:
            if v not in found and v in text:
                found[v] = where
        get = self._grams.get
        firsts = self._firsts
        n = len(text)
        for i in range(n - 1):
            if text[i] not in firsts:
                continue
            gram = text[i : i + 2]
            bucket = get(gram)
            if bucket is not None:
                self._hit(text, i, gram, bucket, where, found)
            if i + 4 <= n:
                gram = text[i : i + 4]
                bucket = get(gram)
                if bucket is not None:
                    self._hit(text, i, gram, bucket, where, found)

    def matches(self, text: str) -> set[str]:
        """Every indexed value occurring in ``text``."""
        found: dict = {}
        self.scan(text, None, found)
        return set(found)

    @staticmethod
    def _hit(text, i, gram, bucket, where, found) -> None:
        # The gram at i can only be evidence of v if v starts at i minus one
        # of the gram's offsets inside v — one slice compare per offset,
        # never a full-text search.
        for v in bucket:
            if v in found:
                continue
            k = v.find(gram)
            while k != -1:
                s = i - k
                if s >= 0 and text[s : s + len(v)] == v:
                    found[v] = where
                    break
                k = v.find(gram, k + 1)
