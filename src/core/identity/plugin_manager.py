"""插件管理器 (v0.3.1).

提供插件的远程浏览、安装、删除和元数据解析功能。
插件源为 GitHub 仓库，通过 API 获取文件列表，通过 raw URL 下载文件。
"""

from __future__ import annotations

import ast
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.core.identity.plugin_registry import PLUGIN_DIR

logger = logging.getLogger(__name__)

# 默认插件源: GitHub 仓库 API
DEFAULT_PLUGIN_SOURCE = "https://api.github.com/repos/HarryHello/mnemosync-plugins/contents/"

# GitHub raw 内容基址
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


@dataclass
class PluginMetadata:
    """插件元数据 (从文件 AST 解析, 不执行代码)."""

    name: str
    description: str = ""
    version: str = ""
    author: str = ""
    file_name: str = ""


@dataclass
class AvailablePlugin:
    """远程可用插件."""

    file_name: str
    download_url: str
    metadata: PluginMetadata | None = None


@dataclass
class InstalledPlugin:
    """本地已安装插件."""

    file_name: str
    metadata: PluginMetadata | None = None


async def list_available(source_url: str | None = None) -> list[AvailablePlugin]:
    """从远程源列出可用插件.

    Args:
        source_url: GitHub Contents API URL, 默认使用内置源.
    """
    url = source_url or DEFAULT_PLUGIN_SOURCE
    plugins: list[AvailablePlugin] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"})
            resp.raise_for_status()
            files = resp.json()

        if not isinstance(files, list):
            logger.warning("插件源返回非列表: %s", url)
            return []

        # 收集候选文件
        candidates = []
        for item in files:
            if item.get("type") != "file":
                continue
            name = item.get("name", "")
            if not name.endswith(".py") or name.startswith("_"):
                continue
            download_url = item.get("download_url", "")
            if not download_url:
                continue
            candidates.append((name, download_url))

        # 并行获取元数据
        metadata_list = await asyncio.gather(
            *[_fetch_metadata(url) for _, url in candidates],
            return_exceptions=True,
        )

        for (name, url), metadata in zip(candidates, metadata_list, strict=False):
            if isinstance(metadata, Exception):
                metadata = None
            plugins.append(AvailablePlugin(
                file_name=name,
                download_url=url,
                metadata=metadata,
            ))
    except httpx.HTTPStatusError as e:
        logger.warning("获取插件源失败 (%s): %s %s", url, e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("获取插件源失败 (%s): %s", url, e)

    return plugins


def list_installed() -> list[InstalledPlugin]:
    """列出本地已安装的插件."""
    _ensure_plugin_dir()
    plugins: list[InstalledPlugin] = []

    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        metadata = _parse_metadata_from_file(py_file)
        plugins.append(InstalledPlugin(
            file_name=py_file.name,
            metadata=metadata,
        ))

    # 子目录插件
    for subdir in sorted(PLUGIN_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(("_", "__")):
            continue
        init = subdir / "__init__.py"
        if init.exists():
            metadata = _parse_metadata_from_file(init)
            plugins.append(InstalledPlugin(
                file_name=subdir.name + "/__init__.py",
                metadata=metadata,
            ))

    return plugins


async def install_plugin(file_name: str, download_url: str) -> Path:
    """下载并安装插件.

    Args:
        file_name: 目标文件名 (如 astrbot.py)
        download_url: 下载 URL (仅允许 GitHub raw 地址)

    Returns:
        安装后的文件路径

    Raises:
        ValueError: 文件名不合法或 URL 不可信
        httpx.HTTPStatusError: 下载失败
    """
    _validate_file_name(file_name)
    _validate_download_url(download_url)
    _ensure_plugin_dir()

    target = PLUGIN_DIR / file_name
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        target.write_bytes(resp.content)

    logger.info("插件已安装: %s → %s", file_name, target)
    return target


def remove_plugin(file_name: str) -> bool:
    """删除已安装的插件.

    支持单文件 (astrbot.py) 和子目录 (my-plugin/__init__.py) 两种格式。

    Returns:
        是否成功删除
    """
    _validate_file_name(file_name)

    if "/" in file_name:
        # 子目录插件: 删除整个目录
        dir_name = file_name.split("/")[0]
        target = PLUGIN_DIR / dir_name
        if not target.is_dir():
            return False
        import shutil
        shutil.rmtree(target)
        logger.info("插件目录已删除: %s", target)
        return True

    target = PLUGIN_DIR / file_name
    if not target.exists():
        return False
    target.unlink()
    logger.info("插件已删除: %s", file_name)
    return True


async def _fetch_metadata(url: str) -> PluginMetadata | None:
    """从远程 URL 获取文件头部并解析元数据."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # 只下载前 4KB 足够读 class 定义
            resp = await client.get(url, headers={"Range": "bytes=0-4096"})
            if resp.status_code not in (200, 206):
                return None
            content = resp.text
        return _parse_metadata_from_source(content)
    except Exception as e:
        logger.debug("解析远程插件元数据失败 (%s): %s", url, e)
        return None


def _parse_metadata_from_file(path: Path) -> PluginMetadata | None:
    """从本地文件解析元数据."""
    try:
        source = path.read_text(encoding="utf-8")
        return _parse_metadata_from_source(source)
    except Exception as e:
        logger.debug("解析本地插件元数据失败 (%s): %s", path, e)
        return None


def _parse_metadata_from_source(source: str) -> PluginMetadata | None:
    """从 Python 源码 AST 解析 IdentityPlugin 子类的类属性.

    只解析字符串赋值, 不执行代码。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # 检查是否继承 IdentityPlugin
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name == "IdentityPlugin":
                return _extract_class_attrs(node)

    return None


def _extract_class_attrs(node: ast.ClassDef) -> PluginMetadata:
    """从类定义中提取字符串属性."""
    attrs: dict[str, str] = {}
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name):
                continue
            if isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                attrs[target.id] = item.value.value

    return PluginMetadata(
        name=attrs.get("name", ""),
        description=attrs.get("description", ""),
        version=attrs.get("version", ""),
        author=attrs.get("author", ""),
    )


def _validate_file_name(file_name: str) -> None:
    """校验文件名安全性."""
    # 子目录插件: "my-plugin/__init__.py"
    if "/" in file_name:
        parts = file_name.split("/")
        if len(parts) != 2 or parts[1] != "__init__.py":
            raise ValueError(f"子目录插件格式必须为 name/__init__.py: {file_name}")
        if parts[0].startswith("_"):
            raise ValueError(f"目录名不能以下划线开头: {file_name}")
        return
    if "\\" in file_name:
        raise ValueError(f"文件名不能包含路径分隔符: {file_name}")
    if file_name.startswith("_"):
        raise ValueError(f"文件名不能以下划线开头: {file_name}")
    if not file_name.endswith(".py"):
        raise ValueError(f"文件名必须以 .py 结尾: {file_name}")


# 允许的下载来源 (主机名前缀)
_ALLOWED_DOWNLOAD_HOSTS = [
    "raw.githubusercontent.com",
    "github.com",
]


def _validate_download_url(url: str) -> None:
    """校验下载 URL 来源可信."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"无效 URL: {url}")
    if not any(parsed.hostname == host or parsed.hostname.endswith("." + host)
               for host in _ALLOWED_DOWNLOAD_HOSTS):
        raise ValueError(
            f"下载来源不受信任: {parsed.hostname}。"
            f"仅允许: {', '.join(_ALLOWED_DOWNLOAD_HOSTS)}"
        )


def _ensure_plugin_dir() -> None:
    """确保插件目录存在."""
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
