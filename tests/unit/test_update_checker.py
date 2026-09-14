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
    # 非数字后缀被忽略, 但不能丢数字段 (原实现在 '5-beta' 处 break 丢 patch)
    assert _parse_version("v0.3.5-beta") == (0, 3, 5)


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


def test_parse_version_ignores_prerelease_suffix():
    """beta 后缀不再截断数字段 (原实现在 '1-beta' 处 break 丢段)."""
    assert _parse_version("v0.4.1-beta.10") == (0, 4, 1)
    assert _parse_version("0.4.1-beta.9") == (0, 4, 1)


def test_version_key_prerelease_numeric_order():
    """回归: 预发布序号必须按数值比较, beta.10 > beta.9 (字典序误判为降级)."""
    from src.infra.update_checker import _version_key

    assert _version_key("v0.4.1-beta.10") > _version_key("0.4.1-beta.9")
    assert _version_key("0.4.1-beta.2") > _version_key("v0.4.1-beta.1")
    assert _version_key("0.4.1-beta.9") < _version_key("0.4.1-beta.10")
    assert _version_key("1.0.0-beta.10") > _version_key("1.0.0-beta.2")


def test_version_key_stable_beats_prerelease():
    from src.infra.update_checker import _version_key

    assert _version_key("v0.4.1") > _version_key("0.4.1-beta.10")
    assert _version_key("0.4.0-beta.1") > _version_key("v0.3.5")
    assert _version_key("v0.3.5") < _version_key("0.4.0-beta.1")


def test_version_key_longer_prerelease_is_newer():
    from src.infra.update_checker import _version_key

    assert _version_key("0.4.1-beta.1") > _version_key("0.4.1-beta")
