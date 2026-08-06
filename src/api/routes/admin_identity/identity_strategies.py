"""身份识别策略 CRUD + AI 辅助配置生成."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_identity_store, get_multi_forwarder
from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    GenerateConfigBody,
    GenerateConfigResponse,
    IdentityStrategyCreateBody,
    IdentityStrategyListResponse,
    IdentityStrategyResponse,
    IdentityStrategyUpdateBody,
)
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType
from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


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
