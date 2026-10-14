"""请求链路日志回归测试 (beta.27).

一次下游 /v1 请求 = 一条链路: 下游请求行 + 管线中各 agent 的上游调用行,
以 correlation_id 串联. 上游行由转发器调试发射点**常驻**写入 (与调试开
关无关 — 内存调试总线重启即失, 不能作为请求日志侧板的唯一载体).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from src.infra.debug_context import set_agent_name, set_correlation_id
from src.infra.forwarder.debug_utils import emit_upstream_debug, set_http_log_sink
from src.persistence.http_log_store import HttpLogStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[HttpLogStore]:
    s = HttpLogStore(str(tmp_path / "logs.db"))
    await s.connect()
    set_http_log_sink(s)
    yield s
    set_http_log_sink(None)
    await s.close()


async def _wait_rows(store: HttpLogStore, n: int) -> list[tuple]:
    rows: list[tuple] = []
    for _ in range(30):
        await store.flush_sync()
        rows = await store.list_paginated(page=1, page_size=100)
        if len(rows) >= n:
            return rows
        await asyncio.sleep(0.05)
    return rows


async def test_upstream_call_persisted_without_debug_bus(store: HttpLogStore) -> None:
    """上游 agent 调用常驻落库: 无调试总线也写, 带 cid/agent/direction."""
    set_correlation_id("cid-test-1")
    set_agent_name("main_dialogue")
    emit_upstream_debug(
        "upstream_request",
        "https://api.deepseek.com/chat/completions",
        method="POST",
        body={"model": "deepseek-flash", "messages": [{"role": "user", "content": "hi"}]},
    )
    emit_upstream_debug(
        "upstream_response",
        "https://api.deepseek.com/chat/completions",
        method="POST",
        status=200,
        duration_ms=123.4,
        body={"choices": [{"message": {"content": "你好"}}]},
    )
    rows = await _wait_rows(store, 2)
    assert len(rows) == 2
    # 行结构: correlation_id/direction/agent 追加在尾部 (11/12/13)
    for row in rows:
        assert row[11] == "cid-test-1"
        assert row[12] == "upstream"
        assert row[13] == "main_dialogue"

    # get_by_correlation_id 按 id 正序: [0]=请求行, [1]=响应行
    import json

    group = await store.get_by_correlation_id("cid-test-1")
    assert len(group) == 2
    req = json.loads(group[0][5])
    assert req["model"] == "deepseek-flash"
    resp = json.loads(group[1][7])
    assert resp["choices"][0]["message"]["content"] == "你好"


async def test_timeline_grouping_and_model_scope(store: HttpLogStore) -> None:
    """timeline 按 cid 聚合 (下游行 + 上游行); scope=model 只出 /v1 下游行."""
    set_correlation_id("cid-req-1")
    store.enqueue({
        "method": "POST", "path": "/v1/chat/completions",
        "request_body": {"q": 1}, "response_status": 200,
        "response_body": {"answer": 1}, "duration_ms": 12.0,
        "correlation_id": "cid-req-1", "direction": "downstream",
    })
    emit_upstream_debug(
        "upstream_request", "https://upstream.test/v1/chat",
        method="POST", body={"messages": []},
    )
    emit_upstream_debug(
        "upstream_response", "https://upstream.test/v1/chat",
        method="POST", status=200, duration_ms=50.0, body={"ok": True},
    )

    # 面板噪音: 另一个 cid 的 /panel 请求
    set_correlation_id("cid-panel")
    store.enqueue({
        "method": "GET", "path": "/panel/admin/notifications/unread-count",
        "response_status": 200, "duration_ms": 1.0,
        "correlation_id": "cid-panel", "direction": "downstream",
    })

    rows = await _wait_rows(store, 4)
    assert len(rows) == 4

    # scope=model: 只剩 /v1/ 下游行, 上游行与面板噪音都不进列表
    model_rows = await store.list_paginated(page=1, page_size=50, scope="model")
    assert len(model_rows) == 1
    assert model_rows[0][2] == "/v1/chat/completions"
    total = await store.count(scope="model")
    assert total == 1

    # timeline: 下游行 + 2 条上游行, 按写入顺序
    group = await store.get_by_correlation_id("cid-req-1")
    assert len(group) == 3
    assert group[0][2] == "/v1/chat/completions"
    assert all(r[12] == "upstream" for r in group[1:])
