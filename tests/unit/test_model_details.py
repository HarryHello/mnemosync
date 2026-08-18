"""Forwarder.list_model_details 能力解析测试 (v0.4.1).

验证: /v1/models 条目带的服务商扩展能力字段 (context_length / max_output_tokens /
input_modalities 等) 被尽力解析到 ModelDetail; 缺失回落默认; 未知字段忽略.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from src.infra.forwarder.forwarder import Forwarder, ForwarderConfig, ModelDetail


def _model(**kw: object) -> SimpleNamespace:
    """构造 openai SDK Model 桩: 已知字段 + model_extra 扩展字段."""
    base = {"id": "m1", "object": "model", "created": 1, "owned_by": "x"}
    default_extra: dict[str, object] = {}
    extra = kw.pop("model_extra", default_extra)
    merged = {**base, **kw}
    ns = SimpleNamespace(**merged)
    ns.model_extra = extra
    return ns


def _detail(config: SimpleNamespace) -> ModelDetail:
    """用 mock client 拉一次 list_model_details."""
    with patch(
        "src.infra.forwarder.forwarder.Forwarder._get_openai_client"
    ) as mock_get:
        client = AsyncMock()
        client.models.list = AsyncMock(return_value=SimpleNamespace(data=[config]))
        mock_get.return_value = client
        fwd = Forwarder(ForwarderConfig(base_url="https://x", api_key="k"))
        import asyncio

        return asyncio.run(fwd.list_model_details())[0]


class TestListModelDetails:
    @pytest.mark.asyncio
    async def test_standard_model_no_extra(self) -> None:
        """标准 OpenAI /v1/models: 只有 id, 能力回落默认."""
        with patch(
            "src.infra.forwarder.forwarder.Forwarder._get_openai_client"
        ) as mock_get:
            client = AsyncMock()
            client.models.list = AsyncMock(return_value=SimpleNamespace(
                data=[_model()],
            ))
            mock_get.return_value = client
            fwd = Forwarder(ForwarderConfig(base_url="https://x", api_key="k"))
            out = await fwd.list_model_details()
        assert len(out) == 1
        assert out[0].id == "m1"
        assert out[0].context_length is None
        assert out[0].output_limit is None
        assert out[0].input_modalities == ["text"]

    def test_capability_fields_parsed(self) -> None:
        """服务商扩展字段解析到 ModelDetail."""
        d = _detail(_model(model_extra={
            "context_length": 131072,
            "max_output_tokens": "8192",
            "input_modalities": ["text", "image"],
        }))
        assert d.context_length == 131072
        assert d.output_limit == 8192
        assert d.input_modalities == ["text", "image"]

    def test_alternate_key_names(self) -> None:
        """候选键名: max_context_window / output_token_limit."""
        d = _detail(_model(model_extra={
            "max_context_window": 65536,
            "output_token_limit": 4096,
        }))
        assert d.context_length == 65536
        assert d.output_limit == 4096

    def test_missing_fields_fall_back_text(self) -> None:
        d = _detail(_model(model_extra={"context_length": 100000}))
        assert d.output_limit is None
        assert d.input_modalities == ["text"]

    def test_bool_and_garbage_ignored(self) -> None:
        """布尔/非数值的扩展字段不应污染能力."""
        d = _detail(_model(model_extra={
            "context_length": True,
            "max_output_tokens": "abc",
        }))
        assert d.context_length is None
        assert d.output_limit is None

    def test_empty_id_skipped(self) -> None:
        with patch(
            "src.infra.forwarder.forwarder.Forwarder._get_openai_client"
        ) as mock_get:
            client = AsyncMock()
            client.models.list = AsyncMock(return_value=SimpleNamespace(
                data=[_model(id="")],
            ))
            mock_get.return_value = client
            fwd = Forwarder(ForwarderConfig(base_url="https://x", api_key="k"))
            import asyncio

            out = asyncio.run(fwd.list_model_details())
        assert out == []

    @pytest.mark.asyncio
    async def test_upstream_error_propagates(self) -> None:
        from openai import APIStatusError

        with patch(
            "src.infra.forwarder.forwarder.Forwarder._get_openai_client"
        ) as mock_get:
            client = AsyncMock()
            err = APIStatusError.__new__(APIStatusError)
            err.status_code = 429
            err.response = SimpleNamespace(text="ratelimited")
            client.models.list = AsyncMock(side_effect=err)
            mock_get.return_value = client
            fwd = Forwarder(ForwarderConfig(base_url="https://x", api_key="k"))
            from src.infra.forwarder.forwarder import UpstreamError

            with pytest.raises(UpstreamError) as ei:
                await fwd.list_model_details()
        assert ei.value.status_code == 429


def test_known_capability_fallback_for_deepseek() -> None:
    """DeepSeek /v1/models 只有 id: 内置表兜底回填上下文/输出/工具."""
    # 直接复用现有 _detail helper (mock client + run)
    d = _detail(_model(id="deepseek-chat"))
    assert d.context_length == 65536
    assert d.output_limit == 8192
    assert d.supports_tools is True


def test_known_capability_prefix_and_exact() -> None:
    # v4 精确命中: 1M 上下文 / 384K 输出 (用户确认)
    d = _detail(_model(id="deepseek-v4-flash"))
    assert d.context_length == 1048576
    assert d.output_limit == 393216
    assert d.supports_tools is True
    # 未知 deepseek- 前缀兜底 64K
    d2 = _detail(_model(id="deepseek-unknown-x"))
    assert d2.context_length == 65536


def test_upstream_declaration_wins_over_known_table() -> None:
    """上游显式声明的能力优先于内置表."""
    d = _detail(_model(model_extra={"context_length": 32000}))
    assert d.context_length == 32000




def test_openrouter_shape_parsed() -> None:
    """OpenRouter /v1/models 形状: 顶层 context_length + architecture 嵌套模态 + supported_parameters 工具."""
    d = _detail(_model(model_extra={
        "context_length": 262144,
        "architecture": {
            "modality": "text+image+video->text",
            "input_modalities": ["text", "image", "video"],
            "output_modalities": ["text"],
        },
        "supported_parameters": ["tools", "temperature", "max_tokens"],
    }))
    assert d.context_length == 262144
    assert d.input_modalities == ["text", "image", "video"]
    assert d.output_modalities == ["text"]
    assert d.supports_tools is True


def test_known_table_does_not_override_openrouter_context() -> None:
    """OpenRouter qwen 模型: 上游 context_length 优先于内置表."""
    d = _detail(_model(id="qwen/qwen3.8-27b", model_extra={"context_length": 262144}))
    assert d.context_length == 262144

def test_unknown_model_falls_back_to_defaults() -> None:
    d = _detail(_model(id="custom-model-123"))
    assert d.context_length is None
    assert d.output_limit is None
    assert d.supports_tools is False

