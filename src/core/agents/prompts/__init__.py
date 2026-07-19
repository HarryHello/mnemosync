"""Agent prompt 模板 (薄封装, 内容来自 PromptStore)."""

from .memory_analysis import (
    build_memory_analysis_prompt,
    load_decay_targets_header,
)
from .prompt_cleaning import (
    build_prompt_cleaning_user_prompt,
    load_prompt_cleaning_system,
)
from .proxy_thinking import build_proxy_thinking_prompt
from .relationship_analysis import build_relationship_analysis_prompt

__all__ = [
    "build_memory_analysis_prompt",
    "load_decay_targets_header",
    "build_relationship_analysis_prompt",
    "build_proxy_thinking_prompt",
    "load_prompt_cleaning_system",
    "build_prompt_cleaning_user_prompt",
]
