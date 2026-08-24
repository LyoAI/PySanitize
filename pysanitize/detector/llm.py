"""LLM-based text detector: locate sensitive fields, never rewrite the text.

The LLM is treated like an object detector for text: given a chunk of the
document it returns ``{"findings": [{"field_type": ..., "value": ...}]}`` where
``value`` must be a *verbatim* substring of the chunk. The detector re-matches
every value back into the chunk with ``re.finditer(re.escape(value))`` to
recover precise global offsets; values that do not re-match are dropped — the
hallucination hard gate (never mask a span the model only imagined). Chunks are
6000 chars with 300 overlap so a field spanning a cut is seen twice and deduped
downstream by ``(field_type, start, end)``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair

from pysanitize.llm.llm_registry import get_llm
from pysanitize.parser.document import ParsedDocument
from pysanitize.utils import get_logger

from .base import Detection, TextDetector
from .specs import load_field_specs, select_specs

logger = get_logger()

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PROVIDER = "openai"
CHUNK_SIZE = 6000
CHUNK_OVERLAP = 300
MIN_VALUE_LEN = 2
MAX_VALUE_LEN = 64
MAX_COMPLETION_TOKENS = 4000

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


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[tuple[int, str]]:
    """Split ``text`` into overlapping chunks at block boundaries / sentence
    ends. Returns ``(offset, chunk)`` pairs; ``offset`` is the chunk's start in
    ``text``. Splits never land closer than ``chunk_size - overlap`` from the
    chunk start, so the loop always progresses.
    """
    min_progress = max(1, chunk_size - overlap)
    chunks: list[tuple[int, str]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            split = _find_split(text, start, end, min_progress)
            if split is not None:
                end = split
        chunks.append((start, text[start:end]))
        if end >= n:
            break
        start = end - overlap
        if start < 0:
            start = 0
    return chunks


_SENT_END_RE = re.compile(r"[。！？；\n]")


def _find_split(text: str, lo: int, hi: int, min_progress: int) -> int | None:
    """Best break position in ``text[lo:hi]`` (or None if none is good enough).

    Prefers a block boundary (``\\n\\n`` — keeps tables intact), then a sentence
    end. Only positions at least ``min_progress`` chars past ``lo`` qualify so
    the chunker is guaranteed forward progress.
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
    ):
        self.model = model
        self.provider = provider
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
        for offset, chunk in chunk_text(doc.text):
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
