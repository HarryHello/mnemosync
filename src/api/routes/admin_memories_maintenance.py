"""admin/memories maintenance 子路由: 向量库重建 (reindex) 与衰减清理 (prune)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import (
    get_memory_store,
    get_multi_forwarder,
    get_reindex_progress,
    get_resolver,
    get_vector_store,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PruneBreakdown as PruneBreakdownSchema,
)
from src.api.schemas.admin import (
    PruneResponse,
    PruneStartBody,
    ReindexStartBody,
    ReindexStatusResponse,
)
from src.core.memory.reindex import Pruner, Reindexer
from src.core.models.resolver import RoleResolver
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/memory/reindex", response_model=ReindexStatusResponse)
async def start_memory_reindex(
    body: ReindexStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    forwarder=Depends(get_multi_forwarder),
    resolver: RoleResolver = Depends(get_resolver),
    progress=Depends(get_reindex_progress),
):
    """启动向量库重建 (异步背景任务). 已在运行返回 409."""
    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 已在运行中")

    reindexer = Reindexer(memory_store, vector_store, forwarder, resolver, progress)

    import asyncio as _asyncio

    async def _run():
        try:
            await reindexer.run(
                prune=body.prune,
                priority_threshold=body.priority_threshold,
            )
        except Exception as e:
            logger.error("reindex 背景任务失败: %s", e)

    _asyncio.create_task(_run())
    return ReindexStatusResponse(**progress.snapshot())


@router.get("/memory/reindex/status", response_model=ReindexStatusResponse)
async def get_memory_reindex_status(
    progress=Depends(get_reindex_progress),
):
    return ReindexStatusResponse(**progress.snapshot())


@router.post("/memory/prune", response_model=PruneResponse)
async def prune_memories(
    body: PruneStartBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    progress=Depends(get_reindex_progress),
):
    """按衰减规则清理记忆. 与 reindex 互斥 (running 时返 409)."""
    if progress.is_running():
        raise HTTPException(status_code=409, detail="reindex 运行中, prune 暂不可执行")

    pruner = Pruner(memory_store, vector_store)
    result = await pruner.run(
        priority_threshold=body.priority_threshold,
        dry_run=body.dry_run,
    )
    return PruneResponse(
        total_before=result.total_before,
        would_delete=result.would_delete,
        deleted=result.deleted,
        breakdown=PruneBreakdownSchema(**result.breakdown.as_dict()),
    )
