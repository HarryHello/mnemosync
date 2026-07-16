"""提示词两层配置系统.

- registry.py: 已知提示词白名单 (source of truth)
- store.py: PromptStore, 提供 CRUD + 备份 + 校验
"""

from .registry import PROMPT_REGISTRY, PromptSpec
from .store import (
    BACKUP_KEEP,
    PromptInfo,
    PromptStore,
    ValidationResult,
    _reset_prompt_store,
    get_prompt_store,
)

__all__ = [
    "PROMPT_REGISTRY",
    "PromptSpec",
    "PromptStore",
    "PromptInfo",
    "ValidationResult",
    "BACKUP_KEEP",
    "get_prompt_store",
    "_reset_prompt_store",
]
