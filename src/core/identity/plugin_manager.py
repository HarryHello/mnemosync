"""插件管理器 (v0.3.1).

提供插件的远程浏览、安装、删除和元数据解析功能。
插件源为 GitHub 仓库，通过 API 获取文件列表，通过 raw URL 下载文件。

beta.24: raw.githubusercontent.com 在国内服务器普遍不可达而
api.github.com 通常可达 (beta.23 实测: 前者超时, 后者 200) —
元数据获取与插件下载在 raw 失败时回退 contents API (base64 全文)。
"""

from __future__ import annotations

import ast
import asyncio
import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

import httpx

from src.core.identity.plugin_registry import PLUGIN_DIR

logger = logging.getLogger(__name__)

# 默认插件源: GitHub 仓库 API
DEFAULT_PLUGIN_SOURCE = "https://api.github.com/repos/HarryHello/mnemosync-plugins/contents/"

# GitHub raw 内容基址
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


def _proxy_url() -> str | None:
    """返回配置的插件代理 (空则 None).

    来源优先级: data/plugin_proxy.toml > config.local.toml [runtime] plugin_proxy
    > 环境变量 MNEMOSYNC_PLUGIN_PROXY.
    """
    from src.core.config import get_plugin_proxy

    val = get_plugin_proxy().strip()
    return val or None


def _apply_proxy(url: str) -> str:
    """把代理前缀应用到 GitHub URL (如 https://gh-proxy.org/ + url).

    未配置代理时原样返回.
    """
    proxy = _proxy_url()
    if not proxy:
        return url
    return proxy.rstrip("/") + "/" + url


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
        async with httpx.AsyncClient(timeout=15, proxy=_proxy_url()) as client:
            resp = await client.get(_apply_proxy(url), headers={"Accept": "application/vnd.github.v3+json"})
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
            download_metadata: PluginMetadata | None = cast(
                PluginMetadata | None, metadata
            ) if not isinstance(metadata, Exception) else None
            plugins.append(AvailablePlugin(
                file_name=name,
                download_url=url,
                metadata=download_metadata,
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
    url = _apply_proxy(download_url)
    _validate_download_url(url)
    _ensure_plugin_dir()

    target = PLUGIN_DIR / file_name
    try:
        async with httpx.AsyncClient(timeout=30, proxy=_proxy_url()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
    except Exception as raw_error:
        # raw 不可达 → contents API 兜底 (beta.24, 国内服务器 raw 常超时)
        api_url = _api_contents_url_from_raw(url)
        if api_url is None:
            raise
        logger.info("raw 下载失败 (%s), 回退 contents API", raw_error)
        content = await _fetch_github_contents(api_url)

    target.write_bytes(content)
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


def _api_contents_url_from_raw(raw_url: str) -> str | None:
    """把 raw.githubusercontent URL 转为 contents API URL (回退用).

    raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}
      → api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}
    非 raw URL 返回 None.
    """
    try:
        stripped = raw_url.split("://", 1)[1]
        parts = stripped.split("/", 4)
        if len(parts) != 5 or parts[0] != "raw.githubusercontent.com":
            return None
        _, owner, repo, ref, path = parts
        if not owner or not repo or not path:
            return None
        return (
            f"https://api.github.com/repos/{owner}/{repo}/contents/"
            f"{quote(path)}?ref={quote(ref)}"
        )
    except Exception:
        return None


async def _fetch_github_contents(api_url: str) -> bytes:
    """经 contents API 拉取单个文件 (base64 解码), 失败抛异常."""
    async with httpx.AsyncClient(timeout=15, proxy=_proxy_url()) as client:
        resp = await client.get(
            _apply_proxy(api_url),
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        resp.raise_for_status()
        return base64.b64decode(resp.json().get("content") or "")


async def _fetch_metadata(url: str) -> PluginMetadata | None:
    """从远程 URL 获取文件头部并解析元数据.

    raw 拉 4KB 头部; 失败或头部不含完整元数据时回退 contents API 全文
    (raw 与 api.github.com 的连通性往往不同, beta.23 服务器实测).
    """
    content: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10, proxy=_proxy_url()) as client:
            # 只下载前 4KB 足够读 class 定义
            resp = await client.get(_apply_proxy(url), headers={"Range": "bytes=0-4096"})
            if resp.status_code in (200, 206):
                content = resp.text
    except Exception as e:
        logger.debug("raw 元数据获取失败 (%s): %s", url, e)

    metadata = _parse_metadata_from_source(content) if content else None
    if metadata is not None:
        return metadata

    api_url = _api_contents_url_from_raw(url)
    if api_url is None:
        return None
    try:
        content = (await _fetch_github_contents(api_url)).decode("utf-8", "replace")
    except Exception as e:
        logger.warning("获取远程插件元数据失败 (raw 与 API 均失败): %s: %s", url, e)
        return None
    return _parse_metadata_from_source(content)


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
    """校验下载 URL 来源可信.

    允许两种来源:
    1. 直接指向 GitHub (raw.githubusercontent.com / github.com, 含子域名)
    2. 配置的代理域名 (如 gh-proxy.org), 但校验其路径部分仍指向
       raw.githubusercontent.com 或 github.com, 防止代理被用来访问任意站点.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"无效 URL: {url}")
    if any(parsed.hostname == host or parsed.hostname.endswith("." + host)
           for host in _ALLOWED_DOWNLOAD_HOSTS):
        return

    # 代理域名: 校验路径仍指向 GitHub
    proxy = _proxy_url()
    if proxy:
        proxy_host = urlparse(proxy).hostname
        if proxy_host and (parsed.hostname == proxy_host
                           or parsed.hostname.endswith("." + proxy_host)):
            path = parsed.path.lstrip("/")
            if path.startswith("https://raw.githubusercontent.com") or path.startswith("https://github.com"):
                return

    raise ValueError(
        f"下载来源不受信任: {parsed.hostname}。"
        f"仅允许: {', '.join(_ALLOWED_DOWNLOAD_HOSTS)} 或配置的代理域名"
    )


def _ensure_plugin_dir() -> None:
    """确保插件目录存在."""
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
