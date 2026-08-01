"""管理 API 路由 - 人格配置与重置.

提供人格状态重置 (清空记忆/关系/流水/向量库) 和人格配置覆盖编辑接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.api.deps import (
    get_conversation_store,
    get_memory_store,
    get_persona_store,
    get_reindex_progress,
    get_relationship_store,
    get_space_policy_store,
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
    _load_default_persona,
    _load_persona_override,
    _reset_settings,
    _write_persona_override,
    get_settings,
)
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.memory_store import SqliteMemoryStore, SqliteRelationshipStore
from src.persistence.space_policy_store import SqliteSpacePolicyStore

# ============================================================================
# Structured Persona API (v0.3.4, SQLite-based)
# ============================================================================


class PersonaIdentityBody(BaseModel):
    """结构化人格身份 (v0.4.0: 移除了 per-user 的 user_addressing/context)."""

    personality: str = ""
    speaking_style: str = ""
    values: list[str] = []
    persona_addressing: str = "人格"


class PersonaOverrideBody(BaseModel):
    """单空间覆盖 (v0.4.0: 移除了 per-user 的 context)."""

    speaking_style: str | None = None
    personality: str | None = None
    scenario: str | None = None


class PersonaDefinitionSaveBody(BaseModel):
    """保存结构化人格 (v0.4.0: 增加 name 字段支持改名)."""

    name: str
    identity: PersonaIdentityBody
    space_overrides: dict[str, PersonaOverrideBody] = {}
    changelog: str = ""


class PersonaDefinitionRead(BaseModel):
    """结构化人格读取."""

    version: str
    name: str
    identity: PersonaIdentityBody
    space_overrides: dict[str, PersonaOverrideBody]
    created_at: str
    updated_at: str


class PersonaVersionItem(BaseModel):
    """人格版本摘要."""

    id: int
    version: str
    name: str
    changelog: str | None
    author: str | None
    created_at: str
    active: bool


class PersonaVersionListResponse(BaseModel):
    items: list[PersonaVersionItem]
    total: int


# ============================================================================
# Persona Profile models (v0.4.0)
# ============================================================================


class PersonaProfileCreateBody(BaseModel):
    name: str
    description: str = ""


class PersonaProfileUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None


class PersonaProfileRead(BaseModel):
    id: str
    name: str
    description: str
    is_active: bool
    created_at: str
    updated_at: str


class PersonaProfileListResponse(BaseModel):
    items: list[PersonaProfileRead]
    total: int


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
    relationship_store: SqliteRelationshipStore = Depends(get_relationship_store),
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
    rel_count = await relationship_store.count_relationships()
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
        deleted_relationships = await relationship_store.delete_all_relationships()
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


# ============================================================================
# Structured Persona (v0.3.4, SQLite-based)
# ============================================================================


def _get_persona_store(request: Request):
    return get_persona_store(request)


@router.get("/persona/definition", response_model=PersonaDefinitionRead)
async def get_persona_definition(request: Request):
    """获取当前激活的结构化人格定义."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    defn = await store.get_active()
    if defn is None:
        raise HTTPException(404, "No active persona definition")
    overrides = {
        sid: PersonaOverrideBody(
            speaking_style=ov.speaking_style,
            personality=ov.personality,
            scenario=ov.scenario,
        )
        for sid, ov in defn.space_overrides.items()
    }
    return PersonaDefinitionRead(
        version=defn.version,
        name=defn.name,
        identity=PersonaIdentityBody(
            personality=defn.identity.personality,
            speaking_style=defn.identity.speaking_style,
            values=list(defn.identity.values),
            persona_addressing=defn.identity.persona_addressing,
        ),
        space_overrides=overrides,
        created_at=defn.created_at.isoformat(),
        updated_at=defn.updated_at.isoformat(),
    )


@router.put("/persona/definition", response_model=PersonaDefinitionRead)
async def save_persona_definition(
    body: PersonaDefinitionSaveBody,
    request: Request,
):
    """保存结构化人格 (创建新版本, 支持改名)."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")

    from src.core.persona.definition import PersonaDefinition, PersonaIdentity, PersonaOverride

    # 版本号递增: 获取当前活跃人格的当前版本号
    current = await store.get_active()
    if current:
        parts = current.version.split(".")
        try:
            new_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        except (IndexError, ValueError):
            new_version = "1.0.1"
    else:
        new_version = "1.0.0"

    defn = PersonaDefinition(
        version=new_version,
        name=body.name,
        identity=PersonaIdentity(
            personality=body.identity.personality,
            speaking_style=body.identity.speaking_style,
            values=body.identity.values,
            persona_addressing=body.identity.persona_addressing,
        ),
        space_overrides={
            sid: PersonaOverride(
                speaking_style=ov.speaking_style,
                personality=ov.personality,
                scenario=ov.scenario,
            )
            for sid, ov in body.space_overrides.items()
        },
    )
    await store.save(defn, changelog=body.changelog)

    overrides = {
        sid: PersonaOverrideBody(
            speaking_style=ov.speaking_style,
            personality=ov.personality,
            scenario=ov.scenario,
        )
        for sid, ov in defn.space_overrides.items()
    }
    return PersonaDefinitionRead(
        version=defn.version,
        name=defn.name,
        identity=PersonaIdentityBody(
            personality=defn.identity.personality,
            speaking_style=defn.identity.speaking_style,
            values=list(defn.identity.values),
            persona_addressing=defn.identity.persona_addressing,
        ),
        space_overrides=overrides,
        created_at=defn.created_at.isoformat(),
        updated_at=defn.updated_at.isoformat(),
    )


@router.get("/persona/versions", response_model=PersonaVersionListResponse)
async def list_persona_versions(
    request: Request,
    limit: int = 50,
    persona_id: str | None = None,
):
    """列出版本历史. 可选传入 persona_id 筛选."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    versions = await store.list_versions(limit=limit, persona_id=persona_id)
    items = [
        PersonaVersionItem(
            id=v["id"],
            version=v["version"],
            name=v["name"],
            changelog=v.get("changelog"),
            author=v.get("author"),
            created_at=v["created_at"],
            active=v["active"],
        )
        for v in versions
    ]
    return PersonaVersionListResponse(items=items, total=len(items))


@router.post("/persona/versions/{version_id}/rollback")
async def rollback_persona_version(
    version_id: int,
    request: Request,
):
    """回滚到指定人格版本."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    ok = await store.rollback(version_id)
    if not ok:
        raise HTTPException(404, detail="Version not found")
    return {"success": True, "version_id": version_id}


# ============================================================================
# Persona Profile Management (v0.4.0)
# ============================================================================


@router.get("/persona/profiles", response_model=PersonaProfileListResponse)
async def list_persona_profiles(request: Request):
    """列出所有人格 profile."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    personas = await store.list_personas()
    items = [
        PersonaProfileRead(
            id=p["id"],
            name=p["name"],
            description=p["description"],
            is_active=p["is_active"],
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )
        for p in personas
    ]
    return PersonaProfileListResponse(items=items, total=len(items))


@router.post("/persona/profiles", response_model=PersonaProfileRead, status_code=201)
async def create_persona_profile(
    body: PersonaProfileCreateBody,
    request: Request,
):
    """创建新人格 profile, 同时写入初始版本定义 (从默认提示词继承)."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    pid = await store.create_persona(name=body.name, description=body.description)

    # 从默认/当前配置创建初始版本定义
    from src.core.persona.definition import PersonaDefinition, PersonaIdentity
    defaults = _load_default_persona()
    identity_data = defaults.get("identity", {})
    if identity_data:
        defn = PersonaDefinition(
            version="1.0.0",
            name=body.name,
            identity=PersonaIdentity(
                personality=identity_data.get("personality", ""),
                speaking_style=identity_data.get("speaking_style", ""),
                values=list(identity_data.get("values", [])),
                persona_addressing=identity_data.get("persona_addressing", "人格"),
            ),
        )
    else:
        # 无结构化 identity 时回退到 legacy prompt
        defn = PersonaDefinition(
            version="1.0.0",
            name=body.name,
            identity=PersonaIdentity(
                personality=defaults.get("prompt", ""),
                persona_addressing=defaults["relation"]["persona_addressing"],
            ),
        )
    await store.save(defn, changelog="初始版本", persona_id=pid)

    profile = await store.get_persona(pid)
    if profile is None:
        raise HTTPException(500, "Failed to create persona profile")
    return PersonaProfileRead(
        id=profile["id"],
        name=profile["name"],
        description=profile["description"],
        is_active=profile["is_active"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
    )


@router.get("/persona/profiles/{persona_id}", response_model=PersonaProfileRead)
async def get_persona_profile(
    persona_id: str,
    request: Request,
):
    """获取指定人格 profile."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    profile = await store.get_persona(persona_id)
    if profile is None:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    return PersonaProfileRead(
        id=profile["id"],
        name=profile["name"],
        description=profile["description"],
        is_active=profile["is_active"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
    )


@router.put("/persona/profiles/{persona_id}", response_model=PersonaProfileRead)
async def update_persona_profile(
    persona_id: str,
    body: PersonaProfileUpdateBody,
    request: Request,
):
    """更新人格 profile (改名/改描述)."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")

    if body.name is None and body.description is None:
        raise HTTPException(400, detail="至少需要传入一个字段")

    ok = await store.update_persona(
        persona_id, name=body.name, description=body.description,
    )
    if not ok:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")

    profile = await store.get_persona(persona_id)
    if profile is None:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    return PersonaProfileRead(
        id=profile["id"],
        name=profile["name"],
        description=profile["description"],
        is_active=profile["is_active"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
    )


@router.post("/persona/profiles/{persona_id}/activate", response_model=PersonaProfileRead)
async def activate_persona_profile(
    persona_id: str,
    request: Request,
):
    """切换到指定人格 profile."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    ok = await store.activate_persona(persona_id)
    if not ok:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    profile = await store.get_persona(persona_id)
    if profile is None:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    return PersonaProfileRead(
        id=profile["id"],
        name=profile["name"],
        description=profile["description"],
        is_active=profile["is_active"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
    )


@router.delete("/persona/profiles/{persona_id}")
async def delete_persona_profile(
    persona_id: str,
    request: Request,
):
    """删除人格 profile 及其所有版本."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    ok = await store.delete_persona(persona_id)
    if not ok:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    return {"success": True, "persona_id": persona_id}


# ============================================================================
# Space Policy (v0.3.4, per-space social behavior)
# ============================================================================


class SpacePolicyBody(BaseModel):
    """空间社交策略."""

    expressor_enabled: bool = True
    expressor_temperature: float = 0.4
    preferred_max_length: int | None = 200
    use_emojis: bool | None = True


class SpacePolicyRead(BaseModel):
    space_id: str
    config: SpacePolicyBody
    updated_at: str


@router.get("/space-policies", response_model=list[SpacePolicyRead])
async def list_space_policies(
    store: SqliteSpacePolicyStore = Depends(get_space_policy_store),
):
    """列出所有空间策略."""
    policies = await store.list_all()
    return [
        SpacePolicyRead(
            space_id=p.space_id,
            config=SpacePolicyBody(
                expressor_enabled=p.expressor_enabled,
                expressor_temperature=p.expressor_temperature,
                preferred_max_length=p.preferred_max_length,
                use_emojis=p.use_emojis,
            ),
            updated_at=p.updated_at.isoformat(),
        )
        for p in policies
    ]


@router.get("/space-policies/{space_id}", response_model=SpacePolicyRead)
async def get_space_policy(
    space_id: str,
    store: SqliteSpacePolicyStore = Depends(get_space_policy_store),
):
    """获取指定空间策略."""
    policy = await store.get(space_id)
    if policy is None:
        raise HTTPException(404, f"No policy for space: {space_id}")
    return SpacePolicyRead(
        space_id=policy.space_id,
        config=SpacePolicyBody(
            expressor_enabled=policy.expressor_enabled,
            expressor_temperature=policy.expressor_temperature,
            preferred_max_length=policy.preferred_max_length,
            use_emojis=policy.use_emojis,
        ),
        updated_at=policy.updated_at.isoformat(),
    )


@router.put("/space-policies/{space_id}", response_model=SpacePolicyRead)
async def upsert_space_policy(
    space_id: str,
    body: SpacePolicyBody,
    store: SqliteSpacePolicyStore = Depends(get_space_policy_store),
):
    """创建或更新空间策略."""
    from src.persistence.space_policy_store import SpacePolicy
    policy = SpacePolicy(
        space_id=space_id,
        expressor_enabled=body.expressor_enabled,
        expressor_temperature=body.expressor_temperature,
        preferred_max_length=body.preferred_max_length,
        use_emojis=body.use_emojis,
    )
    await store.upsert(policy)
    return SpacePolicyRead(
        space_id=policy.space_id,
        config=SpacePolicyBody(
            expressor_enabled=policy.expressor_enabled,
            expressor_temperature=policy.expressor_temperature,
            preferred_max_length=policy.preferred_max_length,
            use_emojis=policy.use_emojis,
        ),
        updated_at=policy.updated_at.isoformat(),
    )


@router.delete("/space-policies/{space_id}")
async def delete_space_policy(
    space_id: str,
    store: SqliteSpacePolicyStore = Depends(get_space_policy_store),
):
    """删除空间策略 (回退到默认行为)."""
    ok = await store.delete(space_id)
    if not ok:
        raise HTTPException(404, f"No policy for space: {space_id}")
    return {"success": True, "space_id": space_id}


# ============================================================================
# Character Card Import (v0.3.4, SillyTavern V1/V2)
# ============================================================================


class CharacterCardPreview(BaseModel):
    """角色卡预览."""

    name: str
    source_format: str
    identity: PersonaIdentityBody
    has_lorebook: bool = False
    has_examples: bool = False


@router.post("/persona/import-card", response_model=CharacterCardPreview)
async def import_character_card(
    request: Request,
    body: dict | None = None,
):
    """从上传的角色卡文件解析并返回预览.

    支持 SillyTavern V1 (PNG) / V2 (PNG tEXt) / JSON 格式。
    返回解析后的人格字段供预览确认, 确认后调用 PUT /persona/definition 保存。
    """
    from src.infra.character_card import CharacterCardError, parse_file

    # 支持两种上传方式: raw bytes body 或 JSON body 含 file_path
    if body and "file_path" in body:
        file_path = body["file_path"]
    else:
        # 直接从 request body 读取上传的文件 bytes
        file_path = None

    if file_path:
        # 服务端已有文件路径 (CLI / 测试场景)
        try:
            card = parse_file(file_path)
        except CharacterCardError as e:
            raise HTTPException(400, detail=str(e)) from e
    else:
        # 从 HTTP body 读取上传的 PNG/JSON 文件
        raw = await request.body()
        if not raw:
            raise HTTPException(400, detail="No file uploaded")
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(400, detail="File too large (max 10MB)")

        from src.infra.character_card import CharacterCard, _sanitize_data, parse_png

        if raw[:4] == b"\x89PNG":
            metadata = parse_png(raw)
            if metadata is None:
                raise HTTPException(400, detail="No character metadata found in PNG")
            data = _sanitize_data(metadata)
            fmt = "v2" if "spec" in data else "v1"
            card = CharacterCard(data, fmt)
        else:
            try:
                import json as _json
                metadata = _json.loads(raw.decode("utf-8"))
                data = _sanitize_data(metadata)
                card = CharacterCard(data, "json")
            except (_json.JSONDecodeError, UnicodeDecodeError) as err:
                raise HTTPException(400, detail="Not a valid PNG or JSON file") from err

    from src.infra.character_card import map_to_persona

    identity_data = map_to_persona(card)
    return CharacterCardPreview(
        name=card.name,
        source_format=card.source_format,
        identity=PersonaIdentityBody(
            personality=identity_data.get("personality", ""),
            speaking_style=identity_data.get("speaking_style", ""),
            values=identity_data.get("values", []),
            persona_addressing=identity_data.get("persona_addressing", "角色"),
        ),
        has_lorebook=card.character_book is not None,
        has_examples=bool(card.mes_example),
    )
