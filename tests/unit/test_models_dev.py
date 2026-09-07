"""models.dev 实时能力源测试 (v0.4.1).

验证: api.json 扁平化索引 / 精确与命名空间查找 / TTL 缓存与失败短路 /
兜底链顺序 (上游声明 → models.dev → 内置静态表).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from src.infra.forwarder import models_dev
from src.infra.forwarder.models_dev import (
    build_models_dev_index,
    fetch_models_dev_index,
    lookup_models_dev,
    reset_models_dev_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_models_dev_cache()
    yield
    reset_models_dev_cache()


# ── api.json 样例 (简化自真实结构) ──────────────────────────────────────────
_SAMPLE: dict = {
    "deepseek": {
        "id": "deepseek",
        "models": {
            "deepseek-v4-flash": {
                "tool_call": True,
                "limit": {"context": 1000000, "output": 384000},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
            "deepseek-v4-flash-vision-exp": {
                "tool_call": True,
                "limit": {"context": 65536, "output": 8192},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
    },
    "openrouter": {
        "id": "openrouter",
        "models": {
            "deepseek/deepseek-v4-flash": {
                "tool_call": True,
                "limit": {"context": 1000000, "output": 384000},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
    "broken-provider": "not-a-dict",
}


class TestBuildIndex:
    def test_flattens_providers(self) -> None:
        idx = build_models_dev_index(_SAMPLE)
        assert set(idx) == {
            "deepseek-v4-flash",
            "deepseek-v4-flash-vision-exp",
            "deepseek/deepseek-v4-flash",
        }

    def test_capability_mapping(self) -> None:
        idx = build_models_dev_index(_SAMPLE)
        cap = idx["deepseek-v4-flash-vision-exp"]
        assert cap.context_length == 65536
        assert cap.output_limit == 8192
        assert cap.supports_tools is True
        assert cap.input_modalities == ("text", "image")

    def test_missing_fields_default(self) -> None:
        idx = build_models_dev_index({
            "p": {"models": {"m1": {"tool_call": False}}},
        })
        cap = idx["m1"]
        assert cap.context_length is None
        assert cap.output_limit is None
        assert cap.supports_tools is False
        assert cap.input_modalities == ("text",)

    def test_garbage_values_ignored(self) -> None:
        idx = build_models_dev_index({
            "p": {"models": {
                "m1": {"limit": {"context": True, "output": "big"}},
                "m2": "not-a-dict",
            }},
        })
        assert idx["m1"].context_length is None
        assert idx["m1"].output_limit is None
        assert "m2" not in idx


class TestLookup:
    def test_exact_and_namespaced(self) -> None:
        idx = build_models_dev_index(_SAMPLE)
        assert lookup_models_dev(idx, "deepseek-v4-flash") is not None
        assert lookup_models_dev(idx, "deepseek/deepseek-v4-flash") is not None
        # 命名空间前缀去除后命中官方条目
        assert lookup_models_dev(idx, "other/deepseek-v4-flash") is not None

    def test_miss(self) -> None:
        idx = build_models_dev_index(_SAMPLE)
        assert lookup_models_dev(idx, "gpt-99") is None
        assert lookup_models_dev(None, "deepseek-v4-flash") is None


class _FakeResp:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise httpx.HTTPStatusError(
                "err", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeResp:
        return self._resp


class TestFetch:
    def test_success_and_ttl_cache(self) -> None:
        with patch.object(models_dev.httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResp(_SAMPLE))):
            idx1 = asyncio.run(fetch_models_dev_index())
            idx2 = asyncio.run(fetch_models_dev_index())
        assert idx1 is not None and len(idx1) == 3
        assert idx2 is idx1  # TTL 内复用缓存, 不打网络

    def test_http_failure_short_circuits(self) -> None:
        calls = []

        def _factory(**kw: object) -> _FakeClient:
            calls.append(1)
            return _FakeClient(_FakeResp(None, status=500))

        with patch.object(models_dev.httpx, "AsyncClient", _factory):
            assert asyncio.run(fetch_models_dev_index()) is None
            # 失败后进入短路窗口: 不再打网络
            assert asyncio.run(fetch_models_dev_index()) is None
        assert len(calls) == 1

    def test_empty_directory_fails(self) -> None:
        with patch.object(
            models_dev.httpx, "AsyncClient",
            lambda **kw: _FakeClient(_FakeResp({})),
        ):
            assert asyncio.run(fetch_models_dev_index()) is None

    def test_network_error_returns_none(self) -> None:
        class _BoomClient(_FakeClient):
            async def get(self, url: str) -> _FakeResp:
                raise httpx.ConnectError("offline")

        with patch.object(
            models_dev.httpx, "AsyncClient",
            lambda **kw: _BoomClient(_FakeResp(_SAMPLE)),
        ):
            assert asyncio.run(fetch_models_dev_index()) is None


# ── forwarder 兜底链集成 ────────────────────────────────────────────────────

def _detail_with_dev(model: SimpleNamespace, dev_index: dict | None) -> object:
    """mock openai client + 注入 dev_index 拉一次 list_model_details."""
    from src.infra.forwarder.forwarder import Forwarder, ForwarderConfig

    async def _idx() -> dict | None:
        return dev_index

    with (
        patch("src.infra.forwarder.forwarder.Forwarder._get_openai_client") as mock_get,
        patch("src.infra.forwarder.forwarder.fetch_models_dev_index", _idx),
    ):
        client = AsyncMock()
        client.models.list = AsyncMock(return_value=SimpleNamespace(data=[model]))
        mock_get.return_value = client
        fwd = Forwarder(ForwarderConfig(base_url="https://x", api_key="k"))
        return asyncio.run(fwd.list_model_details())[0]


def _model(**kw: object) -> SimpleNamespace:
    base = {"id": "m1", "object": "model", "created": 1, "owned_by": "x"}
    extra = kw.pop("model_extra", {})
    ns = SimpleNamespace(**{**base, **kw})
    ns.model_extra = extra
    return ns


class TestFallbackChain:
    def test_dev_wins_over_static_table(self) -> None:
        """models.dev 声明优先于内置静态表 (同 id 数值不同时)."""
        dev = build_models_dev_index(_SAMPLE)
        # deepseek-v4-flash 静态表 1048576, dev 目录 1000000 → 取 dev
        d = _detail_with_dev(_model(id="deepseek-v4-flash"), dev)
        assert d.context_length == 1000000
        assert d.output_limit == 384000

    def test_dev_new_model_filled(self) -> None:
        """静态表没有的新模型 (v4f-ve) 由 models.dev 补齐."""
        dev = build_models_dev_index(_SAMPLE)
        d = _detail_with_dev(_model(id="deepseek-v4-flash-vision-exp"), dev)
        assert d.context_length == 65536
        assert d.input_modalities == ["text", "image"]
        assert d.supports_tools is True

    def test_static_fallback_when_dev_misses(self) -> None:
        """dev 目录没有的 id 回落静态表前缀匹配."""
        dev = build_models_dev_index(_SAMPLE)
        d = _detail_with_dev(_model(id="deepseek-unknown-x"), dev)
        assert d.context_length == 65536

    def test_upstream_declaration_wins_over_dev(self) -> None:
        """上游显式声明始终优先."""
        dev = build_models_dev_index(_SAMPLE)
        d = _detail_with_dev(
            _model(id="deepseek-v4-flash", model_extra={"context_length": 32000}), dev
        )
        assert d.context_length == 32000

    def test_dev_none_falls_to_static(self) -> None:
        """dev 目录拉取失败 (None) 时行为与旧链路一致."""
        d = _detail_with_dev(_model(id="deepseek-chat"), None)
        assert d.context_length == 65536
        assert d.supports_tools is True
