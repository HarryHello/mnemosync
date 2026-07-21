"""NotificationStore 单元测试.

覆盖:
- add + get: level 校验 / meta_json 编解码
- list_page: created_at DESC 排序 + 分页 + unread_only 过滤
- count_unread / mark_read / mark_all_read / delete_by_id
- mark_read 二次调用幂等 (返回 False)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.persistence.notification_store import NotificationStore


@pytest.fixture
async def store(tmp_path: Path) -> NotificationStore:
    s = NotificationStore(str(tmp_path / "n.db"))
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_add_and_get_roundtrip(store: NotificationStore) -> None:
    nid = await store.add(
        level="warning",
        category="memory_write_failed",
        title="记忆入库失败",
        message="upstream 5xx",
        meta={"stage": "embed", "upstream_status": 502},
    )
    assert nid > 0

    got = await store.get(nid)
    assert got is not None
    assert got.level == "warning"
    assert got.category == "memory_write_failed"
    assert got.title == "记忆入库失败"
    assert got.message == "upstream 5xx"
    assert got.meta == {"stage": "embed", "upstream_status": 502}
    assert got.read_at is None


@pytest.mark.asyncio
async def test_add_rejects_invalid_level(store: NotificationStore) -> None:
    with pytest.raises(ValueError):
        await store.add(level="fatal", category="c", title="t", message="m")


@pytest.mark.asyncio
async def test_list_page_orders_desc_and_paginates(store: NotificationStore) -> None:
    for i in range(5):
        await store.add(level="info", category="c", title=f"t{i}", message=f"m{i}")

    page1, total = await store.list_page(limit=2, offset=0)
    assert total == 5
    assert len(page1) == 2
    assert page1[0].title == "t4"  # 最新
    assert page1[1].title == "t3"

    page2, _ = await store.list_page(limit=2, offset=2)
    assert [n.title for n in page2] == ["t2", "t1"]

    page3, _ = await store.list_page(limit=2, offset=4)
    assert [n.title for n in page3] == ["t0"]


@pytest.mark.asyncio
async def test_unread_flow(store: NotificationStore) -> None:
    a = await store.add(level="info", category="c", title="a", message="")
    b = await store.add(level="info", category="c", title="b", message="")
    c = await store.add(level="info", category="c", title="c", message="")

    assert await store.count_unread() == 3

    # 命中未读: True
    assert await store.mark_read(a) is True
    assert await store.count_unread() == 2

    # 幂等: 已读再标, False
    assert await store.mark_read(a) is False

    # unread_only 过滤
    items, total = await store.list_page(limit=10, offset=0, unread_only=True)
    assert total == 2
    assert {n.title for n in items} == {"b", "c"}

    # mark_all_read 返回受影响条数
    n = await store.mark_all_read()
    assert n == 2
    assert await store.count_unread() == 0


@pytest.mark.asyncio
async def test_delete(store: NotificationStore) -> None:
    nid = await store.add(level="info", category="c", title="t", message="")
    assert await store.delete_by_id(nid) is True
    assert await store.get(nid) is None
    # 二次删除返 False
    assert await store.delete_by_id(nid) is False


@pytest.mark.asyncio
async def test_mark_read_missing_id_returns_false(store: NotificationStore) -> None:
    assert await store.mark_read(9999) is False
