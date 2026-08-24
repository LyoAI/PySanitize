"""LLM-based text detector: locate sensitive fields, never rewrite the text.

The LLM is treated like an object detector for text: given a chunk of the
document it returns ``{"findings": [{"field_type": ..., "value": ...}]}`` where
``value`` must be a *verbatim* substring of the chunk. The detector re-matches
every value back into the chunk with ``re.finditer(re.escape(value))`` to
recover precise global offsets; values that do not re-match are dropped — the
hallucination hard gate (never mask a span the model only imagined). Chunks are
block-aware (``chunk_blocks``): a major title opens a new section, a table
stands alone, minor headings and prose accumulate to ``CHUNK_SIZE``, and every
chunk is an exact slice of the document text. The title level that counts as
"major" is derived per document from its actual heading structure by
``auto_title_level`` (or pinned via ``LLMDetector.title_level_limit``).
"""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair

from pysanitize.llm.llm_registry import get_llm
from pysanitize.parser.blocks import Block
from pysanitize.parser.document import ParsedDocument
from pysanitize.utils import get_logger

from .base import Detection, TextDetector
from .specs import load_field_specs, select_specs

logger = get_logger()

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PROVIDER = "openai"
CHUNK_SIZE = 6000
MIN_VALUE_LEN = 2
MAX_VALUE_LEN = 64
MAX_COMPLETION_TOKENS = 4000

# Room to search past the hard limit for a clean sentence break.
_BREAK_MARGIN = 200

# Fixed title level for chunk_blocks' default (smaller = higher, MinerU 0 =
# top): titles within it open a new section chunk, deeper headings accumulate
# like prose. LLMDetector defaults to per-document "auto" instead (see
# auto_title_level).
TITLE_LEVEL_LIMIT = 1

# auto_title_level: a level qualifies as a "chapter" only when it starts at
# least this many sections and the average section is >= 1/4 of the chunk
# budget — smaller sections are too tiny to justify their own LLM call.
MIN_TITLE_SECTIONS = 3


def auto_title_level(
    blocks: list[Block], text_len: int, chunk_size: int
) -> int | None:
    """Pick a title level that partitions the document into meaty sections.

    Scans levels coarsest-first and returns the first ``L`` where titles at
    ``level <= L`` start at least ``MIN_TITLE_SECTIONS`` sections and the
    average section is at least a quarter of the chunk budget. Returns
    ``None`` when no level qualifies — e.g. a doc whose headings are all one
    shallow level (800 tiny "chapters") — so the caller falls back to pure
    budget accumulation. This adapts to per-document structure: a doc that
    only has level-2 headings uses level 2, one with real chapters uses 1.
    """
    counts: dict[int, int] = {}
    for b in blocks:
        if b.type == "title" and b.char_start >= 0 and b.level is not None:
            counts[b.level] = counts.get(b.level, 0) + 1
    if not counts:
        return None
    cumulative = 0
    for level in sorted(counts):
        cumulative += counts[level]
        if cumulative < MIN_TITLE_SECTIONS:
            continue
        if text_len / cumulative >= chunk_size / 4:
            return level
    return None

_FIELD_DOC = "\n".join(
    f"- {name}: {spec.label}" for name, spec in load_field_specs().items()
)

SYSTEM_PROMPT = """You are a document desensitization assistant. Your job is to LOCATE sensitive fields in the given text — never rewrite, summarize, or translate it.

Output a JSON object: {"findings": [{"field_type": "...", "value": "..."}]}

Available field_type values (use one of these only):
""" + _FIELD_DOC + """

Rules:
1. value must be a contiguous substring that appears VERBATIM in the text. Do not add, drop, or alter characters, do not normalize (e.g. remove spaces/punctuation), do not escape.
2. Every finding's value must be findable verbatim in the text; skip anything that is not.
3. Better to miss than to be wrong: skip uncertain values; output {"findings": []} when there is no sensitive information.
4. Output only JSON — no explanations, code fences, or surrounding text."""


def chunk_blocks(
    blocks: list[Block],
    text: str,
    chunk_size: int = CHUNK_SIZE,
    title_level_limit: int | None = TITLE_LEVEL_LIMIT,
) -> list[tuple[int, str]]:
    """Split ``blocks`` into semantic, non-overlapping chunks of ``text``.

    Returns ``(base_offset, chunk_slice)`` pairs where ``chunk_slice`` is an
    exact slice of ``text``. Structural rules: a ``table`` is always its own
    chunk; a ``title`` opens a new section when its level is within
    ``title_level_limit`` (an unknown level counts as major, ``None`` disables
    title boundaries entirely); prose accumulates to the ``chunk_size`` budget;
    page furniture (meta blocks) is skipped. A single block larger than the
    budget is split internally at paragraph/sentence boundaries.
    """
    if not blocks:
        return []
    chunks: list[tuple[int, str]] = []
    run: list[Block] = []

    def flush() -> None:
        if not run:
            return
        base = run[0].char_start
        end = run[-1].char_end
        if end - base > chunk_size:
            chunks.extend(
                (p, piece)
                for p, piece in _split_large(text, base, end, chunk_size)
                if piece.strip()
            )
        elif text[base:end].strip():
            chunks.append((base, text[base:end]))
        run.clear()

    for block in blocks:
        if block.char_start < 0:  # meta block, excluded from doc.text
            continue
        if block.type == "table":
            flush()
            run.append(block)
            flush()
            continue
        if block.type == "title" and (
            title_level_limit is not None
            and (block.level is None or block.level <= title_level_limit)
        ):
            flush()  # a major title opens a new section chunk
        elif run and block.char_end - run[0].char_start > chunk_size:
            flush()  # the run would exceed the budget — close it first
        run.append(block)
    flush()
    return chunks


def _split_large(
    text: str, lo: int, hi: int, chunk_size: int
) -> list[tuple[int, str]]:
    """Split an oversized span (one block > chunk_size) into ~chunk_size pieces.

    Prefers a paragraph boundary (``\\n\\n``), then a sentence end, searching a
    little past the hard limit so the cut lands clean; falls back to a hard cut.
    """
    pieces: list[tuple[int, str]] = []
    pos = lo
    while pos < hi:
        end = min(pos + chunk_size, hi)
        if end < hi:
            split = _find_split(text, pos, min(end + _BREAK_MARGIN, hi), chunk_size)
            if split is not None:
                end = split
        pieces.append((pos, text[pos:end]))
        pos = end
    return pieces


_SENT_END_RE = re.compile(r"[。！？；\n]")


def _find_split(text: str, lo: int, hi: int, min_progress: int) -> int | None:
    """Best break in ``text[lo:hi]`` (or None): paragraph boundary, then a
    sentence end. Only positions at least ``min_progress`` chars past ``lo``
    qualify so the chunker is guaranteed forward progress.
    """
    idx = text.rfind("\n\n", lo, hi)
    if idx >= lo + min_progress:
        return idx
    for m in reversed(tuple(_SENT_END_RE.finditer(text, lo, hi))):
        idx = m.end()
        if idx >= lo + min_progress:
            return idx
    return None


def _parse_findings(content: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON payload; ``json_repair`` as the fallback parser."""
    if not content:
        return []
    content = content.strip()
    try:
        data = json.loads(content)
    except Exception:
        try:
            data = json_repair.loads(content)
        except Exception:
            logger.warning("LLM returned unparseable JSON: %r", content[:200])
            return []
    if isinstance(data, dict):
        findings = data.get("findings", data.get("items", data.get("results", [])))
        return findings if isinstance(findings, list) else []
    if isinstance(data, list):
        return data
    return []


def _locate_value(chunk: str, value: str) -> list[re.Match[str]]:
    """All verbatim occurrences of ``value`` in ``chunk``.

    Falls back to a whitespace-tolerant pattern (the LLM often normalizes
    newlines/indent to single spaces), using the actually-matched text as the
    value.
    """
    matches = list(re.finditer(re.escape(value), chunk))
    if matches:
        return matches
    parts = [re.escape(p) for p in re.split(r"\s+", value) if p]
    if len(parts) > 1:
        return list(re.finditer(r"\s+".join(parts), chunk))
    return []


class LLMDetector(TextDetector):
    """Detects sensitive spans via an LLM that *locates* (returns verbatim
    values), not rewrites. Re-matching back to the text yields exact offsets."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = DEFAULT_PROVIDER,
        fields: list[str] | None = None,
        chunk_size: int = CHUNK_SIZE,
        title_level_limit: int | str = "auto",
    ):
        self.model = model
        self.provider = provider
        self.chunk_size = chunk_size
        # "auto" derives a chapter level from the document's heading structure;
        # an int pins it; None disables title boundaries (budget + tables only).
        self.title_level_limit = title_level_limit
        self.spec_names = set(select_specs(load_field_specs(), fields))
        self._llm = None  # lazy: only touch the API when detect() runs

    def _client(self):
        if self._llm is None:
            self._llm = get_llm(self.model, self.provider)
        return self._llm

    def detect(self, doc: ParsedDocument) -> list[Detection]:
        if not doc.text.strip():
            return []
        try:
            llm = self._client()
        except Exception as e:
            # No API key configured (or a broken config): degrade gracefully so
            # a hybrid run still falls back to rules instead of failing hard.
            logger.warning("LLM detection unavailable (%s), skipping LLM detection", e)
            return []
        out: list[Detection] = []
        limit = self.title_level_limit
        if limit == "auto":
            limit = auto_title_level(doc.blocks, len(doc.text), self.chunk_size)
        for offset, chunk in chunk_blocks(
            doc.blocks, doc.text, self.chunk_size, title_level_limit=limit
        ):
            for finding in self._query(llm, chunk):
                out.extend(self._locate(doc, offset, chunk, finding))
        return out

    # -- LLM round-trip -----------------------------------------------------

    def _query(self, llm, chunk: str) -> list[dict[str, Any]]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Locate sensitive fields in the text below:\n\n" + chunk,
            },
        ]
        try:
            resp = llm.invoke(
                messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
        except Exception as e:  # transport/API error — fail this chunk, keep going
            logger.error("LLM call failed: %s", e)
            return []
        if resp.error:
            logger.error("LLM call error: %s", resp.error)
            return []
        return _parse_findings(resp.content or "")

    # -- value re-match (the hallucination gate) ------------------------------

    def _locate(
        self, doc: ParsedDocument, base: int, chunk: str, finding: dict[str, Any]
    ) -> list[Detection]:
        field_type = str(finding.get("field_type", "")).strip()
        value = str(finding.get("value", "")).strip()
        if field_type not in self.spec_names:
            return []
        if not (MIN_VALUE_LEN <= len(value) <= MAX_VALUE_LEN):
            return []
        out: list[Detection] = []
        for m in _locate_value(chunk, value):
            start = base + m.start()
            end = base + m.end()
            block = doc.block_at(start)
            out.append(
                Detection(
                    field_type=field_type,
                    value=m.group(),
                    start=start,
                    end=end,
                    page=block.page if block else 1,
                    source="llm",
                    confidence=0.95,
                    bbox=block.bbox if block else None,
                )
            )
        return out
