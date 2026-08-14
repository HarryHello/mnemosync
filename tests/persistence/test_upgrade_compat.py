"""v0.4.1 升级兼容测试: 旧库 (v0.3.5/v0.4.0b1) → 好感度迁移.

模拟旧版 relationships 表 (intimacy_score/trust_level) 与 personas 表,
验证 SqliteRelationshipStore/SqlitePersonaStore 初始化时:
- relationships: 加 favor 列并回填 favor = MAX(intimacy, trust) (零缩放)
- personas: 加 mood 四列 (默认中性)
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from src.persistence.persona_store import SqlitePersonaStore
from src.persistence.relationship_store import SqliteRelationshipStore

_LEGACY_RELATIONSHIPS_DDL = """
CREATE TABLE relationships (
    persona_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'stranger',
    intimacy_score REAL NOT NULL DEFAULT 0.0,
    trust_level REAL NOT NULL DEFAULT 0.0,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_active TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (persona_id, user_id)
)
"""

_LEGACY_PERSONAS_DDL = """
CREATE TABLE personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
"""


async def _make_legacy_db(path: Path) -> None:
    """建一个 v0.4.0b1 风格的旧库 (仅所需表)."""
    async with aiosqlite.connect(path) as db:
        await db.execute(_LEGACY_RELATIONSHIPS_DDL)
        await db.execute(_LEGACY_PERSONAS_DDL)
        # 存量关系: 高亲密高信任 / 高亲密低信任 / 全零
        await db.executemany(
            "INSERT INTO relationships "
            "(persona_id, user_id, type, intimacy_score, trust_level, interaction_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("default", "alice", "friend", 0.8, 0.6, 10),
                ("default", "bob", "stranger", 0.3, 0.9, 5),
                ("default", "carol", "stranger", 0.0, 0.0, 1),
            ],
        )
        await db.execute(
            "INSERT INTO personas (id, name, is_active, created_at, updated_at) "
            "VALUES ('default', '默认人格', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        await db.commit()


async def test_relationship_favor_backfill_from_legacy(tmp_path: Path) -> None:
    """旧库初始化 → favor 回填 = MAX(intimacy, trust), 旧列保留."""
    db_path = tmp_path / "legacy.db"
    await _make_legacy_db(db_path)

    store = SqliteRelationshipStore(str(db_path))
    await store.init_db()

    alice = await store.get_relationship("default", "alice")
    assert alice is not None
    assert alice.favor == 0.8  # MAX(0.8, 0.6)
    assert alice.interaction_count == 10
    # 旧列保留 (兼容读取)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT intimacy_score, trust_level FROM relationships WHERE user_id = 'alice'"
        ) as cur:
            row = await cur.fetchone()
        assert row == (0.8, 0.6)

    bob = await store.get_relationship("default", "bob")
    assert bob is not None
    assert bob.favor == 0.9  # MAX(0.3, 0.9)

    carol = await store.get_relationship("default", "carol")
    assert carol is not None
    assert carol.favor == 0.0  # 全零保持零


async def test_persona_mood_columns_added_to_legacy(tmp_path: Path) -> None:
    """旧库初始化 → personas 表加 mood 四列, 默认中性."""
    db_path = tmp_path / "legacy.db"
    await _make_legacy_db(db_path)

    store = SqlitePersonaStore(str(db_path))
    await store.init_db()

    mood = await store.get_mood("default")
    assert mood["valence"] == 0.0
    assert mood["cause"] is None

    # 写入后读回
    await store.set_mood("default", valence=-0.4, cause="被羞辱", interaction_id="i-1")
    mood2 = await store.get_mood("default")
    assert mood2["valence"] == -0.4
    assert mood2["cause"] == "被羞辱"
    assert mood2["last_interaction_id"] == "i-1"


async def test_legacy_migration_is_idempotent(tmp_path: Path) -> None:
    """重复初始化不破坏数据 (迁移幂等)."""
    db_path = tmp_path / "legacy.db"
    await _make_legacy_db(db_path)

    store = SqliteRelationshipStore(str(db_path))
    await store.init_db()
    await store.init_db()  # 第二次: 迁移应静默跳过

    alice = await store.get_relationship("default", "alice")
    assert alice is not None and alice.favor == 0.8
    assert await store.count_relationships() == 3
