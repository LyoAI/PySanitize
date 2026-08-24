"""SKILL.md loader utility."""
from __future__ import annotations
from pathlib import Path

from .logger import get_logger

logger = get_logger()

_DEFAULT_PROMPT = "Based on the provided approval rules, review the company and output a conclusion for each rule."


def load_skill(skill_path: Path | str, default_prompt: str = _DEFAULT_PROMPT) -> str:
    """
    Load the system prompt from a SKILL.md file, stripping any YAML frontmatter
    (the ``---`` block).

    Args:
        skill_path: path to the SKILL.md file.
        default_prompt: fallback prompt when the file is missing or unreadable.

    Returns:
        The processed prompt string.
    """
    try:
        text = Path(skill_path).read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:].lstrip()
        return text
    except Exception as e:
        logger.error("Failed to load SKILL file [%s]: %s", skill_path, e)
        return default_prompt
