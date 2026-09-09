"""models.dev 实时模型能力源 (v0.4.1).

内置静态表 (model_capabilities.py) 只覆盖发布时有把握的条目, 新模型
(如 deepseek-v4-flash-vision-exp) 上线即滞后. models.dev 社区目录
(https://github.com/anomalyco/models.dev) 的 api.json 聚合了全部服务商的
模型声明: limit.context / limit.output / tool_call / modalities.input.

能力兜底链: 上游 /models 声明 → models.dev (进程内 TTL 缓存) → 静态表.
网络失败静默降级并短路重试 (退避窗口), 不影响拉取主流程.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .model_capabilities import KnownCapability

logger = logging.getLogger(__name__)

_MODELS_DEV_URL = "https://models.dev/api.json"
_TTL_SECONDS = 24 * 3600.0  # 能力数据低频变化, 缓存一天
_FAILURE_RETRY_SECONDS = 300.0  # 拉取失败后 5 分钟内不打网络
_FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# 进程内缓存
_cached_index: dict[str, KnownCapability] | None = None
_cached_at: float = 0.0
_failed_at: float = 0.0


def reset_models_dev_cache() -> None:
    """清空进程内缓存 (测试用)."""
    global _cached_index, _cached_at, _failed_at
    _cached_index = None
    _cached_at = 0.0
    _failed_at = 0.0


def build_models_dev_index(raw: dict[str, Any]) -> dict[str, KnownCapability]:
    """api.json → 扁平 model_id → 能力.

    同名 id 跨服务商 (自建网关转发官方模型) 能力声明一致, 保留首个.
    """
    index: dict[str, KnownCapability] = {}
    for provider in raw.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for mid, m in models.items():
            if not isinstance(m, dict) or not mid or mid in index:
                continue
            raw_limit = m.get("limit")
            limit = raw_limit if isinstance(raw_limit, dict) else {}
            ctx = limit.get("context")
            out = limit.get("output")
            raw_mods = m.get("modalities")
            mods = raw_mods if isinstance(raw_mods, dict) else {}
            input_mods = mods.get("input")
            index[str(mid)] = KnownCapability(
                context_length=(
                    int(ctx)
                    if isinstance(ctx, (int, float)) and not isinstance(ctx, bool)
                    else None
                ),
                output_limit=(
                    int(out)
                    if isinstance(out, (int, float)) and not isinstance(out, bool)
                    else None
                ),
                supports_tools=bool(m.get("tool_call")),
                input_modalities=(
                    tuple(str(x) for x in input_mods)
                    if isinstance(input_mods, list) and input_mods
                    else ("text",)
                ),
            )
    return index


def lookup_models_dev(
    index: dict[str, KnownCapability] | None, model_id: str
) -> KnownCapability | None:
    """精确 id 命中; 未命中再试去掉命名空间前缀的形态 (foo/bar → bar)."""
    if not index:
        return None
    cap = index.get(model_id)
    if cap is not None:
        return cap
    if "/" in model_id:
        return index.get(model_id.rsplit("/", 1)[-1])
    return None


async def fetch_models_dev_index() -> dict[str, KnownCapability] | None:
    """拉取 (带缓存) models.dev 索引; 失败返回 None 并进入短路重试窗口."""
    global _cached_index, _cached_at, _failed_at
    now = time.monotonic()
    if _cached_index is not None and now - _cached_at < _TTL_SECONDS:
        return _cached_index
    if _failed_at and now - _failed_at < _FAILURE_RETRY_SECONDS:
        return None
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_MODELS_DEV_URL)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:  # 网络失败静默降级到静态表
        logger.warning("拉取 models.dev 失败 (%s), 回落内置静态表", e)
        _failed_at = now
        return None
    if not isinstance(raw, dict):
        logger.warning("models.dev 返回非对象结构, 回落内置静态表")
        _failed_at = now
        return None
    index = build_models_dev_index(raw)
    if not index:
        logger.warning("models.dev 目录为空, 回落内置静态表")
        _failed_at = now
        return None
    _cached_index = index
    _cached_at = now
    logger.info("models.dev 目录已加载: %d 个模型", len(index))
    return index
