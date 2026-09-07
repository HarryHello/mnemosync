"""版本更新检查器测试."""

from __future__ import annotations

from src.infra.update_checker import _parse_version


def test_parse_version_basic():
    assert _parse_version("v0.3.5") == (0, 3, 5)
    assert _parse_version("1.0.0") == (1, 0, 0)
    assert _parse_version("v2.10.3") == (2, 10, 3)


def test_parse_version_no_v_prefix():
    assert _parse_version("0.3.5") == (0, 3, 5)


def test_parse_version_empty():
    assert _parse_version("") == (0,)


def test_parse_version_partial():
    assert _parse_version("v0.3") == (0, 3)
    assert _parse_version("1") == (1,)


def test_parse_version_non_numeric():
    # 非数字部分被忽略
    assert _parse_version("v0.3.5-beta") == (0, 3)


def test_version_comparison():
    assert _parse_version("v0.3.5") > _parse_version("v0.3.4")
    assert _parse_version("v0.4.0") > _parse_version("v0.3.5")
    assert _parse_version("v1.0.0") > _parse_version("v0.9.9")
    assert _parse_version("v0.3.5") == _parse_version("0.3.5")


# ---------------------------------------------------------------------------
# list_releases
# ---------------------------------------------------------------------------


def _make_release(version, body="描述", prerelease=False):
    return {
        "tag_name": version,
        "body": body,
        "published_at": "2026-08-10T00:00:00Z",
        "prerelease": prerelease,
        "html_url": f"https://github.com/HarryHello/mnemosync/releases/tag/{version}",
    }


async def test_list_releases_returns_parsed(monkeypatch):
    """正常返回解析后的版本列表."""
    from src.infra.update_checker import list_releases

    resp = type("R", (), {"status_code": 200, "json": lambda self: [
        _make_release("v0.4.0", "正式版描述"),
        _make_release("v0.4.0-beta.1", "beta 描述", prerelease=True),
    ]})()

    async def fake_get(*a, **k):
        return resp

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    releases = await list_releases()
    assert len(releases) == 2
    assert releases[0]["version"] == "v0.4.0"
    assert releases[0]["description"] == "正式版描述"
    assert releases[0]["is_prerelease"] is False
    assert releases[1]["is_prerelease"] is True


async def test_list_releases_http_error_returns_empty(monkeypatch):
    """网络错误返回空列表."""
    from src.infra.update_checker import list_releases

    async def fake_get(*a, **k):
        raise httpx.ConnectError("timeout")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    assert await list_releases() == []


async def test_list_releases_non_200_returns_empty(monkeypatch):
    """非 200 返回空列表."""
    from src.infra.update_checker import list_releases

    resp = type("R", (), {"status_code": 500, "json": lambda self: []})()

    async def fake_get(*a, **k):
        return resp

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    assert await list_releases() == []
