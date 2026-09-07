"""版本更新检查: 检测 GitHub releases 是否有新版本."""

from __future__ import annotations

import logging
from importlib.metadata import version as _get_version
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_RELEASES_URL = (
    "https://api.github.com/repos/HarryHello/mnemosync/releases/latest"
)
GITHUB_RELEASES_LIST_URL = (
    "https://api.github.com/repos/HarryHello/mnemosync/releases"
)


def _parse_version(v: str) -> tuple[int, ...]:
    """解析版本号为可比较的元组 (忽略 'v' 前缀)."""
    v = v.lstrip("v")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


async def check_for_update() -> dict[str, Any] | None:
    """检查 GitHub 是否有新版本.

    Returns:
        dict with {latest_version, current_version, url} if update available,
        None if up-to-date or check failed.
    """
    try:
        current = _get_version("mnemosync")
    except Exception as e:
        logger.debug("版本查询失败: %s", e)
        return None

    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.get(
                GITHUB_RELEASES_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code != 200:
                logger.debug("GitHub releases API 返回 %d", resp.status_code)
                return None
            data = resp.json()
    except httpx.HTTPError as e:
        logger.debug("检查更新失败: %s", e)
        return None

    latest = data.get("tag_name", "")
    if not latest:
        return None

    if _parse_version(latest) > _parse_version(current):
        return {
            "latest_version": latest,
            "current_version": current,
            "url": data.get("html_url", ""),
        }
    return None


async def list_releases(limit: int = 30) -> list[dict[str, Any]]:
    """列出所有 GitHub releases (含描述).

    Returns:
        list of {version, description, published_at, is_prerelease, url},
        按发布时间倒序. 网络失败时返回空列表.
    """
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.get(
                GITHUB_RELEASES_LIST_URL,
                params={"per_page": limit},
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code != 200:
                logger.debug("GitHub releases list API 返回 %d", resp.status_code)
                return []
            data = resp.json()
    except httpx.HTTPError as e:
        logger.debug("列出版本失败: %s", e)
        return []

    releases: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        releases.append({
            "version": item.get("tag_name", ""),
            "description": item.get("body", "") or "",
            "published_at": item.get("published_at", ""),
            "is_prerelease": bool(item.get("prerelease", False)),
            "url": item.get("html_url", ""),
        })
    return releases
