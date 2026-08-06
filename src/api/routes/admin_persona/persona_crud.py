"""人格 CRUD + 配置.

包括:
  * Persona Config (v0.2.11 — 覆盖 data/persona_override.toml, 热重载)
  * Structured Persona (v0.3.4, SQLite-based): 定义读写
  * Persona Profile Management (v0.4.0): profile CRUD
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.routes.admin_persona._helpers import _build_persona_read, _get_persona_store
from src.api.routes.admin_persona.models import (
    PersonaDefinitionRead,
    PersonaDefinitionSaveBody,
    PersonaIdentityBody,
    PersonaOverrideBody,
    PersonaProfileCreateBody,
    PersonaProfileListResponse,
    PersonaProfileRead,
    PersonaProfileUpdateBody,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import PersonaConfigRead, PersonaConfigUpdateBody
from src.core.config import (
    _delete_persona_override,
    _load_default_persona,
    _load_persona_override,
    _reset_settings,
    _write_persona_override,
)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Persona Config (v0.2.11 — 覆盖 data/persona_override.toml, 热重载)
# ============================================================================


@router.get("/persona", response_model=PersonaConfigRead)
async def get_persona_config() -> PersonaConfigRead:
    """获取当前人格配置 (多层合并后). 不返回 TOML 原始内容, 返回解析后的字段."""
    return _build_persona_read()


@router.put("/persona", response_model=PersonaConfigRead)
async def update_persona_config(body: PersonaConfigUpdateBody) -> PersonaConfigRead:
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
async def reset_persona_config() -> PersonaConfigRead:
    """删除 persona override 文件, 回退到 config.local.toml / 资源默认值."""
    _delete_persona_override()
    _reset_settings()
    return _build_persona_read()


# ============================================================================
# Structured Persona (v0.3.4, SQLite-based)
# ============================================================================


@router.get("/persona/definition", response_model=PersonaDefinitionRead)
async def get_persona_definition(request: Request) -> PersonaDefinitionRead:
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
) -> PersonaDefinitionRead:
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


# ============================================================================
# Persona Profile Management (v0.4.0)
# ============================================================================


@router.get("/persona/profiles", response_model=PersonaProfileListResponse)
async def list_persona_profiles(request: Request) -> PersonaProfileListResponse:
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
) -> PersonaProfileRead:
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
) -> PersonaProfileRead:
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
) -> PersonaProfileRead:
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
) -> PersonaProfileRead:
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
) -> dict[str, Any]:
    """删除人格 profile 及其所有版本."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    ok = await store.delete_persona(persona_id)
    if not ok:
        raise HTTPException(404, f"Persona profile not found: {persona_id}")
    return {"success": True, "persona_id": persona_id}
