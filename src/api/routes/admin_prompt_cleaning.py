"""管理 API 路由 - 提示词清洗缓存与跳过配置 (v0.4.1).

提供:
- GET    /admin/prompt-cleaning/cache        — 缓存条目列表 (+ 按 frontend 过滤)
- GET    /admin/prompt-cleaning/cache/{hash} — 缓存条目详情
- PUT    /admin/prompt-cleaning/cache/{hash} — 手动编辑 clean_prompt
- DELETE /admin/prompt-cleaning/cache/{hash} — 删除 (下次请求自动重洗)
- POST   /admin/prompt-cleaning/cache/{hash}/re-clean — 当场重洗重建
- DELETE /admin/prompt-cleaning/cache        — 清空全部
- GET    /admin/prompt-cleaning/settings     — 跳过配置列表
- PUT    /admin/prompt-cleaning/settings     — 增改/删除跳过配置
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from src.api.deps import _state
from src.api.routes.auth import get_current_user
from src.persistence.prompt_cache_store import PromptCacheStore, PromptCleanSetting

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


def _store(request: Request) -> PromptCacheStore:
    st = _state(request)
    if st.prompt_cache_store is None:
        raise HTTPException(status_code=503, detail="prompt_cache_store 未初始化")
    return st.prompt_cache_store


# ── Schema ─────────────────────────────────────────────────────


class PromptCacheItem(BaseModel):
    frontend: str
    module_hash: str
    module_title: str
    module_text: str
    clean_prompt: str
    skipped: bool
    created_at: str
    updated_at: str


class PromptCacheListResponse(BaseModel):
    items: list[PromptCacheItem]
    total: int


class PromptCacheEditBody(BaseModel):
    clean_prompt: str


class PromptCleanSettingItem(BaseModel):
    frontend: str
    module_title: str
    skip: bool


class PromptCleanSettingBody(BaseModel):
    frontend: str
    module_title: str
    skip: bool = True


# ── 缓存 ───────────────────────────────────────────────────────


@router.get("/prompt-cleaning/cache", response_model=PromptCacheListResponse)
async def list_clean_cache(
    request: Request,
    frontend: str | None = Query(None, description="按前台 (api_key.note) 过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PromptCacheListResponse:
    store = _store(request)
    items, total = await store.list_cache(limit=page_size, offset=(page - 1) * page_size, frontend=frontend)
    # 列表只返回原文摘要 (详情接口 get_clean_cache 才返回全文), 避免大响应
    return PromptCacheListResponse(
        items=[
            PromptCacheItem(**{**i.__dict__, "module_text": i.module_text[:200]})
            for i in items
        ],
        total=total,
    )


@router.get("/prompt-cleaning/cache/{module_hash}", response_model=PromptCacheItem)
async def get_clean_cache(
    module_hash: str, request: Request,
    frontend: str = Query(..., description="前台 (api_key.note)"),
) -> PromptCacheItem:
    store = _store(request)
    entry = await store.get(frontend, module_hash)
    if entry is None:
        raise HTTPException(status_code=404, detail="缓存条目不存在")
    return PromptCacheItem(**entry.__dict__)


@router.put("/prompt-cleaning/cache/{module_hash}")
async def edit_clean_cache(
    module_hash: str, body: PromptCacheEditBody, request: Request,
    frontend: str = Query(..., description="前台 (api_key.note)"),
) -> dict[str, bool]:
    """手动编辑某模块的清洗结果 (textarea 直接改)."""
    store = _store(request)
    ok = await store.update_clean_prompt(frontend, module_hash, body.clean_prompt)
    if not ok:
        raise HTTPException(status_code=404, detail="缓存条目不存在")
    return {"success": True}


@router.delete("/prompt-cleaning/cache/{module_hash}")
async def delete_clean_cache(
    module_hash: str, request: Request,
    frontend: str = Query(..., description="前台 (api_key.note)"),
) -> dict[str, bool]:
    """删除某条缓存 — 下次请求未命中时自动重新清洗."""
    store = _store(request)
    ok = await store.delete(frontend, module_hash)
    if not ok:
        raise HTTPException(status_code=404, detail="缓存条目不存在")
    return {"success": True}


@router.post("/prompt-cleaning/cache/{module_hash}/re-clean")
async def re_clean_cache(
    module_hash: str, request: Request,
    frontend: str = Query(..., description="前台 (api_key.note)"),
) -> dict[str, Any]:
    """当场重新清洗: 调 ASSIST 模型重洗该模块并写回缓存."""
    store = _store(request)
    entry = await store.get(frontend, module_hash)
    if entry is None:
        raise HTTPException(status_code=404, detail="缓存条目不存在")

    from src.api.deps import _state as _st
    state = _st(request)
    if state.multi_forwarder is None:
        raise HTTPException(status_code=503, detail="multi_forwarder 未初始化")
    semaphore = state.prompt_clean_semaphore

    from src.api.routes.forward.dispatch import _clean_module_with_retry

    if semaphore is None:
        import asyncio
        semaphore = asyncio.Semaphore(20)
    try:
        clean = await _clean_module_with_retry(entry.module_text, state.multi_forwarder, semaphore)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"重新清洗失败: {e}") from e

    await store.update_clean_prompt(frontend, module_hash, clean)
    return {"success": True, "clean_prompt": clean}


@router.delete("/prompt-cleaning/cache")
async def clear_clean_cache(request: Request) -> dict[str, Any]:
    store = _store(request)
    n = await store.clear()
    return {"success": True, "deleted": n}


# ── 跳过配置 ───────────────────────────────────────────────────


@router.get("/prompt-cleaning/settings")
async def list_clean_settings(request: Request) -> dict[str, Any]:
    from typing import cast

    store = _store(request)
    settings = cast(list[PromptCleanSetting], list(await store.list_settings()))
    items = [PromptCleanSettingItem(frontend=s.frontend, module_title=s.module_title, skip=s.skip) for s in settings]
    return {"items": items, "total": len(items)}


@router.put("/prompt-cleaning/settings")
async def set_clean_setting(
    body: PromptCleanSettingBody, request: Request,
) -> dict[str, bool]:
    """设置/取消「跳过不清洗」模块 (按前台; frontend=* 表示全局)."""
    store = _store(request)
    await store.set_skip(body.frontend, body.module_title, body.skip)
    return {"success": True}
