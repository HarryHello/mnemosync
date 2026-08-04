"""身份解析插件管理 (发现 / 安装 / 卸载 / 代理配置)."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.routes.auth import get_current_user
from src.api.schemas.admin import (
    AvailablePluginInfo,
    AvailablePluginListResponse,
    InstalledPluginInfo,
    InstalledPluginListResponse,
    PluginInfo,
    PluginInstallBody,
    PluginListResponse,
    PluginProxyBody,
)
from src.core.identity.plugin_manager import PluginMetadata

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


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
