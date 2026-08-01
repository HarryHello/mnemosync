"""测试 HttpLogStore 的写入、查询和清理.

HttpLogStore 使用后台批量写盘 (enqueue + writer_loop),
测试中通过 await asyncio.sleep() 等待 writer flush.
"""

from __future__ import annotations

import asyncio

import pytest
from src.persistence.http_log_store import HttpLogStore


@pytest.fixture
async def store(tmp_path):
    s = HttpLogStore(str(tmp_path / "http_logs.db"))
    await s.connect()
    yield s
    await s.close()


def _make_entry(
    method: str = "GET",
    path: str = "/api/test",
    status: int = 200,
    duration_ms: float = 12.5,
) -> dict:
    return {
        "method": method,
        "path": path,
        "query_params": None,
        "request_headers": {"User-Agent": "test"},
        "request_body": None,
        "response_status": status,
        "response_body": {"ok": True},
        "duration_ms": duration_ms,
        "client_ip": "127.0.0.1",
    }


async def _flush_and_wait(store: HttpLogStore) -> None:
    """Wait for the background writer to flush the queue."""
    # Give the writer_loop time to process the batch
    await asyncio.sleep(1.0)


# ─── 写入 + 查询 ───────────────────────────────────────


async def test_enqueue_and_list_paginated(store):
    store.enqueue(_make_entry(method="GET", path="/a", status=200))
    store.enqueue(_make_entry(method="POST", path="/b", status=201))
    store.enqueue(_make_entry(method="GET", path="/c", status=404))
    await _flush_and_wait(store)

    rows = await store.list_paginated(page=1, page_size=10)
    assert len(rows) == 3


async def test_list_paginated_filters_by_method(store):
    store.enqueue(_make_entry(method="GET", path="/a"))
    store.enqueue(_make_entry(method="POST", path="/b"))
    await _flush_and_wait(store)

    rows = await store.list_paginated(page=1, page_size=10, method="GET")
    assert len(rows) == 1
    # method is stored uppercase
    assert rows[0][1] == "GET"


async def test_list_paginated_filters_by_status(store):
    store.enqueue(_make_entry(status=200))
    store.enqueue(_make_entry(status=500))
    await _flush_and_wait(store)

    rows = await store.list_paginated(page=1, page_size=10, status=500)
    assert len(rows) == 1
    assert rows[0][6] == 500


async def test_list_paginated_filters_by_path(store):
    store.enqueue(_make_entry(path="/api/users"))
    store.enqueue(_make_entry(path="/api/orders"))
    await _flush_and_wait(store)

    rows = await store.list_paginated(page=1, page_size=10, path="users")
    assert len(rows) == 1


# ─── count ─────────────────────────────────────────────


async def test_count(store):
    assert await store.count() == 0
    store.enqueue(_make_entry())
    store.enqueue(_make_entry())
    await _flush_and_wait(store)
    assert await store.count() == 2


async def test_count_filters(store):
    store.enqueue(_make_entry(method="GET"))
    store.enqueue(_make_entry(method="POST"))
    await _flush_and_wait(store)
    assert await store.count(method="GET") == 1


# ─── clear_all ─────────────────────────────────────────


async def test_clear_all(store):
    store.enqueue(_make_entry())
    store.enqueue(_make_entry())
    await _flush_and_wait(store)
    assert await store.count() == 2

    await store.clear_all()
    assert await store.count() == 0


# ─── cleanup ───────────────────────────────────────────


async def test_cleanup_removes_old_records(store):
    """cleanup 应能删除超过保留天数的记录."""
    store.enqueue(_make_entry())
    await _flush_and_wait(store)

    # Artificially age the record by updating created_at to 100 days ago
    async with store._conn() as db:
        await db.execute(
            "UPDATE http_logs SET created_at = datetime('now', '-100 days')"
        )
        await db.commit()

    assert await store.count() == 1
    await store.cleanup(retention_days=30, max_records=1000)
    assert await store.count() == 0


async def test_cleanup_keeps_recent_records(store):
    store.enqueue(_make_entry())
    await _flush_and_wait(store)

    # Record is fresh (< 1 day old), should survive cleanup
    await store.cleanup(retention_days=30, max_records=1000)
    assert await store.count() == 1


async def test_cleanup_trims_by_max_records(store):
    """超出 max_records 的旧记录应被删除."""
    for _ in range(5):
        store.enqueue(_make_entry())
    await _flush_and_wait(store)
    assert await store.count() == 5

    await store.cleanup(retention_days=365, max_records=3)
    assert await store.count() == 3


# ─── get_by_id ─────────────────────────────────────────


async def test_get_by_id(store):
    store.enqueue(_make_entry(method="PUT", path="/resource"))
    await _flush_and_wait(store)

    rows = await store.list_paginated(page=1, page_size=1)
    log_id = rows[0][0]
    row = await store.get_by_id(log_id)
    assert row is not None
    assert row[1] == "PUT"
    assert row[2] == "/resource"


async def test_get_by_id_nonexistent(store):
    assert await store.get_by_id(99999) is None


# ─── 边界 ──────────────────────────────────────────────


async def test_enqueue_before_connect_is_noop():
    """enqueue 在 connect 前调用不应崩溃."""
    s = HttpLogStore("/tmp/test_http_log_noop.db")
    s.enqueue(_make_entry())  # queue is None, should silently skip


async def test_list_paginated_page_numbering(store):
    for i in range(5):
        store.enqueue(_make_entry(path=f"/item-{i}"))
    await _flush_and_wait(store)

    page1 = await store.list_paginated(page=1, page_size=2)
    page2 = await store.list_paginated(page=2, page_size=2)
    page3 = await store.list_paginated(page=3, page_size=2)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # No overlap between pages
    ids1 = {r[0] for r in page1}
    ids2 = {r[0] for r in page2}
    ids3 = {r[0] for r in page3}
    assert ids1.isdisjoint(ids2)
    assert ids1.isdisjoint(ids3)
    assert ids2.isdisjoint(ids3)
