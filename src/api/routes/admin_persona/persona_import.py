"""角色卡导入导出 (v0.3.4, SillyTavern V1/V2).

角色卡解析预览与当前人格定义导出.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.routes.admin_persona._helpers import _get_persona_store
from src.api.routes.admin_persona.models import (
    CharacterCardPreview,
    PersonaIdentityBody,
)
from src.api.routes.auth import get_current_user

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/persona/import-card", response_model=CharacterCardPreview)
async def import_character_card(
    request: Request,
    body: dict[str, Any] | None = None,
) -> CharacterCardPreview:
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


@router.get("/persona/export")
async def export_persona(request: Request) -> Response:
    """导出当前激活人格定义 (JSON 下载).

    返回 PersonaDefinition 序列化后的 JSON, 通过 Content-Disposition 触发下载.
    无激活人格时返回 404.
    """
    from fastapi.responses import Response

    store = _get_persona_store(request)
    if store is None:
        raise HTTPException(404, "persona_store not available")
    defn = await store.get_active()
    if defn is None:
        raise HTTPException(404, "No active persona definition")

    import json as _json

    payload = _json.dumps(defn.to_dict(), ensure_ascii=False, indent=2)
    filename = f"{defn.name or 'persona'}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
