"""SKILL.md 加载工具"""
from __future__ import annotations
from pathlib import Path

from .logger import get_logger

logger = get_logger()

_DEFAULT_PROMPT = "请基于提供的审批规则对企业进行审批校验，逐条输出审核结论。"


def load_skill(skill_path: Path | str, default_prompt: str = _DEFAULT_PROMPT) -> str:
    """
    从 SKILL.md 文件加载系统 Prompt，自动去除 YAML frontmatter（--- 块）。

    Args:
        skill_path: SKILL.md 文件路径。
        default_prompt: 文件不存在或读取失败时的兜底 Prompt。

    Returns:
        处理后的 Prompt 字符串。
    """
    try:
        text = Path(skill_path).read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:].lstrip()
        return text
    except Exception as e:
        logger.error("加载 SKILL 文件失败 [%s]: %s", skill_path, e)
        return default_prompt
