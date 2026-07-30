"""管理 API 路由 - Agent 提示词覆盖管理.

提供提示词的列表、详情、覆盖写入、重置恢复、dry-run 校验、历史备份查询接口.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    PromptDetail,
    PromptHistoryItem,
    PromptHistoryResponse,
    PromptSummary,
    PromptValidateResponse,
    PromptWriteBody,
)
from src.core.prompts import get_prompt_store
from src.core.prompts.registry import PROMPT_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# Helpers
# ============================================================================


def _resolve_prompt_name(name: str) -> None:
    """路径穿越防御第一道: 只放行 registry 白名单.

    未命中直接 404, 不透露文件系统信息.
    """
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail="unknown prompt")


# ============================================================================
# Agent Prompts (覆盖管理)
# ============================================================================


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
