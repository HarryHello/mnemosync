"""人格版本管理.

列出版本历史与回滚到指定版本.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.routes.admin_persona._helpers import _get_persona_store
from src.api.routes.admin_persona.models import (
    PersonaVersionItem,
    PersonaVersionListResponse,
)
from src.api.routes.auth import get_current_user

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/persona/versions", response_model=PersonaVersionListResponse)
async def list_persona_versions(
    request: Request,
    limit: int = 50,
    persona_id: str | None = None,
) -> PersonaVersionListResponse:
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
) -> dict[str, Any]:
    """回滚到指定人格版本."""
    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    ok = await store.rollback(version_id)
    if not ok:
        raise HTTPException(404, detail="Version not found")
    return {"success": True, "version_id": version_id}
