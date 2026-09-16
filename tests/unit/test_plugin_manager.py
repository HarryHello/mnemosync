"""Plugin manager 单元测试.

覆盖:
- _validate_file_name: 文件名安全校验
- _validate_download_url: 下载来源可信校验
- _parse_metadata_from_source: AST 解析 IdentityPlugin 元数据
- _extract_class_attrs: 类属性提取
- PluginMetadata / AvailablePlugin / InstalledPlugin: 数据类
- list_installed: 本地插件列表 (使用 tmp_path mock PLUGIN_DIR)
- remove_plugin: 删除已安装插件
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from src.core.identity.plugin_manager import (
    AvailablePlugin,
    InstalledPlugin,
    PluginMetadata,
    _api_contents_url_from_raw,
    _apply_proxy,
    _extract_class_attrs,
    _fetch_metadata,
    _parse_metadata_from_source,
    _proxy_url,
    _validate_download_url,
    _validate_file_name,
    install_plugin,
    remove_plugin,
)

# ---------------------------------------------------------------------------
# PluginMetadata / AvailablePlugin / InstalledPlugin data classes
# ---------------------------------------------------------------------------

class TestPluginDataClasses:
    def test_plugin_metadata_defaults(self) -> None:
        m = PluginMetadata(name="test")
        assert m.name == "test"
        assert m.description == ""
        assert m.version == ""
        assert m.author == ""
        assert m.file_name == ""

    def test_available_plugin(self) -> None:
        ap = AvailablePlugin(file_name="a.py", download_url="http://x")
        assert ap.metadata is None

    def test_installed_plugin(self) -> None:
        ip = InstalledPlugin(file_name="b.py")
        assert ip.metadata is None


# ---------------------------------------------------------------------------
# _validate_file_name
# ---------------------------------------------------------------------------

class TestValidateFileName:
    def test_valid_simple(self) -> None:
        _validate_file_name("astrbot.py")  # no error

    def test_valid_subdir_plugin(self) -> None:
        _validate_file_name("my-plugin/__init__.py")

    def test_rejects_leading_underscore(self) -> None:
        with pytest.raises(ValueError, match="下划线"):
            _validate_file_name("_private.py")

    def test_rejects_non_py(self) -> None:
        with pytest.raises(ValueError, match="必须以 .py 结尾"):
            _validate_file_name("script.js")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="路径分隔符"):
            _validate_file_name("dir\\file.py")

    def test_rejects_bad_subdir_format(self) -> None:
        with pytest.raises(ValueError, match="必须为 name/__init__.py"):
            _validate_file_name("my-plugin/main.py")

    def test_rejects_deep_subdir(self) -> None:
        with pytest.raises(ValueError, match="必须为 name/__init__.py"):
            _validate_file_name("a/b/__init__.py")

    def test_rejects_underscore_dir(self) -> None:
        with pytest.raises(ValueError, match="下划线"):
            _validate_file_name("_internal/__init__.py")


# ---------------------------------------------------------------------------
# _validate_download_url
# ---------------------------------------------------------------------------

class TestValidateDownloadUrl:
    def test_valid_github_raw(self) -> None:
        _validate_download_url(
            "https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/astrbot.py"
        )

    def test_valid_github_com(self) -> None:
        _validate_download_url("https://github.com/HarryHello/mnemosync-plugins/blob/main/a.py")

    def test_rejects_untrusted_host(self) -> None:
        with pytest.raises(ValueError, match="不受信任"):
            _validate_download_url("https://evil.com/malware.py")

    def test_rejects_no_hostname(self) -> None:
        with pytest.raises(ValueError, match="无效"):
            _validate_download_url("not-a-url")

    def test_subdomain_of_github_raw_allowed(self) -> None:
        """raw.githubusercontent.com is itself a subdomain; validation allows its subdomains."""
        _validate_download_url("https://evil.raw.githubusercontent.com/x.py")

    def test_accepts_raw_subdomain(self) -> None:
        _validate_download_url("https://raw.githubusercontent.com/org/repo/main/f.py")

    def test_accepts_proxy_domain_with_github_path(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value="https://gh-proxy.org/"):
            _validate_download_url(
                "https://gh-proxy.org/https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/a.py"
            )

    def test_accepts_proxy_domain_with_github_com_path(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value="https://gh-proxy.org/"):
            _validate_download_url(
                "https://gh-proxy.org/https://github.com/HarryHello/mnemosync-plugins/blob/main/a.py"
            )

    def test_rejects_proxy_domain_with_non_github_path(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value="https://gh-proxy.org/"):
            with pytest.raises(ValueError, match="不受信任"):
                _validate_download_url("https://gh-proxy.org/https://evil.com/malware.py")

    def test_rejects_other_proxy_domain(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value="https://gh-proxy.org/"):
            with pytest.raises(ValueError, match="不受信任"):
                _validate_download_url(
                    "https://other-proxy.net/https://raw.githubusercontent.com/org/repo/main/a.py"
                )


# ---------------------------------------------------------------------------
# _proxy_url / _apply_proxy
# ---------------------------------------------------------------------------

class TestProxyApply:
    def test_apply_proxy_no_proxy(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value=None):
            assert _apply_proxy(
                "https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/a.py"
            ) == "https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/a.py"

    def test_apply_proxy_prefix(self) -> None:
        with patch("src.core.identity.plugin_manager._proxy_url", return_value="https://gh-proxy.org/"):
            assert _apply_proxy("https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/a.py") == (
                "https://gh-proxy.org/https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/a.py"
            )

    def test_proxy_url_respects_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MNEMOSYNC_PLUGIN_PROXY", "https://proxy.example/")
        assert _proxy_url() == "https://proxy.example/"

    def test_proxy_url_empty_env_returns_none(self, monkeypatch) -> None:
        monkeypatch.delenv("MNEMOSYNC_PLUGIN_PROXY", raising=False)
        assert _proxy_url() is None


# ---------------------------------------------------------------------------
# _parse_metadata_from_source
# ---------------------------------------------------------------------------

class TestParseMetadataFromSource:
    def test_parses_identity_plugin_class(self) -> None:
        source = '''
class MyPlugin(IdentityPlugin):
    name = "my-plugin"
    description = "A test plugin"
    version = "1.0.0"
    author = "Test Author"
'''
        m = _parse_metadata_from_source(source)
        assert m is not None
        assert m.name == "my-plugin"
        assert m.description == "A test plugin"
        assert m.version == "1.0.0"
        assert m.author == "Test Author"

    def test_parses_dotted_base_name(self) -> None:
        source = '''
class MyPlugin(plugin.IdentityPlugin):
    name = "dotted"
    description = "uses dotted import"
'''
        m = _parse_metadata_from_source(source)
        assert m is not None
        assert m.name == "dotted"

    def test_no_identity_plugin_class(self) -> None:
        source = '''
class RegularClass:
    name = "nope"
'''
        assert _parse_metadata_from_source(source) is None

    def test_no_attrs_returns_empty_metadata(self) -> None:
        source = '''
class EmptyPlugin(IdentityPlugin):
    pass
'''
        m = _parse_metadata_from_source(source)
        assert m is not None
        assert m.name == ""
        assert m.description == ""

    def test_invalid_syntax(self) -> None:
        assert _parse_metadata_from_source("def (broken") is None

    def test_non_string_attrs_ignored(self) -> None:
        source = '''
class P(IdentityPlugin):
    name = "ok"
    count = 42
    items = ["a", "b"]
'''
        m = _parse_metadata_from_source(source)
        assert m is not None
        assert m.name == "ok"


# ---------------------------------------------------------------------------
# _extract_class_attrs
# ---------------------------------------------------------------------------

import ast  # noqa: E402


class TestExtractClassAttrs:
    def test_extracts_string_attrs(self) -> None:
        source = '''
class Foo:
    name = "bar"
    description = "desc"
    version = "2.0"
'''
        tree = ast.parse(source)
        cls_node = tree.body[0]
        m = _extract_class_attrs(cls_node)
        assert m.name == "bar"
        assert m.description == "desc"
        assert m.version == "2.0"

    def test_empty_class(self) -> None:
        source = "class Empty:\n    pass"
        tree = ast.parse(source)
        m = _extract_class_attrs(tree.body[0])
        assert m.name == ""
        assert m.description == ""


# ---------------------------------------------------------------------------
# list_installed (patched PLUGIN_DIR)
# ---------------------------------------------------------------------------

class TestListInstalled:
    def test_finds_py_files(self, tmp_path: Path) -> None:
        from src.core.identity.plugin_manager import list_installed

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "astrbot.py").write_text(
            'class P(IdentityPlugin):\n    name = "astrbot"\n'
        )
        (plugin_dir / "_private.py").write_text("# ignored")
        (plugin_dir / "__init__.py").write_text("# ignored")

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            plugins = list_installed()
        names = [p.file_name for p in plugins]
        assert "astrbot.py" in names
        assert "_private.py" not in names

    def test_finds_subdir_plugins(self, tmp_path: Path) -> None:
        from src.core.identity.plugin_manager import list_installed

        plugin_dir = tmp_path / "plugins"
        sub = plugin_dir / "my-plugin"
        sub.mkdir(parents=True)
        (sub / "__init__.py").write_text(
            'class P(IdentityPlugin):\n    name = "sub"\n'
        )

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            plugins = list_installed()
        names = [p.file_name for p in plugins]
        assert "my-plugin/__init__.py" in names

    def test_empty_dir(self, tmp_path: Path) -> None:
        from src.core.identity.plugin_manager import list_installed

        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            assert list_installed() == []


# ---------------------------------------------------------------------------
# remove_plugin (patched PLUGIN_DIR)
# ---------------------------------------------------------------------------

class TestRemovePlugin:
    def test_remove_existing_file(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "target.py").write_text("# plugin")

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            assert remove_plugin("target.py") is True
            assert not (plugin_dir / "target.py").exists()

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            assert remove_plugin("ghost.py") is False

    def test_remove_subdir(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins"
        sub = plugin_dir / "my-plugin"
        sub.mkdir(parents=True)
        (sub / "__init__.py").write_text("# plugin")

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            assert remove_plugin("my-plugin/__init__.py") is True
            assert not sub.exists()

    def test_remove_subdir_not_exists(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()

        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            assert remove_plugin("nonexistent/__init__.py") is False


def test_available_route_version_comparison() -> None:
    """available 端点的 update_available 判定逻辑 (beta.16 插件更新).

    _version_key 语义化比较: 1.0.1 > 1.0.0 → 可更新; 相同/回退 → 不可更新.
    """
    from src.infra.update_checker import _version_key

    def has_update(remote: str, installed: str) -> bool:
        if not installed or not remote:
            return False
        try:
            return _version_key(remote) > _version_key(installed)
        except Exception:
            return False

    assert has_update("1.0.1", "1.0.0") is True
    assert has_update("1.1.0", "1.0.0") is True
    assert has_update("1.0.0", "1.0.0") is False
    assert has_update("1.0.0", "1.0.1") is False   # 远程更旧不提示降级
    assert has_update("1.0.1", "") is False        # 本地无版本信息不提示
    assert has_update("", "1.0.0") is False        # 远程无版本信息不提示


# ── raw 不可达 → contents API 兜底 (beta.24) ─────────────────────────


class _FakeResp:
    def __init__(self, status_code: int = 200, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data if json_data is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = type("R", (), {"status_code": self.status_code})()
            raise httpx.HTTPStatusError("err", request=request, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._json


class _FakeClient:
    """按 URL 前缀路由的假 httpx client."""

    routes: dict[str, _FakeResp] = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str, **kwargs) -> _FakeResp:
        for prefix, resp in _FakeClient.routes.items():
            if url.startswith(prefix):
                return resp
        raise httpx.ConnectError(f"no route for {url}", request=None)  # type: ignore[arg-type]


PLUGIN_SRC = (
    'class AstrBotPlugin(IdentityPlugin):\n'
    '    name = "astrbot"\n'
    '    version = "1.0.2"\n'
)


class TestRawFallback:
    """raw.githubusercontent 不可达时回退 contents API (beta.23 实测被墙)."""

    def setup_method(self) -> None:
        _FakeClient.routes = {}

    def test_fetch_metadata_falls_back_to_api(self, monkeypatch) -> None:
        raw_url = "https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/astrbot.py"
        encoded = base64.b64encode(
            b'class AstrBotPlugin(IdentityPlugin):\n    version = "1.0.2"\n'
        )
        _FakeClient.routes = {
            "https://raw.githubusercontent.com/": _FakeResp(503, text=""),
            "https://api.github.com/repos/HarryHello/mnemosync-plugins/contents/astrbot.py": _FakeResp(
                200, json_data={"content": encoded.decode()}
            ),
        }
        monkeypatch.setattr("src.core.identity.plugin_manager.httpx.AsyncClient", _FakeClient)
        metadata = asyncio.run(_fetch_metadata(raw_url))
        assert metadata is not None
        assert metadata.version == "1.0.2"

    def test_fetch_metadata_truncated_head_falls_back(self, monkeypatch) -> None:
        """4KB 头部不含元数据 (截断) 时也走 API 全文兜底."""
        raw_url = "https://raw.githubusercontent.com/o/r/main/big_plugin.py"
        _FakeClient.routes = {
            "https://raw.githubusercontent.com/o/r/main/big_plugin.py": _FakeResp(
                206, text="# 只是长注释, 4KB 内没有 class 元数据\n" * 200
            ),
            "https://api.github.com/repos/o/r/contents/big_plugin.py": _FakeResp(
                200,
                json_data={"content": base64.b64encode(
                    b'class Big(IdentityPlugin):\n    version = "2.0.0"\n'
                ).decode()},
            ),
        }
        monkeypatch.setattr("src.core.identity.plugin_manager.httpx.AsyncClient", _FakeClient)
        metadata = asyncio.run(_fetch_metadata(raw_url))
        assert metadata is not None and metadata.version == "2.0.0"

    def test_fetch_metadata_api_url_derived_from_raw(self) -> None:
        url = _api_contents_url_from_raw(
            "https://raw.githubusercontent.com/o/r/main/sub/pkg.py"
        )
        assert url == "https://api.github.com/repos/o/r/contents/sub/pkg.py?ref=main"
        assert _api_contents_url_from_raw("https://evil.com/x") is None

    def test_install_falls_back_to_api(self, tmp_path: Path, monkeypatch) -> None:
        raw_url = "https://raw.githubusercontent.com/HarryHello/mnemosync-plugins/main/astrbot.py"
        src = 'class AstrBotPlugin(IdentityPlugin):\n    version = "1.0.2"\n'
        _FakeClient.routes = {
            "https://raw.githubusercontent.com/": _FakeResp(503, text=""),
            "https://api.github.com/repos/HarryHello/mnemosync-plugins/contents/astrbot.py": _FakeResp(
                200, json_data={"content": base64.b64encode(src.encode()).decode()}
            ),
        }
        monkeypatch.setattr("src.core.identity.plugin_manager.httpx.AsyncClient", _FakeClient)
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        with patch("src.core.identity.plugin_manager.PLUGIN_DIR", plugin_dir):
            target = asyncio.run(install_plugin("astrbot.py", raw_url))
        assert (plugin_dir / "astrbot.py").read_text(encoding="utf-8") == src
        assert target.exists()
