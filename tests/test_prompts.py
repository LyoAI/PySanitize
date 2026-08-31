"""Prompt loader: templates on disk, injection points, extra requirements."""

from __future__ import annotations

import pytest

from pysanitize import prompts


@pytest.fixture(autouse=True)
def _clean_extra_requirements():
    """Keep the module-global extra requirements clean around each test."""
    prompts.set_extra_requirements(None)
    yield
    prompts.set_extra_requirements(None)


def test_system_prompt_contains_field_doc():
    prompt = prompts.get_system_prompt("- phone: phone number")
    assert "- phone: phone number" in prompt
    assert "LOCATE" in prompt
    assert "VERBATIM" in prompt
    assert "findings" in prompt


def test_user_message_contains_chunk():
    msg = prompts.get_user_message("甲" * 100)
    assert "甲" * 100 in msg
    assert "Locate sensitive fields" in msg


def test_templates_have_injection_points():
    assert "{field_doc}" in prompts._read("system.md")
    assert "{chunk}" in prompts._read("user.md")


def test_extra_requirements_appended_to_system_prompt():
    prompts.set_extra_requirements("Also detect contract numbers like HT-2024-XXXX.")
    prompt = prompts.get_system_prompt("- phone: phone number")
    assert "HT-2024-XXXX" in prompt
    assert "Additional user requirements" in prompt


def test_extra_requirements_cleared_restores_base_prompt():
    prompts.set_extra_requirements("temp requirement")
    prompts.set_extra_requirements(None)
    prompt = prompts.get_system_prompt("- phone: phone number")
    assert "Additional user requirements" not in prompt


def test_blank_extra_requirements_is_ignored():
    prompts.set_extra_requirements("   \n  ")
    assert prompts.get_extra_requirements() is None


def test_build_field_doc_formatting():
    doc = prompts.build_field_doc({"phone": "phone number", "email": "email address"})
    assert doc == "- phone: phone number\n- email: email address"
