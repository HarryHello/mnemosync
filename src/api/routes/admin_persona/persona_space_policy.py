"""空间社交策略 (v0.3.4, per-space social behavior).

虽与人格相关, 但路径 /space-policies 独立成域, 单独存放.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_space_policy_store
from src.api.routes.admin_persona.models import SpacePolicyBody, SpacePolicyRead
from src.api.routes.auth import get_current_user
from src.persistence.space_policy_store import SqliteSpacePolicyStore

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/space-policies", response_model=list[SpacePolicyRead])
async def list_space_policies(
    store: SqliteSpacePolicyStore = Depends(get_space_policy_store),
) -> list[SpacePolicyRead]:
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
) -> SpacePolicyRead:
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
) -> SpacePolicyRead:
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
) -> dict[str, Any]:
    """删除空间策略 (回退到默认行为)."""
    ok = await store.delete(space_id)
    if not ok:
        raise HTTPException(404, f"No policy for space: {space_id}")
    return {"success": True, "space_id": space_id}
