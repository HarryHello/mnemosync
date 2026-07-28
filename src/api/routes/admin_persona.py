"""管理 API 路由 - 人格配置与重置.

提供人格状态重置 (清空记忆/关系/流水/向量库) 和人格配置覆盖编辑接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import (
    get_conversation_store,
    get_memory_store,
    get_reindex_progress,
    get_vector_store,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PersonaConfigRead,
    PersonaConfigRelation,
    PersonaConfigUpdateBody,
    PersonaResetBody,
    PersonaResetResponse,
)
from src.core.config import (
    _delete_persona_override,
    _load_persona_override,
    _reset_settings,
    _write_persona_override,
    get_settings,
)
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Helpers
# ============================================================================


def _build_persona_read() -> PersonaConfigRead:
    """从多层合并后的 settings 构建响应."""
    s = get_settings()
    rel = s.persona.relation
    override_exists = _load_persona_override() is not None
    return PersonaConfigRead(
        name=s.persona.name,
        prompt=s.persona.prompt,
        relation=PersonaConfigRelation(
            persona_addressing=rel.persona_addressing,
            user_addressing=rel.user_addressing,
            context=rel.context,
        ),
        overridden=override_exists,
    )


# ============================================================================
# Persona Reset (v0.2.7 — 回到"新装"语义)
# ============================================================================


@router.post("/persona/reset", response_model=PersonaResetResponse)
async def reset_persona(
    body: PersonaResetBody,
    memory_store: SqliteMemoryStore = Depends(get_memory_store),
    vector_store=Depends(get_vector_store),
    conversation_store: SqliteConversationStore = Depends(get_conversation_store),
    progress=Depends(get_reindex_progress),
):
    """把人格状态回退到"新装"级别: 清空长期记忆 (含 PERMANENT) / 关系 / 短期流水 / 向量库.

    与 prune 的差异:
      * prune 保 PERMANENT, 只按衰减规则清 NORMAL; 此端点**不保 PERMANENT**
      * prune 不动 relationships / conversation_turns; 此端点一并清空
      * 向量库通过 reset_collection() 整个 drop 重建 (下次写入自动重锁 embedding metadata)

    保留:
      * api_keys / auth / llm_service (含 role_bindings) / prompts 覆盖层 / http_logs
      * config.local.toml 里的 [persona] 定义 (是"人格描述", 不是"人格状态")

    与 reindex 互斥: running 时返 409.

    dry_run=True 只统计不执行. 非 dry_run 任一步失败其他步骤已完成的不回滚,
    错误累计到 errors 便于面板呈现部分失败.
    """
    if progress.is_running():
        raise HTTPException(
            status_code=409, detail="reindex 运行中, persona reset 暂不可执行"
        )

    mem_count = await memory_store.count_all()
    rel_count = await memory_store.count_relationships()
    turn_count = await conversation_store.count()

    if body.dry_run:
        return PersonaResetResponse(
            dry_run=True,
            deleted_memories=mem_count,
            deleted_relationships=rel_count,
            deleted_conversation_turns=turn_count,
            vector_reset=False,
        )

    errors: list[str] = []
    vector_reset = False
    deleted_memories = 0
    deleted_relationships = 0
    deleted_turns = 0

    # 1. 先清 Chroma (若失败中止, 尚未破坏 SQLite)
    try:
        vector_store.reset_collection()
        vector_reset = True
    except Exception as e:
        logger.exception("persona reset: vector reset 失败")
        errors.append(f"vector_reset: {e}")

    # 2. memory_entries (含 PERMANENT)
    try:
        deleted_memories = await memory_store.delete_all_memories()
    except Exception as e:
        logger.exception("persona reset: memory_entries 清空失败")
        errors.append(f"memory_entries: {e}")

    # 3. relationships
    try:
        deleted_relationships = await memory_store.delete_all_relationships()
    except Exception as e:
        logger.exception("persona reset: relationships 清空失败")
        errors.append(f"relationships: {e}")

    # 4. conversation_turns (短期记忆流水)
    try:
        deleted_turns = await conversation_store.delete_all()
    except Exception as e:
        logger.exception("persona reset: conversation_turns 清空失败")
        errors.append(f"conversation_turns: {e}")

    logger.info(
        "persona reset 完成: memories=%d relationships=%d turns=%d vector=%s errors=%d",
        deleted_memories, deleted_relationships, deleted_turns, vector_reset, len(errors),
    )
    return PersonaResetResponse(
        dry_run=False,
        deleted_memories=deleted_memories,
        deleted_relationships=deleted_relationships,
        deleted_conversation_turns=deleted_turns,
        vector_reset=vector_reset,
        errors=errors,
    )


# ============================================================================
# Persona Config (v0.2.11 — 覆盖 data/persona_override.toml, 热重载)
# ============================================================================


@router.get("/persona", response_model=PersonaConfigRead)
async def get_persona_config():
    """获取当前人格配置 (多层合并后). 不返回 TOML 原始内容, 返回解析后的字段."""
    return _build_persona_read()


@router.put("/persona", response_model=PersonaConfigRead)
async def update_persona_config(body: PersonaConfigUpdateBody):
    """写入 data/persona_override.toml, 覆盖人格字段.

    三字段都可选传, 但至少传一个. 写入后立即热重载 (调用 _reset_settings).
    """
    if body.name is None and body.prompt is None and body.relation is None:
        raise HTTPException(400, detail="至少需要传入一个字段")

    # 读取当前 override (若存在) 作为增量基础
    current = _load_persona_override() or {}
    if body.name is not None:
        current["name"] = body.name
    if body.prompt is not None:
        current["prompt"] = body.prompt
    if body.relation is not None:
        current_rel = dict(current.get("relation", {}))
        if body.relation.persona_addressing is not None:
            current_rel["persona_addressing"] = body.relation.persona_addressing
        if body.relation.user_addressing is not None:
            current_rel["user_addressing"] = body.relation.user_addressing
        if body.relation.context is not None:
            current_rel["context"] = body.relation.context
        current["relation"] = current_rel

    _write_persona_override(current)
    _reset_settings()
    return _build_persona_read()


@router.delete("/persona", response_model=PersonaConfigRead)
async def reset_persona_config():
    """删除 persona override 文件, 回退到 config.local.toml / 资源默认值."""
    _delete_persona_override()
    _reset_settings()
    return _build_persona_read()
