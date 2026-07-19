"""提示词注册表 (source of truth).

Registry 是所有 Agent 提示词的白名单. 只有列在此处的 name 才允许被
`PromptStore` 加载/保存, 从而防止路径穿越 (HTTP path 参数、CLI arg 直接
进入文件系统操作前必须经过 registry 校验).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    """单个提示词的元数据.

    Attributes:
        name: 提示词文件名 (无扩展名), 用作路径和引用
        placeholders: 必需占位符 (大写单词, 不含 `__`).
            store.validate() 会检查内容中是否含有全部占位符
        description: 面板/CLI 展示的说明
    """

    name: str
    placeholders: tuple[str, ...]
    description: str


PROMPT_REGISTRY: dict[str, PromptSpec] = {
    "memory_analysis": PromptSpec(
        name="memory_analysis",
        placeholders=(
            "SOURCE_USER",
            "CONVERSATION",
            "DECAY_TARGETS",
            "PERSONA_NAME",
            "PERSONA_ADDRESSING",
            "USER_ADDRESSING",
            "RELATION_CONTEXT",
        ),
        description="记忆分析 Agent",
    ),
    "memory_analysis_decay_header": PromptSpec(
        name="memory_analysis_decay_header",
        placeholders=(),
        description="记忆分析: 衰减目标段头",
    ),
    "relationship_analysis": PromptSpec(
        name="relationship_analysis",
        placeholders=(
            "CURRENT_REL",
            "CONVERSATION",
            "PERSONA_NAME",
            "PERSONA_ADDRESSING",
            "USER_ADDRESSING",
            "RELATION_CONTEXT",
        ),
        description="关系分析 Agent",
    ),
    "prompt_cleaning_system": PromptSpec(
        name="prompt_cleaning_system",
        placeholders=(),
        description="提示词清洗 Agent: system prompt",
    ),
    "prompt_cleaning_user": PromptSpec(
        name="prompt_cleaning_user",
        placeholders=("SYSTEM_MESSAGE",),
        description="提示词清洗 Agent: user prompt",
    ),
    "proxy_thinking": PromptSpec(
        name="proxy_thinking",
        placeholders=("USER_NAME", "RELATIONSHIP", "MEMORIES", "USER_MESSAGE"),
        description="代理推理 Agent",
    ),
    "sentence_classifier": PromptSpec(
        name="sentence_classifier",
        placeholders=("TEXT",),
        description="工具: 单句分类 (提示词清洗内部调用)",
    ),
    "main_dialogue_frame": PromptSpec(
        name="main_dialogue_frame",
        placeholders=(
            "PERSONA_NAME",
            "PERSONA_PROMPT",
            "USER_NAME",
            "RELATIONSHIP",
            "PERMANENT_MEMORIES",
            "RETRIEVED_MEMORIES",
            "PROXY_THINKING_SECTION",
        ),
        description="主对话框架 (行为准则 / section 标题 / 记忆容器)",
    ),
}


__all__ = ["PromptSpec", "PROMPT_REGISTRY"]
