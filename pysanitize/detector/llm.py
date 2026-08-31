"""LLM-based text detector: locate sensitive fields, never rewrite the text.

The LLM is treated like an object detector for text: given a chunk of the
document it returns ``{"findings": [{"field_type": ..., "value": ...}]}`` where
``value`` must be a *verbatim* substring of the chunk. The detector re-matches
every value back into the chunk with ``re.finditer(re.escape(value))`` to
recover precise global offsets; values that do not re-match are dropped — the
hallucination hard gate (never mask a span the model only imagined). Chunks are
block-aware (``chunk_blocks``): a major title opens a new section, a table
stands alone, minor headings and prose accumulate to ``chunk_size``, and every
chunk is an exact slice of the document text. The title level that counts as
"major" is derived per document from its actual heading structure by
``auto_title_level`` (or pinned via ``LLMDetector.title_level_limit``).

All tunables (model/provider, chunking, value-length filters, completion cap)
live in ``config/pipeline.yaml`` (``text:`` section) and are read through
``pysanitize.config.get_text_config`` — no defaults are hardcoded here.
"""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair

from pysanitize.config import get_text_config
from pysanitize.llm.llm_registry import get_llm
from pysanitize.parser.blocks import Block
from pysanitize.parser.document import ParsedDocument
from pysanitize.prompts import build_field_doc, get_system_prompt, get_user_message
from pysanitize.utils import get_logger

from .base import Detection, TextDetector
from .specs import load_field_specs, select_specs

logger = get_logger()

# Room to search past the hard limit for a clean sentence break when splitting
# an oversized block. Structural to the splitter, not worth a config knob.
_BREAK_MARGIN = 200

# chunk_blocks' title_level_limit sentinel: resolve the config value. In the
# raw-function context a config "auto" has no per-document derivation, so it
# falls back to the structural default (top-level titles only); LLMDetector
# always passes an explicit int | None resolved via auto_title_level.
_TITLE_LEVEL_DEFAULT = "default"
# Structural fallback for that case (smaller = higher; MinerU level 0 = top).
_TOP_TITLE_LEVEL = 1


def _chunking_config() -> dict[str, Any]:
    return get_text_config().get("chunking", {})


def auto_title_level(
    blocks: list[Block], text_len: int, chunk_size: int, min_sections: int | None = None
) -> int | None:
    """Pick a title level that partitions the document into meaty sections.

    Scans levels coarsest-first and returns the first ``L`` where titles at
    ``level <= L`` start at least ``min_sections`` sections (default:
    ``text.min_title_sections`` from the pipeline config) and the average
    section is at least a quarter of the chunk budget. Returns ``None`` when
    no level qualifies — e.g. a doc whose headings are all one shallow level
    (800 tiny "chapters") — so the caller falls back to pure budget
    accumulation. This adapts to per-document structure: a doc that only has
    level-2 headings uses level 2, one with real chapters uses 1.
    """
    if min_sections is None:
        min_sections = int(get_text_config().get("min_title_sections", 3))
    counts: dict[int, int] = {}
    for b in blocks:
        if b.type == "title" and b.char_start >= 0 and b.level is not None:
            counts[b.level] = counts.get(b.level, 0) + 1
    if not counts:
        return None
    cumulative = 0
    for level in sorted(counts):
        cumulative += counts[level]
        if cumulative < min_sections:
            continue
        if text_len / cumulative >= chunk_size / 4:
            return level
    return None

def _field_doc() -> str:
    """The available-``field_type`` bullet list, from the loaded field specs."""
    return build_field_doc(
        {name: spec.label for name, spec in load_field_specs().items()}
    )


def _system_prompt() -> str:
    """System prompt from the ``prompts/`` template + current field specs."""
    return get_system_prompt(_field_doc())


def chunk_blocks(
    blocks: list[Block],
    text: str,
    chunk_size: int | None = None,
    title_level_limit: int | str | None = _TITLE_LEVEL_DEFAULT,
) -> list[tuple[int, str]]:
    """Split ``blocks`` into semantic, non-overlapping chunks of ``text``.

    Returns ``(base_offset, chunk_slice)`` pairs where ``chunk_slice`` is an
    exact slice of ``text``. Structural rules: a ``table`` is always its own
    chunk; a ``title`` opens a new section when its level is within
    ``title_level_limit`` (an unknown level counts as major, ``None`` disables
    title boundaries entirely); prose accumulates to the ``chunk_size`` budget;
    page furniture (meta blocks) is skipped. A single block larger than the
    budget is split internally at paragraph/sentence boundaries.

    Defaults come from ``config/pipeline.yaml`` (``text.chunking``): pass
    ``chunk_size`` / ``title_level_limit`` explicitly to override. A config
    ``title_level_limit`` of ``"auto"`` has no per-document derivation in this
    raw-function context and falls back to top-level-titles-only.
    """
    if not blocks:
        return []
    chunking = _chunking_config()
    if chunk_size is None:
        chunk_size = int(chunking.get("chunk_size", 6000))
    if title_level_limit == _TITLE_LEVEL_DEFAULT:
        limit = chunking.get("title_level_limit", "auto")
        title_level_limit = limit if isinstance(limit, int) else _TOP_TITLE_LEVEL
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
    values), not rewrites. Re-matching back to the text yields exact offsets.

    Model/provider, chunking and value-length filters default to the
    ``text:`` section of ``config/pipeline.yaml``; pass explicit values to
    override.
    """

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        fields: list[str] | None = None,
        chunk_size: int | None = None,
        title_level_limit: int | str = "auto",
    ):
        cfg = get_text_config()
        chunking = cfg.get("chunking", {})
        self.model = model or cfg.get("model")
        self.provider = provider or cfg.get("provider")
        self.chunk_size = (
            int(chunking.get("chunk_size", 6000)) if chunk_size is None else int(chunk_size)
        )
        # "auto" derives a chapter level from the document's heading structure;
        # an int pins it; None disables title boundaries (budget + tables only).
        self.title_level_limit = title_level_limit
        # Finding filters + per-call completion cap (hallucination gate tuning).
        self.min_value_len = int(cfg.get("min_value_len", 2))
        self.max_value_len = int(cfg.get("max_value_len", 64))
        self.max_completion_tokens = int(cfg.get("max_completion_tokens", 4000))
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
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": get_user_message(chunk)},
        ]
        try:
            resp = llm.invoke(
                messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                max_completion_tokens=self.max_completion_tokens,
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
        if not (self.min_value_len <= len(value) <= self.max_value_len):
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
