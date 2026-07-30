"""DebugEventBus 单元测试.

测试点:
  * 惰性 emit: 0 订阅者时 emit 返回 None, buffer 不变
  * ring buffer 容量
  * subscribe / unsubscribe 计数; grace timer 触发
  * stream chunk + finalize
  * headers 脱敏 (Bearer token 中间打码)
"""

from __future__ import annotations

import asyncio

import pytest
from src.infra.debug_bus import BODY_PREVIEW_MAX, DebugEventBus, _redact_headers


@pytest.mark.asyncio
async def test_should_emit_gates_on_subscribers():
    bus = DebugEventBus(capacity=10)
    assert not bus.should_emit()
    eid = bus.emit(direction="inbound_request", correlation_id="c1", url="/v1/x")
    assert eid is None
    assert bus.list_recent() == []


@pytest.mark.asyncio
async def test_emit_after_subscribe_and_deliver():
    bus = DebugEventBus(capacity=10)
    sub_id, q = await bus.subscribe()
    try:
        eid = bus.emit(
            direction="inbound_request",
            correlation_id="c1",
            url="/v1/chat/completions",
            method="POST",
            body={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert eid is not None
        summary = await asyncio.wait_for(q.get(), timeout=1.0)
        assert summary.id == eid
        assert summary.direction == "inbound_request"
        assert summary.correlation_id == "c1"
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_ring_buffer_evicts_oldest():
    bus = DebugEventBus(capacity=3)
    sub_id, _ = await bus.subscribe()
    try:
        for i in range(5):
            bus.emit(direction="upstream_request", correlation_id=f"c{i}", url="/x")
        recent = bus.list_recent(limit=10)
        assert len(recent) == 3
        # 保留的应是最后 3 条
        assert [e.correlation_id for e in recent] == ["c2", "c3", "c4"]
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_grace_callback_fires_when_subscribers_drop_to_zero():
    bus = DebugEventBus(capacity=10, grace_seconds=0.05)
    called = asyncio.Event()

    async def _cb():
        called.set()

    bus.set_grace_callback(_cb)
    sub_id, _ = await bus.subscribe()
    await bus.unsubscribe(sub_id)
    await asyncio.wait_for(called.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_resubscribe_cancels_grace_timer():
    bus = DebugEventBus(capacity=10, grace_seconds=1.0)
    called = asyncio.Event()

    async def _cb():
        called.set()

    bus.set_grace_callback(_cb)
    sub_id, _ = await bus.subscribe()
    await bus.unsubscribe(sub_id)
    # 立刻再订阅, 应取消 grace timer
    sub_id2, _ = await bus.subscribe()
    await asyncio.sleep(0.15)  # < grace
    assert not called.is_set()
    await bus.unsubscribe(sub_id2)


@pytest.mark.asyncio
async def test_stream_chunk_and_finalize():
    bus = DebugEventBus(capacity=10)
    sub_id, q = await bus.subscribe()
    try:
        eid = bus.emit(direction="upstream_request", correlation_id="c", url="/u", agent="main")
        assert eid is not None
        await asyncio.wait_for(q.get(), timeout=1.0)  # 消费初始事件
        bus.append_stream_chunk(eid, b'data: {"delta": "hel"}\n\n')
        bus.append_stream_chunk(eid, b'data: {"delta": "lo"}\n\n')
        bus.finalize_stream(eid, assembled="hello", status=200, duration_ms=42.0)
        final = await asyncio.wait_for(q.get(), timeout=1.0)
        assert final.id == eid
        assert final.direction.endswith("_final")
        assert final.status == 200
        assert final.duration_ms == 42.0
        detail = bus.get_full(eid)
        assert detail is not None
        assert detail["stream_assembled"] == "hello"
        assert detail["stream_chunks_count"] == 2
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_get_full_returns_none_for_unknown():
    bus = DebugEventBus(capacity=5)
    assert bus.get_full("nope") is None


def test_redact_bearer_token_keeps_prefix_suffix():
    h = {"Authorization": "Bearer sk-abcdefghijklmn12345678"}
    out = _redact_headers(h)
    assert out["Authorization"].startswith("Bearer sk-abc")
    assert "5678" in out["Authorization"]
    assert "****" in out["Authorization"]


def test_redact_short_bearer():
    out = _redact_headers({"Authorization": "Bearer short"})
    assert out["Authorization"] == "Bearer ****"


@pytest.mark.asyncio
async def test_emit_pipeline_no_subscribers():
    """emit_pipeline 在无订阅者时应静默跳过."""
    bus = DebugEventBus()
    result = bus.emit_pipeline(correlation_id="cid", event_kind="test", data={"x": 1})
    assert result is None


@pytest.mark.asyncio
async def test_emit_pipeline_delivers_to_subscribers():
    """emit_pipeline 应将管线事件推送给订阅者."""
    bus = DebugEventBus()
    sub_id, q = await bus.subscribe()
    try:
        eid = bus.emit_pipeline(
            correlation_id="cid-1",
            event_kind="tool_policy",
            data={"stage": "inbound", "removed_tools": ["poke"]},
        )
        assert eid is not None
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev.direction == "pipeline"
        assert ev.url == "pipeline:tool_policy"
        assert ev.correlation_id == "cid-1"
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_emit_pipeline_helper_safe_with_none():
    """emit_pipeline 辅助函数在 bus=None 时应安全跳过."""
    from src.infra.debug_context import emit_pipeline, set_correlation_id
    set_correlation_id("cid-test")
    emit_pipeline(None, event_kind="test", data={"x": 1})
    # Should not raise


def test_redact_cookie_header():
    out = _redact_headers({"Cookie": "session=xyz"})
    assert out["Cookie"] == "***"


@pytest.mark.asyncio
async def test_body_preview_truncates_large_bodies():
    bus = DebugEventBus(capacity=5)
    sub_id, _ = await bus.subscribe()
    try:
        big = "x" * (BODY_PREVIEW_MAX + 100)
        eid = bus.emit(direction="upstream_response", correlation_id="c", url="/u", body=big)
        assert eid is not None
        detail = bus.get_full(eid)
        assert detail is not None
        assert detail["summary"]["is_truncated"] is True
        assert detail["summary"]["body_full_size"] == len(big)
    finally:
        await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_clear_wipes_buffer_and_lookup():
    bus = DebugEventBus(capacity=5)
    sub_id, _ = await bus.subscribe()
    try:
        eid = bus.emit(direction="inbound_request", correlation_id="c", url="/x", body={"k": 1})
        assert eid is not None
        assert bus.get_full(eid) is not None
        bus.clear()
        assert bus.list_recent() == []
        assert bus.get_full(eid) is None
    finally:
        await bus.unsubscribe(sub_id)
