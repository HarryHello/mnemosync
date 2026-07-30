"""空间串行锁测试."""

import asyncio

import pytest
from src.infra.space_lock import SpaceLockManager


@pytest.mark.asyncio
async def test_same_key_serial():
    """同一 key 的锁应串行: 第二个 acquire 等第一个 release."""
    mgr = SpaceLockManager()
    lock = await mgr.acquire("space-A")
    await lock.acquire()
    assert lock.locked()

    # 第二个协程尝试获取同一把锁, 应被阻塞
    acquired = asyncio.Event()

    async def wait_and_acquire():
        l = await mgr.acquire("space-A")
        await l.acquire()
        acquired.set()
        l.release()

    task = asyncio.create_task(wait_and_acquire())
    # 等一小段时间, 确认第二个协程被阻塞
    await asyncio.sleep(0.05)
    assert not acquired.is_set()

    # 释放后第二个应能获取
    lock.release()
    await asyncio.wait_for(acquired.wait(), timeout=1.0)
    await task


@pytest.mark.asyncio
async def test_different_key_parallel():
    """不同 key 的锁应并行: 互不阻塞."""
    mgr = SpaceLockManager()
    lock_a = await mgr.acquire("space-A")
    lock_b = await mgr.acquire("space-B")
    await lock_a.acquire()

    # space-B 的锁不应被阻塞
    await lock_b.acquire()
    assert lock_b.locked()
    lock_b.release()
    lock_a.release()


def test_lock_key_priority():
    """锁键优先级: space_id > source_user > api_key_id > global."""
    mgr = SpaceLockManager()
    assert mgr.lock_key(space_id="s1", source_user="u1", api_key_id="k1") == "s1"
    assert mgr.lock_key(space_id=None, source_user="u1", api_key_id="k1") == "u1"
    assert mgr.lock_key(space_id=None, source_user=None, api_key_id="k1") == "k1"
    assert mgr.lock_key(space_id=None, source_user=None, api_key_id=None) == "global"
