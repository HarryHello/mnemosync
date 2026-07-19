"""MultiForwarder 多候选 fallback 单元测试.

在 ``Forwarder`` 方法层注入 mock, 验证:
- 首个候选成功即返回, 不尝试后续
- ``UpstreamTimeout`` / ``UpstreamError(5xx)`` / 连接错误触发 fallback
- ``UpstreamError(4xx)`` 直接抛, 不 fallback
- 全部失败抛 ``UpstreamAllCandidatesFailed``
- 空候选列表抛 ``NoCandidateForRoleError``
- 流式: 首字节前的错误 fallback; 首字节后的错误不切候选
- Forwarder 按 service_id 缓存, 同一 service 多次调用复用 client
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.models.resolver import NoCandidateForRoleError, RoleResolver
from src.infra.forwarder.forwarder import Forwarder, UpstreamError, UpstreamTimeout
from src.infra.forwarder.multi import (
    MultiForwarder,
    UpstreamAllCandidatesFailed,
    _should_fallback,
)
from src.infra.llm_service.models import LLMServiceProvider, ModelType
from src.infra.llm_service.store import LLMServiceStore


@pytest.fixture
async def resolver(tmp_path):
    """预置三个服务, 三个 main 候选."""
    store = LLMServiceStore(str(tmp_path / "llm.db"))
    await store.init_db()
    for i, letter in enumerate("abc"):
        await store.save_service(
            LLMServiceProvider.create(f"svc-{letter}", f"https://{letter}.example/v1", f"sk-{letter}")
        )
        await store.add_role_binding(ModelType.MAIN, f"svc-{letter}", f"model-{letter}")
    return RoleResolver(store)


# ─── _should_fallback 分流 ────────────────────────────────

def test_fallback_predicate():
    assert _should_fallback(UpstreamTimeout("timeout"))
    assert _should_fallback(UpstreamError(500, "boom"))
    assert _should_fallback(UpstreamError(502, "bad gateway"))
    assert _should_fallback(UpstreamError(None, "network"))
    assert _should_fallback(httpx.ConnectError("refused"))
    assert _should_fallback(httpx.ReadError("read"))

    # 4xx 不 fallback
    assert not _should_fallback(UpstreamError(400, "bad request"))
    assert not _should_fallback(UpstreamError(401, "unauthorized"))
    assert not _should_fallback(UpstreamError(429, "rate limit"))

    # 其他非 upstream 异常也不 fallback (让它冒到调用方)
    assert not _should_fallback(ValueError("wrong param"))


# ─── 非流式 chat ──────────────────────────────────────────

async def test_chat_first_candidate_wins(resolver):
    multi = MultiForwarder(resolver)
    with patch.object(Forwarder, "chat", new=AsyncMock(return_value={"id": "ok"})) as m:
        result = await multi.chat(ModelType.MAIN, messages=[{"role": "user", "content": "hi"}])
        assert result == {"id": "ok"}
        # 只调用一次
        assert m.call_count == 1


async def test_chat_falls_back_on_5xx(resolver):
    multi = MultiForwarder(resolver)
    call_log: list[str] = []

    async def side_effect(*, model, **_):
        call_log.append(model)
        if model == "model-a":
            raise UpstreamError(503, "unavailable")
        if model == "model-b":
            raise UpstreamTimeout("slow")
        return {"id": "from-c", "model": model}

    with patch.object(Forwarder, "chat", new=AsyncMock(side_effect=side_effect)):
        result = await multi.chat(ModelType.MAIN, messages=[])
        assert result == {"id": "from-c", "model": "model-c"}
        assert call_log == ["model-a", "model-b", "model-c"]


async def test_chat_does_not_fallback_on_4xx(resolver):
    multi = MultiForwarder(resolver)
    call_log: list[str] = []

    async def side_effect(*, model, **_):
        call_log.append(model)
        raise UpstreamError(400, "bad request")

    with patch.object(Forwarder, "chat", new=AsyncMock(side_effect=side_effect)):
        with pytest.raises(UpstreamError) as exc_info:
            await multi.chat(ModelType.MAIN, messages=[])
        assert exc_info.value.status_code == 400
        assert call_log == ["model-a"]  # 只尝试了第一个


async def test_chat_all_fail_raises_aggregate(resolver):
    multi = MultiForwarder(resolver)

    async def side_effect(*, model, **_):
        raise UpstreamTimeout(f"{model} timeout")

    with patch.object(Forwarder, "chat", new=AsyncMock(side_effect=side_effect)):
        with pytest.raises(UpstreamAllCandidatesFailed) as exc_info:
            await multi.chat(ModelType.MAIN, messages=[])
        err = exc_info.value
        assert err.role == ModelType.MAIN
        assert len(err.errors) == 3
        assert [c.model for c, _ in err.errors] == ["model-a", "model-b", "model-c"]


async def test_chat_empty_candidates_raises(tmp_path):
    store = LLMServiceStore(str(tmp_path / "empty.db"))
    await store.init_db()
    resolver = RoleResolver(store)
    multi = MultiForwarder(resolver)
    with pytest.raises(NoCandidateForRoleError):
        await multi.chat(ModelType.MAIN, messages=[])


async def test_chat_connection_error_fallbacks(resolver):
    multi = MultiForwarder(resolver)
    call_log: list[str] = []

    async def side_effect(*, model, **_):
        call_log.append(model)
        if model == "model-a":
            raise httpx.ConnectError("connection refused")
        return {"id": "ok", "model": model}

    with patch.object(Forwarder, "chat", new=AsyncMock(side_effect=side_effect)):
        result = await multi.chat(ModelType.MAIN, messages=[])
        assert result["model"] == "model-b"
        assert call_log == ["model-a", "model-b"]


# ─── 流式 chat_stream ────────────────────────────────────

async def _drain(gen: AsyncIterator[bytes]) -> list[bytes]:
    out: list[bytes] = []
    async for chunk in gen:
        out.append(chunk)
    return out


async def test_stream_first_candidate_wins(resolver):
    multi = MultiForwarder(resolver)

    async def fake_stream(self, **_):
        yield b"data: chunk1\n"
        yield b"data: chunk2\n"

    with patch.object(Forwarder, "chat_stream", new=fake_stream):
        chunks = await _drain(multi.chat_stream(ModelType.MAIN, messages=[]))
        assert chunks == [b"data: chunk1\n", b"data: chunk2\n"]


async def test_stream_falls_back_before_first_byte(resolver):
    multi = MultiForwarder(resolver)
    log: list[str] = []

    async def fake_stream(self, *, model, **_):
        log.append(model)
        if model in ("model-a", "model-b"):
            raise UpstreamError(503, "unavailable")
            yield  # unreachable, makes this an async generator  # pragma: no cover
        else:
            yield b"ok-chunk"

    with patch.object(Forwarder, "chat_stream", new=fake_stream):
        chunks = await _drain(multi.chat_stream(ModelType.MAIN, messages=[]))
        assert chunks == [b"ok-chunk"]
        assert log == ["model-a", "model-b", "model-c"]


async def test_stream_does_not_fallback_after_first_byte(resolver):
    """一旦已经产出 chunk, 中途中断不切候选."""
    multi = MultiForwarder(resolver)
    log: list[str] = []

    async def fake_stream(self, *, model, **_):
        log.append(model)
        yield b"first-chunk"
        # 首字节后崩了
        raise UpstreamError(503, "died mid-stream")

    with patch.object(Forwarder, "chat_stream", new=fake_stream):
        chunks: list[bytes] = []
        with pytest.raises(UpstreamError):
            async for c in multi.chat_stream(ModelType.MAIN, messages=[]):
                chunks.append(c)
        assert chunks == [b"first-chunk"]
        assert log == ["model-a"]  # 没切到 model-b


async def test_stream_4xx_does_not_fallback(resolver):
    multi = MultiForwarder(resolver)
    log: list[str] = []

    async def fake_stream(self, *, model, **_):
        log.append(model)
        raise UpstreamError(400, "bad")
        yield  # pragma: no cover

    with patch.object(Forwarder, "chat_stream", new=fake_stream):
        with pytest.raises(UpstreamError) as exc_info:
            async for _ in multi.chat_stream(ModelType.MAIN, messages=[]):
                pass  # pragma: no cover
        assert exc_info.value.status_code == 400
        assert log == ["model-a"]


async def test_stream_all_fail_raises_aggregate(resolver):
    multi = MultiForwarder(resolver)

    async def fake_stream(self, *, model, **_):
        raise UpstreamTimeout(f"{model} slow")
        yield  # pragma: no cover

    with patch.object(Forwarder, "chat_stream", new=fake_stream):
        with pytest.raises(UpstreamAllCandidatesFailed):
            async for _ in multi.chat_stream(ModelType.MAIN, messages=[]):
                pass  # pragma: no cover


# ─── embed / rerank ──────────────────────────────────────

async def test_embed_uses_first_candidate_no_fallback(tmp_path):
    """嵌入是单绑定, 不 fallback: 上游报错直接抛."""
    store = LLMServiceStore(str(tmp_path / "e.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.add_role_binding(ModelType.EMBEDDING, "a", "embed-1")
    multi = MultiForwarder(RoleResolver(store))

    async def side_effect(*, model, **_):
        assert model == "embed-1"
        return [[0.1, 0.2]]

    with patch.object(Forwarder, "embed", new=AsyncMock(side_effect=side_effect)):
        vecs = await multi.embed("hello")
        assert vecs == [[0.1, 0.2]]


async def test_embed_no_fallback_on_error(tmp_path):
    """嵌入上游 5xx 也不 fallback (语义空间不兼容)."""
    store = LLMServiceStore(str(tmp_path / "e.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.add_role_binding(ModelType.EMBEDDING, "a", "embed-1")
    multi = MultiForwarder(RoleResolver(store))

    with patch.object(
        Forwarder,
        "embed",
        new=AsyncMock(side_effect=UpstreamError(500, "down")),
    ):
        with pytest.raises(UpstreamError):
            await multi.embed("hello")


async def test_embed_does_not_send_dimensions_by_default(tmp_path):
    """绑定即使带 embedding_dim, 只要 send_dimensions=False (默认) 就不透传上游.

    v0.2.8 变更: 兼容 bge/bce/jina/mistral/gemini 等固定维模型.
    """
    store = LLMServiceStore(str(tmp_path / "e.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.add_role_binding(
        ModelType.EMBEDDING, "a", "bge-m3", embedding_dim=1024
    )
    multi = MultiForwarder(RoleResolver(store))

    captured: dict = {}

    async def side_effect(*, model, dimensions=None, **_):
        captured["dimensions"] = dimensions
        return [[0.0] * 1024]

    with patch.object(Forwarder, "embed", new=AsyncMock(side_effect=side_effect)):
        await multi.embed("hi")
    assert captured["dimensions"] is None


async def test_embed_sends_dimensions_when_flag_true(tmp_path):
    """send_dimensions=True 时把 embedding_dim 作为 dimensions 透传上游.

    这是可变维模型 (text-embedding-3-*, qwen3-embedding-*, DashScope v3/v4) 的用法.
    """
    store = LLMServiceStore(str(tmp_path / "e.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.add_role_binding(
        ModelType.EMBEDDING, "a", "text-embedding-v3",
        embedding_dim=1024, send_dimensions=True,
    )
    multi = MultiForwarder(RoleResolver(store))

    captured: dict = {}

    async def side_effect(*, model, dimensions=None, **_):
        captured["dimensions"] = dimensions
        return [[0.0] * 1024]

    with patch.object(Forwarder, "embed", new=AsyncMock(side_effect=side_effect)):
        await multi.embed("hi")
    assert captured["dimensions"] == 1024


async def test_embed_explicit_dimensions_overrides_binding(tmp_path):
    """调用方显式传 dimensions 时无条件透传, 无视 send_dimensions.

    probe-dimension 端点在探测阶段依赖这条路径.
    """
    store = LLMServiceStore(str(tmp_path / "e.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.add_role_binding(
        ModelType.EMBEDDING, "a", "bge-m3",
        embedding_dim=1024, send_dimensions=False,
    )
    multi = MultiForwarder(RoleResolver(store))

    captured: dict = {}

    async def side_effect(*, model, dimensions=None, **_):
        captured["dimensions"] = dimensions
        return [[0.0] * 512]

    with patch.object(Forwarder, "embed", new=AsyncMock(side_effect=side_effect)):
        await multi.embed("hi", dimensions=512)
    assert captured["dimensions"] == 512


async def test_rerank_falls_back(tmp_path):
    store = LLMServiceStore(str(tmp_path / "r.db"))
    await store.init_db()
    await store.save_service(LLMServiceProvider.create("a", "https://a", "sk-a"))
    await store.save_service(LLMServiceProvider.create("b", "https://b", "sk-b"))
    await store.add_role_binding(ModelType.RERANK, "a", "rerank-1")
    await store.add_role_binding(ModelType.RERANK, "b", "rerank-2")
    multi = MultiForwarder(RoleResolver(store))

    async def side_effect(*, model, **_):
        if model == "rerank-1":
            raise UpstreamTimeout("slow")
        return [{"index": 0, "relevance_score": 0.9, "document": "doc-a"}]

    with patch.object(Forwarder, "rerank", new=AsyncMock(side_effect=side_effect)):
        results = await multi.rerank("q", ["doc-a", "doc-b"])
        assert results == [{"index": 0, "relevance_score": 0.9, "document": "doc-a"}]


# ─── Forwarder 复用 ──────────────────────────────────────

async def test_forwarder_cached_per_service(resolver):
    multi = MultiForwarder(resolver)
    with patch.object(Forwarder, "chat", new=AsyncMock(return_value={"ok": True})):
        await multi.chat(ModelType.MAIN, messages=[])
        await multi.chat(ModelType.MAIN, messages=[])
    # 三个 service, 但只有 svc-a 用到 (第一个成功)
    assert set(multi._forwarders.keys()) == {"svc-a"}


async def test_close_disposes_forwarders(resolver):
    multi = MultiForwarder(resolver)
    async def make_fail(*, model, **_):
        raise UpstreamError(500, "x")
    with patch.object(Forwarder, "chat", new=AsyncMock(side_effect=make_fail)):
        with pytest.raises(UpstreamAllCandidatesFailed):
            await multi.chat(ModelType.MAIN, messages=[])
    assert len(multi._forwarders) == 3
    await multi.close()
    assert multi._forwarders == {}
