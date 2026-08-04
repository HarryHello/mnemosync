"""管理 API 路由 - 身份管理 (Actors / UserGroups / IdentityStrategies).

提供身份识别策略 CRUD、AI 辅助配置生成、Actor/Group 管理及绑定接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.deps import (
    get_identity_store,
    get_multi_forwarder,
    get_relationship_store,
)
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    ActorListResponse,
    ActorResponse,
    AvailablePluginInfo,
    AvailablePluginListResponse,
    GenerateConfigBody,
    GenerateConfigResponse,
    IdentityStrategyCreateBody,
    IdentityStrategyListResponse,
    IdentityStrategyResponse,
    IdentityStrategyUpdateBody,
    InstalledPluginInfo,
    InstalledPluginListResponse,
    PluginInfo,
    PluginInstallBody,
    PluginListResponse,
    PluginProxyBody,
    UserGroupCreateBody,
    UserGroupListResponse,
    UserGroupResponse,
)
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType
from src.core.identity.plugin_manager import PluginMetadata
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.relationship_store import SqliteRelationshipStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Identity Strategies
# ============================================================================


@router.get("/identity/strategies", response_model=IdentityStrategyListResponse)
async def list_identity_strategies(
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> IdentityStrategyListResponse:
    """列出所有身份识别策略."""
    items, total = await store.list_strategies()
    return IdentityStrategyListResponse(
        items=[
            IdentityStrategyResponse(
                id=s.id, name=s.name, strategy_type=s.strategy_type,
                config=s.config, is_active=s.is_active,
                created_at=s.created_at.isoformat() if s.created_at else "",
                updated_at=s.updated_at.isoformat() if s.updated_at else "",
            )
            for s in items
        ],
        total=total,
    )


@router.post("/identity/strategies", response_model=IdentityStrategyResponse, status_code=201)
async def create_identity_strategy(
    body: IdentityStrategyCreateBody,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> IdentityStrategyResponse:
    """创建身份识别策略."""
    if body.strategy_type not in ("direct", "api_key_bound", "regex", "llm", "plugin"):
        raise HTTPException(400, detail=f"无效策略类型: {body.strategy_type}")
    s = await store.create_strategy(
        name=body.name, strategy_type=body.strategy_type, config=body.config,
    )
    return IdentityStrategyResponse(
        id=s.id, name=s.name, strategy_type=s.strategy_type,
        config=s.config, is_active=s.is_active,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


@router.get("/identity/strategies/{strategy_id}", response_model=IdentityStrategyResponse)
async def get_identity_strategy(
    strategy_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> IdentityStrategyResponse:
    """获取单个策略详情."""
    s = await store.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(404, detail="策略不存在")
    return IdentityStrategyResponse(
        id=s.id, name=s.name, strategy_type=s.strategy_type,
        config=s.config, is_active=s.is_active,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


@router.patch("/identity/strategies/{strategy_id}", response_model=IdentityStrategyResponse)
async def update_identity_strategy(
    strategy_id: str,
    body: IdentityStrategyUpdateBody,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> IdentityStrategyResponse:
    """更新策略 (名称/配置/启用状态)."""
    s = await store.update_strategy(
        strategy_id,
        name=body.name,
        config=body.config,
        is_active=body.is_active,
    )
    if s is None:
        raise HTTPException(404, detail="策略不存在")
    return IdentityStrategyResponse(
        id=s.id, name=s.name, strategy_type=s.strategy_type,
        config=s.config, is_active=s.is_active,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


@router.post("/identity/strategies/generate-config", response_model=GenerateConfigResponse)
async def generate_strategy_config(
    body: GenerateConfigBody,
    forwarder: MultiForwarder = Depends(get_multi_forwarder),
) -> GenerateConfigResponse:
    """AI 辅助生成身份策略配置 (v0.3.1).

    用户用自然语言描述身份信息在消息中的格式, 模型自动生成合法的策略配置 JSON,
    包含正则表达式 (regex 类型) 或 prompt 模板 (llm 类型).
    """
    if body.strategy_type not in ("regex", "llm"):
        raise HTTPException(400, detail=f"不支持为 {body.strategy_type} 类型生成配置, 仅支持 regex / llm")

    sample_block = f"\n\n示例消息:\n```\n{body.sample_message}\n```" if body.sample_message else ""

    if body.strategy_type == "regex":
        system_prompt = (
            "你是一个正则表达式专家, 帮助用户生成 Mnemosync 身份识别策略的配置。\n\n"
            "用户会描述他的消息中身份信息的位置和格式, 你需要输出一个 JSON 对象, 包含以下字段:\n"
            '- frontend: 前台应用名 (如 astrbot, maibot, chatbox, web 等)\n'
            '- actor_pattern: 提取用户唯一标识的正则 (如 QQ号、Discord ID、用户名), 必须包含一个捕获组 ()\n'
            '- name_pattern: 可选, 提取用户显示名称的正则, 包含一个捕获组\n'
            '- space_pattern: 可选, 提取群聊/会话 ID 的正则, 包含一个捕获组\n'
            '- event_id_pattern: 可选, 提取消息事件 ID 的正则, 包含一个捕获组\n'
            '- search_in: 搜索范围, 可选值: system (仅 system 消息), last_user (最后一条 user 消息), all (全部消息)\n\n'
            "正则编写要点:\n"
            "- 用 \\s* 匹配可能的空白字符\n"
            "- 用 [:：] 匹配中英文冒号\n"
            "- 用 \\S+ 匹配非空白标识符, \\d+ 匹配纯数字 ID\n"
            "- 每个 pattern 必须包含恰好一个捕获组 (括号), 用于提取目标值\n"
            "- 如果用户描述中某个字段不存在, 省略该字段\n\n"
            "严格输出 JSON 对象, 不要包含任何解释或 markdown 标记."
        )
    else:
        system_prompt = (
            "你是一个 AI 提示词工程师, 帮助用户生成 Mnemosync 身份识别策略的配置。\n\n"
            "LLM 策略通过调用辅助模型从消息中提取身份信息。你需要输出一个 JSON 对象, 包含以下字段:\n"
            '- frontend: 前台应用名 (如 astrbot, maibot, chatbox, web 等)\n'
            '- prompt_template: 提示词模板, 包含 {content} 占位符, 指示模型从消息中提取身份信息\n'
            "  并返回 JSON: {\"actor_id\":\"...\",\"actor_name\":\"...\",\"space_id\":\"...\",\"event_id\":\"...\"}\n\n"
            "严格输出 JSON 对象, 不要包含任何解释或 markdown 标记."
        )

    user_prompt = (
        f"请根据以下描述生成 {body.strategy_type} 策略配置:\n\n"
        f"{body.description}{sample_block}"
    )

    try:
        resp = await forwarder.chat(
            ModelType.ASSIST,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        data = json.loads(raw)
        # 校验生成的 JSON 是有效对象
        if not isinstance(data, dict):
            raise ValueError("模型返回的不是 JSON 对象")
        return GenerateConfigResponse(config=json.dumps(data, ensure_ascii=False, indent=2))
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning("AI 生成策略配置失败: %s", e)
        raise HTTPException(500, detail=f"模型生成失败: {e}")
    except Exception as e:
        logger.warning("AI 生成策略配置失败 (网络/模型): %s", e)
        raise HTTPException(502, detail=f"模型调用失败: {e}")


@router.delete("/identity/strategies/{strategy_id}")
async def delete_identity_strategy(
    strategy_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> dict[str, Any]:
    """删除策略."""
    removed = await store.delete_strategy(strategy_id)
    if not removed:
        raise HTTPException(404, detail="策略不存在")
    return {"success": True}


# ============================================================================
# Actors
# ============================================================================


@router.get("/identity/actors", response_model=ActorListResponse)
async def list_actors(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorListResponse:
    """列出所有 Actor."""
    items, total = await store.list_actors(limit=limit, offset=offset)
    return ActorListResponse(
        items=[
            ActorResponse(
                id=a.id, external_key=a.external_key, frontend=a.frontend,
                display_name=a.display_name, metadata=a.metadata,
                created_at=a.created_at.isoformat() if a.created_at else "",
                updated_at=a.updated_at.isoformat() if a.updated_at else "",
            )
            for a in items
        ],
        total=total,
    )


@router.get("/identity/actors/{actor_id}", response_model=ActorResponse)
async def get_actor(
    actor_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorResponse:
    """获取单个 Actor."""
    a = await store.get_actor(actor_id)
    if a is None:
        raise HTTPException(404, detail="Actor 不存在")
    return ActorResponse(
        id=a.id, external_key=a.external_key, frontend=a.frontend,
        display_name=a.display_name, metadata=a.metadata,
        created_at=a.created_at.isoformat() if a.created_at else "",
        updated_at=a.updated_at.isoformat() if a.updated_at else "",
    )


# ============================================================================
# UserGroups
# ============================================================================


@router.get("/identity/groups", response_model=UserGroupListResponse)
async def list_user_groups(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupListResponse:
    """列出所有 UserGroup."""
    items, total = await store.list_groups(limit=limit, offset=offset)
    return UserGroupListResponse(
        items=[
            UserGroupResponse(
                id=g.id, name=g.name,
                created_at=g.created_at.isoformat() if g.created_at else "",
                updated_at=g.updated_at.isoformat() if g.updated_at else "",
            )
            for g in items
        ],
        total=total,
    )


@router.post("/identity/groups", response_model=UserGroupResponse, status_code=201)
async def create_user_group(
    body: UserGroupCreateBody,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupResponse:
    """创建 UserGroup."""
    g = await store.create_group(name=body.name)
    return UserGroupResponse(
        id=g.id, name=g.name,
        created_at=g.created_at.isoformat() if g.created_at else "",
        updated_at=g.updated_at.isoformat() if g.updated_at else "",
    )


@router.get("/identity/groups/{group_id}", response_model=UserGroupResponse)
async def get_user_group(
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupResponse:
    """获取单个 UserGroup."""
    g = await store.get_group(group_id)
    if g is None:
        raise HTTPException(404, detail="UserGroup 不存在")
    return UserGroupResponse(
        id=g.id, name=g.name,
        created_at=g.created_at.isoformat() if g.created_at else "",
        updated_at=g.updated_at.isoformat() if g.updated_at else "",
    )


@router.get("/identity/groups/{group_id}/members", response_model=ActorListResponse)
async def list_group_members(
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> ActorListResponse:
    """列出 UserGroup 的所有成员 Actor."""
    members = await store.list_group_members(group_id)
    return ActorListResponse(
        items=[
            ActorResponse(
                id=a.id, external_key=a.external_key, frontend=a.frontend,
                display_name=a.display_name, metadata=a.metadata,
                created_at=a.created_at.isoformat() if a.created_at else "",
                updated_at=a.updated_at.isoformat() if a.updated_at else "",
            )
            for a in members
        ],
        total=len(members),
    )


# ============================================================================
# Actor ↔ Group Bindings
# ============================================================================


@router.post("/identity/actors/{actor_id}/groups/{group_id}")
async def bind_actor_to_group(
    actor_id: str,
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
    relationship_store: SqliteRelationshipStore = Depends(get_relationship_store),
) -> dict[str, Any]:
    """绑定 Actor 到 UserGroup.

    绑定后自动迁移 Actor 的现有关系数据到 UserGroup, 防止同一人出现
    两条独立关系 (绑定前以 actor_id 为 user_id, 绑定后以 group_id 为 user_id).
    """
    ok = await store.bind_actor_to_group(actor_id, group_id)
    if not ok:
        raise HTTPException(409, detail="绑定已存在或 Actor/Group 不存在")
    from src.core.constants import DEFAULT_PERSONA_ID
    migrated = await relationship_store.migrate_relationships_to_group(
        DEFAULT_PERSONA_ID, actor_id, group_id,
    )
    if migrated:
        logger.info(
            "关系迁移: actor=%s → group=%s, 迁移 %d 条",
            actor_id, group_id, migrated,
        )
    return {"success": True, "actor_id": actor_id, "group_id": group_id}


@router.delete("/identity/actors/{actor_id}/groups/{group_id}")
async def unbind_actor_from_group(
    actor_id: str,
    group_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> dict[str, Any]:
    """解绑 Actor 从 UserGroup."""
    ok = await store.unbind_actor_from_group(actor_id, group_id)
    if not ok:
        raise HTTPException(404, detail="绑定不存在")
    return {"success": True}


@router.get("/identity/actors/{actor_id}/groups", response_model=UserGroupListResponse)
async def list_actor_groups(
    actor_id: str,
    store: SqliteIdentityStore = Depends(get_identity_store),
) -> UserGroupListResponse:
    """列出 Actor 所属的所有 UserGroup."""
    groups = await store.list_actor_groups(actor_id)
    return UserGroupListResponse(
        items=[
            UserGroupResponse(
                id=g.id, name=g.name,
                created_at=g.created_at.isoformat() if g.created_at else "",
                updated_at=g.updated_at.isoformat() if g.updated_at else "",
            )
            for g in groups
        ],
        total=len(groups),
    )


@router.get("/identity/plugins", response_model=PluginListResponse)
async def list_identity_plugins(
    request: Request,
) -> PluginListResponse:
    """列出所有已发现的身份解析插件."""
    plugins = getattr(request.app.state, "identity_plugins", None) or {}
    items = [
        PluginInfo(name=name, description=p.description or "")
        for name, p in plugins.items()
    ]
    return PluginListResponse(items=items, total=len(items))


def _metadata_fields(metadata: PluginMetadata | None) -> dict[str, str]:
    """从 PluginMetadata 提取 schema 字段."""
    if metadata is None:
        return {"name": "", "description": "", "version": "", "author": ""}
    return {
        "name": metadata.name or "",
        "description": metadata.description or "",
        "version": metadata.version or "",
        "author": metadata.author or "",
    }


@router.get("/identity/plugins/available", response_model=AvailablePluginListResponse)
async def list_available_plugins() -> AvailablePluginListResponse:
    """从远程源列出可用插件.

    通过 GitHub API 获取插件源仓库的文件列表，解析每个插件的元数据。
    同时标记哪些已安装。
    """
    from src.core.identity.plugin_manager import list_available, list_installed

    available = await list_available()
    installed_names = {p.file_name for p in list_installed()}

    items = [
        AvailablePluginInfo(
            file_name=p.file_name,
            download_url=p.download_url,
            **_metadata_fields(p.metadata),
            installed=p.file_name in installed_names,
        )
        for p in available
    ]
    return AvailablePluginListResponse(items=items, total=len(items))


@router.get("/identity/plugins/installed", response_model=InstalledPluginListResponse)
async def list_installed_plugins() -> InstalledPluginListResponse:
    """列出本地已安装的插件 (含元数据)."""
    from src.core.identity.plugin_manager import list_installed

    items = [
        InstalledPluginInfo(
            file_name=p.file_name,
            **_metadata_fields(p.metadata),
        )
        for p in list_installed()
    ]
    return InstalledPluginListResponse(items=items, total=len(items))


@router.post("/identity/plugins/install", status_code=201)
async def install_plugin(body: PluginInstallBody) -> dict[str, Any]:
    """从远程源安装插件.

    下载 .py 文件到 plugins/ 目录，重启后自动生效。
    """
    from src.core.identity.plugin_manager import install_plugin as do_install

    try:
        path = await do_install(body.file_name, body.download_url)
        return {"success": True, "file_name": body.file_name, "path": str(path)}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.warning("安装插件失败: %s", e)
        raise HTTPException(502, detail=f"下载失败: {e}")


@router.get("/identity/plugins/proxy")
async def get_plugin_proxy_setting() -> dict[str, Any]:
    """读取插件代理配置 (v0.3.1)."""
    from src.core.config import get_plugin_proxy

    return {"plugin_proxy": get_plugin_proxy()}


@router.put("/identity/plugins/proxy")
async def set_plugin_proxy_setting(body: PluginProxyBody) -> dict[str, Any]:
    """持久化插件代理配置 (v0.3.1).

    写入 data/plugin_proxy.toml, 插件检索/下载立即生效.
    """
    from src.core.config import get_plugin_proxy, set_plugin_proxy

    set_plugin_proxy(body.plugin_proxy)
    return {"plugin_proxy": get_plugin_proxy()}


@router.delete("/identity/plugins/{file_name:path}")
async def remove_plugin(file_name: str) -> dict[str, Any]:
    """删除已安装的插件.

    删除后需要重启才能生效 (插件实例仍驻留内存)。
    """
    from src.core.identity.plugin_manager import remove_plugin as do_remove

    try:
        removed = do_remove(file_name)
        if not removed:
            raise HTTPException(404, detail=f"插件文件不存在: {file_name}")
        return {"success": True, "file_name": file_name}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
