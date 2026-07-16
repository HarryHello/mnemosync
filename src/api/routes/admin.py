"""管理 API 路由.

提供日志查询、记忆管理、关系状态、Agent 提示词覆盖等管理接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PromptDetail,
    PromptHistoryItem,
    PromptHistoryResponse,
    PromptSummary,
    PromptValidateResponse,
    PromptWriteBody,
)
from src.core.config import get_settings
from src.core.prompts import get_prompt_store
from src.core.prompts.registry import PROMPT_REGISTRY
from src.persistence.auth_store import User
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Schemas
# ============================================================================

class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class HttpLogResponse(BaseModel):
    id: int
    method: str
    path: str
    query_params: Optional[str]
    request_headers: Optional[dict]
    request_body: Optional[dict | str | list]
    response_status: Optional[int]
    response_body: Optional[dict | str | list]
    duration_ms: Optional[float]
    client_ip: Optional[str]
    created_at: str


class HttpLogListResponse(BaseModel):
    items: list[HttpLogResponse]
    total: int
    page: int
    page_size: int


class MemoryResponse(BaseModel):
    id: str
    content: str
    memory_type: str
    importance: float
    decay_rate: float
    access_count: int
    source_user: str
    created_at: str
    last_accessed_at: Optional[str]


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int


class RelationshipResponse(BaseModel):
    persona_id: str
    user_id: str
    intimacy: float
    trust: float
    relationship_type: Optional[str]
    notes: Optional[str]
    updated_at: str


# ============================================================================
# Health Check
# ============================================================================

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查."""
    return HealthResponse(
        status="ok",
        version="0.2.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ============================================================================
# HTTP Logs
# ============================================================================

@router.get("/logs", response_model=HttpLogListResponse)
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    method: Optional[str] = None,
    path: Optional[str] = None,
    status: Optional[int] = None,
):
    """查询 HTTP 日志."""
    settings = get_settings()
    db_path = "data/http_logs.db"

    async with aiosqlite.connect(db_path) as db:
        # 构建查询条件
        conditions = []
        params = []

        if method:
            conditions.append("method = ?")
            params.append(method.upper())

        if path:
            conditions.append("path LIKE ?")
            params.append(f"%{path}%")

        if status:
            conditions.append("response_status = ?")
            params.append(status)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # 获取总数
        cursor = await db.execute(f"SELECT COUNT(*) FROM http_logs WHERE {where_clause}", params)
        total = (await cursor.fetchone())[0]

        # 分页查询
        offset = (page - 1) * page_size
        cursor = await db.execute(
            f"SELECT * FROM http_logs WHERE {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset]
        )
        rows = await cursor.fetchall()

        # 转换为响应格式
        items = []
        for row in rows:
            items.append(HttpLogResponse(
                id=row[0],
                method=row[1],
                path=row[2],
                query_params=row[3],
                request_headers=json.loads(row[4]) if row[4] else None,
                request_body=json.loads(row[5]) if row[5] else None,
                response_status=row[6],
                response_body=json.loads(row[7]) if row[7] else None,
                duration_ms=row[8],
                client_ip=row[9],
                created_at=row[10],
            ))

        return HttpLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get("/logs/{log_id}", response_model=HttpLogResponse)
async def get_log(log_id: int):
    """获取单条日志详情."""
    db_path = "data/http_logs.db"

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT * FROM http_logs WHERE id = ?", (log_id,))
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Log not found")

        return HttpLogResponse(
            id=row[0],
            method=row[1],
            path=row[2],
            query_params=row[3],
            request_headers=json.loads(row[4]) if row[4] else None,
            request_body=json.loads(row[5]) if row[5] else None,
            response_status=row[6],
            response_body=json.loads(row[7]) if row[7] else None,
            duration_ms=row[8],
            client_ip=row[9],
            created_at=row[10],
        )


@router.delete("/logs")
async def clear_logs():
    """清空所有日志."""
    db_path = "data/http_logs.db"

    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM http_logs")
        await db.commit()

    return {"success": True, "message": "All logs cleared"}


# ============================================================================
# Memories
# ============================================================================

@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    source_user: str = "default",
    limit: int = Query(100, ge=1, le=500),
):
    """查询记忆列表."""
    settings = get_settings()
    store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await store.init_db()

    # 获取所有记忆
    all_memories = await store.list_all_for_user(source_user, limit=limit)
    total = len(all_memories)

    items = []
    for m in all_memories:
        items.append(MemoryResponse(
            id=m.id,
            content=m.content,
            memory_type=m.memory_type.value,
            importance=m.importance,
            decay_rate=m.decay_rate,
            access_count=m.access_count,
            source_user=m.source_user,
            created_at=m.created_at.isoformat() if m.created_at else "",
            last_accessed_at=m.last_accessed_at.isoformat() if m.last_accessed_at else None,
        ))

    return MemoryListResponse(items=items, total=total)


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: str):
    """获取单条记忆详情."""
    settings = get_settings()
    store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await store.init_db()

    memory = await store.get_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type.value,
        importance=memory.importance,
        decay_rate=memory.decay_rate,
        access_count=memory.access_count,
        source_user=memory.source_user,
        created_at=memory.created_at.isoformat() if memory.created_at else "",
        last_accessed_at=memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
    )


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """删除记忆."""
    settings = get_settings()
    store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await store.init_db()

    success = await store.delete(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"success": True, "message": "Memory deleted"}


# ============================================================================
# Relationship
# ============================================================================

@router.get("/relationship", response_model=RelationshipResponse)
async def get_relationship(user_id: str = "default"):
    """获取关系状态."""
    settings = get_settings()
    store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await store.init_db()

    rel = await store.get_relationship("default", user_id)
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")

    return RelationshipResponse(
        persona_id=rel.persona_id,
        user_id=rel.user_id,
        intimacy=rel.intimacy,
        trust=rel.trust,
        relationship_type=rel.relationship_type,
        notes=rel.notes,
        updated_at=rel.updated_at.isoformat() if rel.updated_at else "",
    )


# ============================================================================
# Agent Prompts (覆盖管理)
# ============================================================================

def _resolve_prompt_name(name: str) -> None:
    """路径穿越防御第一道: 只放行 registry 白名单.

    未命中直接 404, 不透露文件系统信息.
    """
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail="unknown prompt")


@router.get("/prompts", response_model=list[PromptSummary])
async def list_prompts():
    """列出所有 Agent 提示词 + 覆盖状态."""
    store = get_prompt_store()
    return [
        PromptSummary(
            name=info.name,
            description=info.description,
            placeholders=list(info.placeholders),
            overridden=info.overridden,
            version=info.version,
        )
        for info in store.list()
    ]


@router.get("/prompts/{name}", response_model=PromptDetail)
async def get_prompt(name: str):
    """获取单个提示词详情 (current + default 原文)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    info = store.get_info(name)
    current = store.load_raw(name, default=False)
    default = store.load_raw(name, default=True)
    return PromptDetail(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
        current=current,
        default=default,
    )


@router.put("/prompts/{name}", response_model=PromptSummary)
async def update_prompt(name: str, body: PromptWriteBody):
    """写入覆盖版本. 校验失败返回 400."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    result = store.validate(name, body.content)
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.error,
                "missing_placeholders": result.missing_placeholders,
            },
        )
    store.save(name, body.content)
    info = store.get_info(name)
    return PromptSummary(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
    )


@router.delete("/prompts/{name}", response_model=PromptSummary)
async def reset_prompt(name: str):
    """删除覆盖, 回到默认 (自动备份最后一版)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    store.reset(name)
    info = store.get_info(name)
    return PromptSummary(
        name=info.name,
        description=info.description,
        placeholders=list(info.placeholders),
        overridden=info.overridden,
        version=info.version,
    )


@router.post("/prompts/{name}:validate", response_model=PromptValidateResponse)
async def validate_prompt(name: str, body: PromptWriteBody):
    """dry-run 校验 (不写盘)."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    result = store.validate(name, body.content)
    return PromptValidateResponse(
        ok=result.ok,
        missing_placeholders=result.missing_placeholders,
        error=result.error,
    )


@router.get("/prompts/{name}/history", response_model=PromptHistoryResponse)
async def list_prompt_history(name: str):
    """列出该 name 在 .history/ 下的备份."""
    _resolve_prompt_name(name)
    store = get_prompt_store()
    items = [
        PromptHistoryItem(
            filename=item["filename"],
            mtime=item["mtime"],
            size=item["size"],
        )
        for item in store.list_history(name)
    ]
    return PromptHistoryResponse(items=items)
