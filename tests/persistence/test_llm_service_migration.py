"""LLM 服务商库升级迁移测试 (v0.4.1 模型注册表).

模拟 v0.4.0 旧库 (无 models 表, role_bindings 带能力列, model_configs 有数据):
- 007/008: role_bindings + model_configs → models 去重回填
- 009/010: role_bindings.model_id 加列 + 回填
- 升级后 resolve_role 能力经 join 正确、绑定不变
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from src.infra.llm_service.models import ModelType
from src.infra.llm_service.store import LLMServiceStore


async def _create_legacy_db(path: Path) -> None:
    """构造 v0.4.0 旧结构库: llm_services + model_configs + role_bindings (无 models)."""
    async with aiosqlite.connect(str(path)) as db:
        await db.execute("""
            CREATE TABLE config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE llm_services (
                id TEXT PRIMARY KEY, base_url TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL,
                api_format TEXT NOT NULL DEFAULT 'openai',
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE model_configs (
                id TEXT PRIMARY KEY, service_id TEXT NOT NULL,
                model TEXT NOT NULL, model_type TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE role_bindings (
                role TEXT NOT NULL, priority INTEGER NOT NULL,
                service_id TEXT NOT NULL, model TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                context_length INTEGER, embedding_dim INTEGER,
                send_dimensions INTEGER NOT NULL DEFAULT 0,
                input_modalities TEXT DEFAULT '["text"]',
                output_modalities TEXT DEFAULT '["text"]',
                PRIMARY KEY (role, priority)
            )
        """)
        await db.execute(
            "INSERT INTO llm_services VALUES ('s1', 'https://x', 'enc1', 'openai', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO llm_services VALUES ('s2', 'https://y', 'enc2', 'openai', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        # 绑定: s1/m1 出现两次 (main + assist), 能力不同 → 合并取 MAX
        await db.execute(
            "INSERT INTO role_bindings VALUES ('main', 0, 's1', 'm1', '2026-08-01T00:00:00+00:00', 131072, NULL, 0, '[\"text\",\"image\"]', '[\"text\"]')"
        )
        await db.execute(
            "INSERT INTO role_bindings VALUES ('assist', 0, 's1', 'm1', '2026-08-01T00:00:00+00:00', 65536, NULL, 0, '[\"text\"]', '[\"text\"]')"
        )
        await db.execute(
            "INSERT INTO role_bindings VALUES ('main', 1, 's2', 'm2', '2026-08-01T00:00:00+00:00', NULL, NULL, 0, '[\"text\"]', '[\"text\"]')"
        )
        # model_configs: 含一个不在绑定里的模型 (应并入 models)
        await db.execute(
            "INSERT INTO model_configs VALUES ('c1', 's2', 'm-extra', 'main', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_upgrade_backfills_registry_and_model_id(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    await _create_legacy_db(db)

    store = LLMServiceStore(str(db))
    # 旧库的 api_key_encrypted 是占位符 — 先写真实 Fernet 密文 (密钥自动入 config 表)
    enc = await store._encrypt("secret-key")
    async with aiosqlite.connect(str(db)) as con:
        await con.execute("UPDATE llm_services SET api_key_encrypted = ?", (enc,))
        await con.commit()
    await store.init_db()  # 触发 CREATE TABLE IF NOT EXISTS + 迁移 007-010

    # 注册表: 绑定去重 (s1:m1 一条) + model_configs 并入
    entries = await store.list_model_registry()
    by_id = {e.id: e for e in entries}
    assert set(by_id) == {"s1:m1", "s2:m2", "s2:m-extra"}

    # s1:m1 能力合并: MAX(input_modalities) 保留 image
    m1 = by_id["s1:m1"]
    assert "image" in m1.input_modalities
    assert m1.context_length == 131072
    assert m1.concurrency == 20  # 默认
    assert m1.enabled is True

    # 绑定: model_id 已回填
    bindings = await store.list_role_bindings()
    by_key = {(b.role.value, b.priority): b for b in bindings}
    assert by_key[("main", 0)].model_id == "s1:m1"
    assert by_key[("assist", 0)].model_id == "s1:m1"
    assert by_key[("main", 1)].model_id == "s2:m2"
    # 能力经 join 来自注册表
    assert by_key[("main", 0)].context_length == 131072
    assert "image" in by_key[("main", 0)].input_modalities

    # resolve_role: 完整链路可用
    resolved = await store.resolve_role(ModelType.MAIN)
    assert [(r.service_id, r.model) for r in resolved] == [("s1", "m1"), ("s2", "m2")]
    assert resolved[0].context_length == 131072
    assert "image" in resolved[0].input_modalities


@pytest.mark.asyncio
async def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    """二次 init_db 不重复回填/不破坏数据."""
    db = tmp_path / "legacy2.db"
    await _create_legacy_db(db)
    store = LLMServiceStore(str(db))
    await store.init_db()
    await store.init_db()  # 再次

    entries = await store.list_model_registry()
    assert len(entries) == 3  # 无重复
    bindings = await store.list_role_bindings()
    assert len(bindings) == 3
