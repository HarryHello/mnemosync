"""PromptCacheStore 单元测试."""

from __future__ import annotations

import pytest

from src.persistence.prompt_cache_store import (
    MAX_CACHE_ENTRIES,
    PromptCacheEntry,
    PromptCacheStore,
)


@pytest.fixture
async def store(tmp_path):
    s = PromptCacheStore(str(tmp_path / "prompt_cache.db"))
    await s.init_db()
    return s


def _entry(frontend="cherry", h="abc123", title="Memory", text="# Memory\n内容", clean="保留"):
    return PromptCacheEntry(
        frontend=frontend, module_hash=h, module_title=title,
        module_text=text, clean_prompt=clean,
    )


async def test_save_and_get(store: PromptCacheStore) -> None:
    await store.save(_entry())
    entry = await store.get("cherry", "abc123")
    assert entry is not None
    assert entry.clean_prompt == "保留"
    assert entry.module_title == "Memory"


async def test_get_missing(store: PromptCacheStore) -> None:
    assert await store.get("cherry", "nope") is None


async def test_update_and_delete(store: PromptCacheStore) -> None:
    await store.save(_entry())
    assert await store.update_clean_prompt("cherry", "abc123", "改过的") is True
    got = await store.get("cherry", "abc123")
    assert got.clean_prompt == "改过的"
    assert await store.delete("cherry", "abc123") is True
    assert await store.delete("cherry", "abc123") is False  # 已删
    assert await store.get("cherry", "abc123") is None


async def test_frontend_isolation(store: PromptCacheStore) -> None:
    """缓存按前台隔离: 不同 frontend 不串."""
    await store.save(_entry())
    assert await store.get("other", "abc123") is None
    await store.save(_entry(frontend="other"))
    assert await store.get("other", "abc123") is not None


async def test_list_and_clear(store: PromptCacheStore) -> None:
    await store.save(_entry(frontend="a", h="h1"))
    await store.save(_entry(frontend="b", h="h2"))
    items, total = await store.list_cache(limit=10, offset=0)
    assert total == 2
    assert len(items) == 2
    items_a, total_a = await store.list_cache(frontend="a")
    assert total_a == 1
    assert items_a[0].frontend == "a"
    assert await store.clear() == 2


async def test_skip_settings(store: PromptCacheStore) -> None:
    await store.set_skip("cherry", "Memory", True)
    assert await store.skip_modules("cherry") == {"Memory"}
    # 通配: * 命中所有前台
    await store.set_skip("*", "Tools", True)
    assert await store.skip_modules("cherry") == {"Memory", "Tools"}
    assert await store.skip_modules("other") == {"Tools"}
    # 取消
    await store.set_skip("cherry", "Memory", False)
    assert await store.skip_modules("cherry") == {"Tools"}
    settings = await store.list_settings()
    assert len(settings) == 1
    assert settings[0].frontend == "*"


async def test_trim_enforces_limit(store: PromptCacheStore, monkeypatch) -> None:
    monkeypatch.setattr("src.persistence.prompt_cache_store.MAX_CACHE_ENTRIES", 5)
    for i in range(8):
        await store.save(_entry(h=f"h{i}"))
    items, total = await store.list_cache()
    assert total == 5  # 裁剪到上限