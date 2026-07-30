"""admin/memories CRUD 子路由: 记忆的查/删/纠正/替代链/批量删除."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.deps import (
    get_memory_store,
    get_multi_forwarder,
    get_resolver,
    get_vector_store,
)
from src.api.routes.auth import get_current_user
from src.persistence.memory_store import SqliteMemoryStore

from .admin_mem_shared import (
    MemoryListResponse,
    MemoryResponse,
    _memory_to_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


class MemoryCorrectBody(BaseModel):
    """记忆纠正请求体."""

    content: str = Field(..., min_length=1, description="纠正后的记忆内容")
    reason: str = Field("", description="纠正原因 (审计用)")


@router.get("/memories/sources")
async def list_memory_sources(
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """列出 memory_entries 中出现过的 source_user, 供下拉框使用."""
    return {"items": await store.list_distinct_source_users()}


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    source_user: str = Query("", description="用户标识, 空=全部用户"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    memory_type: str | None = Query(None, description="normal | permanent"),
    sort_by: str = Query("created_at", description="created_at | last_accessed | importance | decay_rate | access_count | memory_type | source_user"),
    sort_order: str = Query("desc", description="asc | desc"),
    before: str | None = Query(None, description="ISO 时间，仅返回此时间之前创建的记忆"),
    after: str | None = Query(None, description="ISO 时间，仅返回此时间之后创建的记忆"),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """查询记忆列表 (服务器端分页 + 排序)."""
    before_dt = None
    after_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid before timestamp: {before}")
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid after timestamp: {after}")
    offset = (page - 1) * page_size
    items, total = await store.list_page_for_user(
        source_user,
        limit=page_size,
        offset=offset,
        memory_type=memory_type,
        sort_by=sort_by,
        sort_order=sort_order,
        before=before_dt,
        after=after_dt,
    )
    return MemoryListResponse(
        items=[_memory_to_response(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """获取单条记忆详情."""
    memory = await store.get_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(memory)


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """删除记忆."""
    success = await store.delete(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "message": "Memory deleted"}


@router.post("/memories/{memory_id}/correct", response_model=MemoryResponse)
async def correct_memory(
    memory_id: str,
    body: MemoryCorrectBody,
    store: SqliteMemoryStore = Depends(get_memory_store),
    forwarder=Depends(get_multi_forwarder),
    resolver=Depends(get_resolver),
):
    """纠正一条记忆: 创建新记忆替代旧记忆 (软替代)."""
    old = await store.get_by_id(memory_id)
    if not old:
        raise HTTPException(status_code=404, detail="Memory not found")
    if old.superseded_by:
        raise HTTPException(status_code=409, detail="Memory already superseded")

    from src.core.memory.models import MemoryEntry

    new_entry = MemoryEntry.create(
        content=body.content,
        role=old.role,
        source_user=old.source_user,
        memory_type=old.memory_type,
        importance=old.importance,
        decay_rate=old.decay_rate,
    )
    new_entry.visibility = old.visibility
    new_entry.custom_policies = old.custom_policies
    new_entry.emotional_tags = old.emotional_tags
    new_entry.space_id = old.space_id
    new_entry.related_memories = old.related_memories + [old.id]

    try:
        from src.infra.debug_context import use_agent
        with use_agent("memory_correct"):
            vecs = await forwarder.embed(new_entry.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e

    from src.infra.llm_service.models import ModelType
    from src.infra.vector_store import VectorStoreLockError
    try:
        from src.api.deps import get_vector_store
        vector_store = get_vector_store()
        cand = await resolver.first(ModelType.EMBEDDING)
        vector_store.assert_embedding_matches(cand.service_id, cand.model, len(vecs[0]))
    except VectorStoreLockError as e:
        raise HTTPException(status_code=409, detail=f"Vector store lock mismatch: {e}") from e
    except Exception:
        pass

    await store.save(new_entry)
    try:
        vector_store.add(new_entry, vecs[0])
    except Exception:
        pass

    await store.mark_superseded(memory_id, new_entry.id)
    try:
        vector_store.delete(memory_id)
    except Exception:
        pass

    logger.info(
        "记忆纠正: %s -> %s (reason=%s)", memory_id, new_entry.id, body.reason,
    )
    return _memory_to_response(new_entry)


@router.get("/memories/{memory_id}/supersede-chain")
async def get_supersede_chain(
    memory_id: str, store: SqliteMemoryStore = Depends(get_memory_store)
):
    """获取记忆的替代链 (原始 -> 替代版本 -> 更新的替代 -> ...)."""
    chain = await store.get_supersede_chain(memory_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "items": [
            {
                "id": e.id,
                "content": e.content,
                "superseded_by": e.superseded_by,
                "created_at": e.created_at.isoformat(),
            }
            for e in chain
        ],
        "total": len(chain),
    }


@router.delete("/memories")
async def delete_memories_batch(
    source_user: str = Query(..., min_length=1, description="用户标识 (effective_user_id, 必填)"),
    memory_type: str | None = Query(None, description="可选: 仅删除指定类型 (permanent/normal)"),
    before: str | None = Query(None, description="可选: 仅删除此 ISO 时间之前创建的记忆"),
    store: SqliteMemoryStore = Depends(get_memory_store),
):
    """批量删除指定用户的记忆."""
    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid before timestamp: {before}")
    deleted = await store.delete_by_user(
        source_user,
        memory_type=memory_type,
        before=before_dt,
    )
    return {"success": True, "deleted": deleted, "message": f"Deleted {deleted} memories"}
