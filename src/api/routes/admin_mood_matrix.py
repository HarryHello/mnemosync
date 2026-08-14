"""admin/mood-matrix 子路由: 好感度×情绪 6×6 矩阵格子编辑 (面板 grid)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.routes.auth import get_current_user

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


class MoodMatrixCellBody(BaseModel):
    """单格编辑请求体."""

    text: str = Field("", description="格子引导文本 (空 = 重置回默认)")


@router.get("/mood-matrix")
async def get_mood_matrix() -> dict[str, Any]:
    """返回 6×6 矩阵: 档位/心情段元数据 + 36 格 (含覆盖标记)."""
    from src.core.memory.models import RELATIONSHIP_STAGE_LABELS
    from src.core.memory.mood_matrix import (
        CELL_IDS,
        FAVOR_TIERS,
        MOOD_LABELS,
        load_mood_matrix,
        load_override_cells,
    )

    cells = load_mood_matrix()
    overridden = set(load_override_cells().keys())
    return {
        "favor_tiers": [
            {"id": t, "label": RELATIONSHIP_STAGE_LABELS.get(t, t)} for t in FAVOR_TIERS
        ],
        "mood_labels": list(MOOD_LABELS),
        "cells": [
            {"id": cid, "text": cells.get(cid, ""), "overridden": cid in overridden}
            for cid in CELL_IDS
        ],
    }


@router.put("/mood-matrix/{cell_id}")
async def put_mood_matrix_cell(cell_id: str, body: MoodMatrixCellBody) -> dict[str, bool]:
    """保存单个格子到覆盖层 (只改该格, 其余保留)."""
    from src.core.memory.mood_matrix import save_cell

    try:
        save_cell(cell_id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True}


@router.delete("/mood-matrix/{cell_id}")
async def delete_mood_matrix_cell(cell_id: str) -> dict[str, bool]:
    """重置单个格子 (从覆盖层移除, 回默认)."""
    from src.core.memory.mood_matrix import reset_cell

    try:
        ok = reset_cell(cell_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": ok}
