"""Agent prompt 模板."""

from .memory_analysis import (
    DECAY_TARGETS_HEADER,
    MEMORY_ANALYSIS_PROMPT,
    build_memory_analysis_prompt,
)
from .proxy_thinking import PROXY_THINKING_PROMPT
from .relationship_analysis import (
    RELATIONSHIP_ANALYSIS_PROMPT,
    build_relationship_analysis_prompt,
)

__all__ = [
    "MEMORY_ANALYSIS_PROMPT",
    "DECAY_TARGETS_HEADER",
    "build_memory_analysis_prompt",
    "RELATIONSHIP_ANALYSIS_PROMPT",
    "build_relationship_analysis_prompt",
    "PROXY_THINKING_PROMPT",
]
