"""API Key source 列 + panel-debug 生命周期测试."""

from __future__ import annotations

import pytest

from src.persistence.api_key_store import (
    API_KEY_SOURCE_PANEL_DEBUG,
    API_KEY_SOURCE_USER,
    ApiKey,
    SqliteApiKeyStore,
)


@pytest.mark.asyncio
async def test_source_defaults_to_user(tmp_path):
    store = SqliteApiKeyStore(str(tmp_path / "k.db"))
    await store.connect()
    try:
        ak = ApiKey.generate(note="手动创建")
        assert ak.source == API_KEY_SOURCE_USER
        await store.save(ak)
        got = await store.get_by_id(ak.id)
        assert got is not None
        assert got.source == API_KEY_SOURCE_USER
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_all_filters_by_source(tmp_path):
    store = SqliteApiKeyStore(str(tmp_path / "k.db"))
    await store.connect()
    try:
        user_a = ApiKey.generate(note="user-a", source=API_KEY_SOURCE_USER)
        user_b = ApiKey.generate(note="user-b", source=API_KEY_SOURCE_USER)
        dbg = ApiKey.generate(note="dbg", source=API_KEY_SOURCE_PANEL_DEBUG)
        for k in (user_a, user_b, dbg):
            await store.save(k)

        all_items = await store.list_all()
        assert len(all_items) == 3

        user_only = await store.list_all(source=API_KEY_SOURCE_USER)
        assert {k.note for k in user_only} == {"user-a", "user-b"}

        dbg_only = await store.list_all(source=API_KEY_SOURCE_PANEL_DEBUG)
        assert len(dbg_only) == 1
        assert dbg_only[0].note == "dbg"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_by_source_only_removes_matching(tmp_path):
    store = SqliteApiKeyStore(str(tmp_path / "k.db"))
    await store.connect()
    try:
        user = ApiKey.generate(note="u", source=API_KEY_SOURCE_USER)
        d1 = ApiKey.generate(note="d1", source=API_KEY_SOURCE_PANEL_DEBUG)
        d2 = ApiKey.generate(note="d2", source=API_KEY_SOURCE_PANEL_DEBUG)
        for k in (user, d1, d2):
            await store.save(k)

        deleted = await store.delete_by_source(API_KEY_SOURCE_PANEL_DEBUG)
        assert deleted == 2

        remaining = await store.list_all()
        assert len(remaining) == 1
        assert remaining[0].note == "u"
        assert remaining[0].source == API_KEY_SOURCE_USER
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_key_full_decrypt_survives_roundtrip(tmp_path):
    """panel-debug key 需要能被复用 (session-key 端点靠 key_full 判断)."""
    store = SqliteApiKeyStore(str(tmp_path / "k.db"))
    await store.connect()
    try:
        ak = ApiKey.generate(note="debug", source=API_KEY_SOURCE_PANEL_DEBUG)
        original = ak.key_full
        assert original is not None
        await store.save(ak)

        items = await store.list_all(source=API_KEY_SOURCE_PANEL_DEBUG)
        assert len(items) == 1
        assert items[0].key_full == original
    finally:
        await store.close()
