"""插件管理器 (v0.3.1).

提供插件的远程浏览、安装、删除和元数据解析功能。
插件源为 GitHub 仓库，通过 API 获取文件列表，通过 raw URL 下载文件。
"""

from __future__ import annotations

import ast
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
    is_builtin: bool = False  # 是否随主程序分发


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

        for item in files:
            if item.get("type") != "file":
                continue
            name = item.get("name", "")
            if not name.endswith(".py") or name.startswith("_"):
                continue
            download_url = item.get("download_url", "")
            if not download_url:
                continue

            # 尝试获取元数据
            metadata = await _fetch_metadata(download_url)
            plugins.append(AvailablePlugin(
                file_name=name,
                download_url=download_url,
                metadata=metadata,
            ))
    except httpx.HTTPStatusError as e:
        logger.warning("获取插件源失败 (%s): %s %s", url, e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("获取插件源失败 (%s): %s", url, e)

    return plugins


async def list_installed() -> list[InstalledPlugin]:
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
                file_name=subdir.name + "/",
                metadata=metadata,
            ))

    return plugins


async def install_plugin(file_name: str, download_url: str) -> Path:
    """下载并安装插件.

    Args:
        file_name: 目标文件名 (如 astrbot.py)
        download_url: 下载 URL

    Returns:
        安装后的文件路径

    Raises:
        ValueError: 文件名不合法
        httpx.HTTPStatusError: 下载失败
    """
    _validate_file_name(file_name)
    _ensure_plugin_dir()

    target = PLUGIN_DIR / file_name
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        target.write_bytes(resp.content)

    logger.info("插件已安装: %s → %s", file_name, target)
    return target


async def install_from_url(url: str) -> tuple[Path, str]:
    """从任意 URL 安装插件.

    自动从 URL 推断文件名。

    Returns:
        (文件路径, 文件名)
    """
    # 从 URL 提取文件名
    file_name = url.rsplit("/", 1)[-1].split("?")[0]
    if not file_name.endswith(".py"):
        raise ValueError(f"URL 不指向 .py 文件: {url}")

    return await install_plugin(file_name, url), file_name


def remove_plugin(file_name: str) -> bool:
    """删除已安装的插件.

    Returns:
        是否成功删除
    """
    _validate_file_name(file_name)
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
    if "/" in file_name or "\\" in file_name:
        raise ValueError(f"文件名不能包含路径分隔符: {file_name}")
    if file_name.startswith("_"):
        raise ValueError(f"文件名不能以下划线开头: {file_name}")
    if not file_name.endswith(".py"):
        raise ValueError(f"文件名必须以 .py 结尾: {file_name}")


def _ensure_plugin_dir() -> None:
    """确保插件目录存在."""
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
