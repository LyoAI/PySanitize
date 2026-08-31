"""Prompt templates for LLM calls, loaded from the ``prompts/`` package.

The ``.md`` files next to this module are the single source of truth for what
the model is told; Python only injects dynamic values (``{field_doc}`` from
the field specs, ``{chunk}`` document slices) and the optional
``extra_requirements`` free-text block set by interactive frontends (TUI /
WebUI). Edit the ``.md`` files to tune prompts — no code changes needed.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def _read(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# Free-text requirements injected into the system prompt (set by interactive
# frontends). Module-global because LLMDetector builds its prompt without
# knowing about any specific UI; a concurrent WebUI should upgrade this to a
# context-var.
_extra_requirements: str | None = None


def set_extra_requirements(text: str | None) -> None:
    """Set (or clear with ``None``) user requirements appended to the system prompt."""
    global _extra_requirements
    _extra_requirements = (text or "").strip() or None


def get_extra_requirements() -> str | None:
    return _extra_requirements


def _requirements_block() -> str:
    if not _extra_requirements:
        return ""
    return (
        "\n\nAdditional user requirements (treat as extensions of the rules "
        "above; they never override the verbatim rule):\n" + _extra_requirements
    )


def build_field_doc(field_labels: dict[str, str]) -> str:
    """Render the ``field_type`` bullet list shown to the model.

    Args:
        field_labels: mapping of field name → human-readable label, exactly
            the shape ``load_field_specs()`` returns (name → ``FieldSpec``)
            flattened by the caller.
    """
    return "\n".join(f"- {name}: {label}" for name, label in field_labels.items())


def get_system_prompt(field_doc: str) -> str:
    """Build the system prompt: template + field docs + extra requirements."""
    prompt = _read("system.md").replace("{field_doc}", field_doc)
    return prompt + _requirements_block()


def get_user_message(chunk: str) -> str:
    """Build the user message wrapping one document chunk."""
    return _read("user.md").replace("{chunk}", chunk)
