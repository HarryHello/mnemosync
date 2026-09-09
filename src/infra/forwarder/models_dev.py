"""models.dev 实时模型能力源 (v0.4.1).

内置静态表 (model_capabilities.py) 只覆盖发布时有把握的条目, 新模型
(如 deepseek-v4-flash-vision-exp) 上线即滞后. models.dev 社区目录
(https://github.com/anomalyco/models.dev) 的 api.json 聚合了全部服务商的
模型声明: limit.context / limit.output / tool_call / modalities.input.

能力兜底链: 上游 /models 声明 → models.dev (进程内 TTL 缓存) → 静态表.

同一模型 id 常被目录里多个服务商收录且声明不一 (如 glm-5.3-flash 的
output 有 128K 也有 1M), 索引按服务商保留全部来源, 查找时优先取
api base_url 与当前服务商一致的条目 (beta.6 实测教训).

网络失败静默降级并短路重试 (退避窗口), 不影响拉取主流程.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .model_capabilities import KnownCapability

logger = logging.getLogger(__name__)

_MODELS_DEV_URL = "https://models.dev/api.json"
_TTL_SECONDS = 24 * 3600.0  # 能力数据低频变化, 缓存一天
_FAILURE_RETRY_SECONDS = 300.0  # 拉取失败后 5 分钟内不打网络
_FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# 进程内缓存
_cached_index: dict[str, list[_ModelSource]] | None = None
_cached_at: float = 0.0
_failed_at: float = 0.0


@dataclass(frozen=True)
class _ModelSource:
    """同一模型 id 在某服务商下的能力声明."""

    provider: str
    api_base: str | None
    cap: KnownCapability


def reset_models_dev_cache() -> None:
    """清空进程内缓存 (测试用)."""
    global _cached_index, _cached_at, _failed_at
    _cached_index = None
    _cached_at = 0.0
    _failed_at = 0.0


def build_models_dev_index(raw: dict[str, Any]) -> dict[str, list[_ModelSource]]:
    """api.json → model_id → 来源列表 (api.json 遍历顺序, 首个在前)."""
    index: dict[str, list[_ModelSource]] = {}
    for provider_id, provider in raw.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for mid, m in models.items():
            if not isinstance(m, dict) or not mid:
                continue
            raw_limit = m.get("limit")
            limit = raw_limit if isinstance(raw_limit, dict) else {}
            ctx = limit.get("context")
            out = limit.get("output")
            raw_mods = m.get("modalities")
            mods = raw_mods if isinstance(raw_mods, dict) else {}
            input_mods = mods.get("input")
            cap = KnownCapability(
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
            index.setdefault(str(mid), []).append(
                _ModelSource(provider=str(provider_id), api_base=provider.get("api"), cap=cap)
            )
    return index


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        return None


def lookup_models_dev(
    index: dict[str, list[_ModelSource]] | None,
    model_id: str,
    *,
    base_url: str | None = None,
) -> KnownCapability | None:
    """精确 id 命中; 未命中再试去掉命名空间前缀的形态 (foo/bar → bar).

    多服务商收录同一 id 时: 优先取 api base_url 与当前服务商同主机的来源
    (同主机再取首个); 无 base_url 或无同主机来源时取首个.
    """
    if not index:
        return None
    sources = index.get(model_id)
    if sources is None and "/" in model_id:
        sources = index.get(model_id.rsplit("/", 1)[-1])
    if not sources:
        return None
    if base_url:
        host = _url_host(base_url)
        if host:
            for src in sources:
                if _url_host(src.api_base) == host:
                    return src.cap
    return sources[0].cap


async def fetch_models_dev_index() -> dict[str, list[_ModelSource]] | None:
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
