"""身份/模型解析: API Key 验证, 模型候选, 身份上下文."""
import json
import logging
from typing import Any

from fastapi import Request

from src.core.identity import IdentityContext, IdentityResolver
from src.core.models.resolver import NoCandidateForRoleError
from src.infra.llm_service.models import ModelType
from src.persistence.api_key_store import ApiKey

from ._accessors import _get_api_key_store, _get_identity_store, _get_multi_forwarder, _get_plugins

# VIRTUAL_MODEL_ANY 来自 core.constants, 避免与 __init__.py 产生循环依赖,
# 在函数内部延迟导入。

logger = logging.getLogger(__name__)


async def _resolve_main_candidate(
    http_request: Request,
    *,
    require_tools: bool = False,
    streaming: bool = False,
) -> Any:
    """解析 MAIN 角色首选候选.

    当请求携带 tools 时 (require_tools=True), 优先选择支持工具的候选;
    不支持工具的候选跳过. 返回 ResolvedCandidate 或 None (无候选).
    """
    from src.api.deps import _state
    resolver = _state(http_request).resolver
    if resolver is None:
        return None
    try:
        if require_tools:
            return await resolver.first_for_tools(ModelType.MAIN, streaming=streaming)
        return await resolver.first(ModelType.MAIN)
    except NoCandidateForRoleError:
        return None


async def _resolve_main_model(
    http_request: Request,
    *,
    require_tools: bool = False,
    streaming: bool = False,
) -> str:
    """解析 MAIN 角色首选候选的模型名 (供 usage/response.model/推理判定使用)."""
    from src.core.constants import VIRTUAL_MODEL_ANY
    cand = await _resolve_main_candidate(
        http_request, require_tools=require_tools, streaming=streaming,
    )
    return cand.model if cand else VIRTUAL_MODEL_ANY


async def _resolve_source_frontend(request: Request, api_key_id: str | None) -> str | None:
    """从 API Key note 派生 source_frontend 元数据.

    v0.2.6: 用于回写 conversation_turns.source_frontend, 仅调试/追溯用,
    不作为查询条件. 服务器 side 派生, 不依赖客户端。
    """
    if api_key_id is None:
        return None
    from src.api.deps import _state_or_none
    state = _state_or_none(request)
    store = state.api_key_store if state else None
    if store is None:
        store = _get_api_key_store()
    try:
        ak: ApiKey | None = await store.get_by_id(api_key_id)
    except Exception as e:
        logger.debug("API Key 查询失败: %s", e)
        return None
    return ak.note if ak else None


async def _resolve_identity_context(
    http_request: Request,
    api_key: ApiKey | None,
    request_user: str | None,
    messages: list[Any],
) -> IdentityContext | None:
    """解析请求中的身份信息。

    1. 获取 API Key 绑定的策略
    2. 解析身份
    3. 返回 IdentityContext（None = 非归属模式）
    """
    identity_store = _get_identity_store(http_request)
    if identity_store is None:
        return None

    strategy_id = api_key.strategy_id if api_key else None
    if not strategy_id:
        return None

    strategy = await identity_store.get_strategy(strategy_id)
    if strategy is None or not strategy.is_active:
        return None

    config = json.loads(strategy.config) if strategy.config else {}
    forwarder = _get_multi_forwarder(http_request)
    plugins = _get_plugins(http_request)
    resolver = IdentityResolver(identity_store, forwarder, plugins)
    return await resolver.resolve(
        request_user=request_user,
        messages=messages,
        strategy_type=strategy.strategy_type,
        strategy_config=config,
        strategy_name=strategy.name,
    )


def _model_speaker_label(
    identity: IdentityContext | None,
    request_user: str | None,
) -> str:
    """生成模型可读身份；内部 actor/group UUID 只用于存储，绝不进入提示词."""
    if identity is None:
        return (request_user or "未知参与者").strip() or "未知参与者"
    name = (identity.display_name or "").strip()
    external_key = (identity.external_key or "").strip()
    frontend = (identity.frontend or "unknown").strip()
    if name and external_key:
        return f"{name} | {frontend} {external_key}"
    return name or external_key or "未知参与者"


async def _verify_api_key(request: Request) -> ApiKey | None:
    """从 Authorization header 验证 API Key, 返回 ApiKey 对象 (含 strategy_id) 或 None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[7:]
    store = _get_api_key_store()
    api_key = await store.get_by_raw_key(raw_key)
    if api_key is None:
        return None
    await store.update_last_used(api_key.id)
    return api_key
