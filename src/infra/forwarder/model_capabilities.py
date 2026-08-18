"""内置知名模型能力表 (v0.4.1 兜底).

上游 /v1/models 对多数服务商只返回 {id, object, owned_by} (DeepSeek 官方文档确认),
不带 context_length / max_output_tokens 等扩展字段. 此表在**上游未声明**时按
模型名兜底回填, 让"拉取后自动填能力"在主流模型上生效; 用户可在注册后手动改.

规则: 精确 id 优先, 再按最长前缀匹配 (如 deepseek- / qwen-). 仅收录有把握的条目;
不确定的模型不硬编 (回落上游声明/默认).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownCapability:
    context_length: int | None = None
    output_limit: int | None = None
    supports_tools: bool = False
    input_modalities: tuple[str, ...] = ("text",)


#: 精确 id → 能力
_KNOWN_EXACT: dict[str, KnownCapability] = {
    # DeepSeek (官方文档: deepseek-chat/reasoner 上下文 64K, 输出默认 8K, 支持工具)
    "deepseek-chat": KnownCapability(context_length=65536, output_limit=8192, supports_tools=True),
    "deepseek-reasoner": KnownCapability(context_length=65536, output_limit=8192, supports_tools=True),
    "deepseek-coder": KnownCapability(context_length=65536, output_limit=8192, supports_tools=True),
    "deepseek-v4-flash": KnownCapability(context_length=131072, output_limit=8192, supports_tools=True),
    "deepseek-v4-pro": KnownCapability(context_length=131072, output_limit=8192, supports_tools=True),
    # OpenAI GPT-4o 系 (128K 上下文 / 16K 输出)
    "gpt-4o": KnownCapability(context_length=131072, output_limit=16384, supports_tools=True),
    "gpt-4o-mini": KnownCapability(context_length=131072, output_limit=16384, supports_tools=True),
    # Claude 3.5/4 Sonnet (200K 上下文)
    "claude-3-5-sonnet-latest": KnownCapability(context_length=200000, output_limit=8192, supports_tools=True),
    "claude-3-7-sonnet-latest": KnownCapability(context_length=200000, output_limit=8192, supports_tools=True),
}

#: 前缀 → 能力 (取最长匹配)
_KNOWN_PREFIX: dict[str, KnownCapability] = {
    "deepseek-": KnownCapability(context_length=65536, output_limit=8192, supports_tools=True),
    "gpt-4o": KnownCapability(context_length=131072, output_limit=16384, supports_tools=True),
    "claude-3-5-sonnet": KnownCapability(context_length=200000, output_limit=8192, supports_tools=True),
    "claude-3-7-sonnet": KnownCapability(context_length=200000, output_limit=8192, supports_tools=True),
    "qwen": KnownCapability(context_length=131072, output_limit=8192, supports_tools=True),
    "glm-4": KnownCapability(context_length=131072, output_limit=8192, supports_tools=True),
    "moonshot": KnownCapability(context_length=131072, output_limit=8192, supports_tools=True),
}


def known_capability_for(model_id: str) -> KnownCapability | None:
    """按模型名找兜底能力: 精确命中优先, 否则最长前缀匹配. 找不到返回 None."""
    cap = _KNOWN_EXACT.get(model_id)
    if cap is not None:
        return cap
    best: tuple[int, KnownCapability] | None = None
    for prefix, c in _KNOWN_PREFIX.items():
        if model_id.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), c)
    return best[1] if best else None
