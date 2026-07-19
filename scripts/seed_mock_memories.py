"""用 TOML 数据文件把一批"模拟记忆"塞进 SQLite, 用于面板/端点/UI 调试.

用法:
    uv run python scripts/seed_mock_memories.py
    uv run python scripts/seed_mock_memories.py --toml scripts/mock_memories.toml
    uv run python scripts/seed_mock_memories.py --wipe    # 先清空再写入
    uv run python scripts/seed_mock_memories.py --db /path/to/memory.db

只写 SQLite (memory_entries + relationships), 不动 Chroma 向量库. 因为向量必须由
真实嵌入模型生成且要过 lock_embedding 校验, 随机造假向量语义无意义.

想让这些记忆能被语义检索命中, 灌完后跑一次:
    mnemosync memory reindex          # CLI
    # 或面板 → 记忆维护 → 重建索引
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

# 让脚本能在项目根目录之外被直接调用
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_settings  # noqa: E402
from src.core.memory.models import (  # noqa: E402
    MemoryEntry,
    MemoryType,
    Relationship,
)
from src.persistence.memory_store import SqliteMemoryStore  # noqa: E402

DEFAULT_TOML = PROJECT_ROOT / "scripts" / "mock_memories.toml"
DEFAULT_SOURCE_USER = "default"
DEFAULT_PERSONA_ID = "default"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed mock memories into SQLite.")
    p.add_argument("--toml", type=Path, default=DEFAULT_TOML, help="记忆数据 TOML 文件路径")
    p.add_argument("--db", type=Path, default=None, help="覆盖 memory.db 路径 (默认读 config.local.toml)")
    p.add_argument("--wipe", action="store_true", help="写入前先清空 memory_entries / relationships")
    p.add_argument(
        "--source-user",
        default=DEFAULT_SOURCE_USER,
        help=f"source_user 值 (默认 {DEFAULT_SOURCE_USER}, 单人格单用户设计不建议改)",
    )
    return p.parse_args()


def build_entries(data: dict, source_user: str) -> list[MemoryEntry]:
    raw_items = data.get("memory", [])
    if not raw_items:
        raise ValueError(f"TOML 里没有找到任何 [[memory]] 块")

    entries: list[MemoryEntry] = []
    for i, item in enumerate(raw_items):
        content = item.get("content")
        if not content:
            raise ValueError(f"[[memory]] #{i} 缺少 content")

        type_str = item.get("type", "normal").lower()
        mem_type = MemoryType.PERMANENT if type_str == "permanent" else MemoryType.NORMAL

        entry = MemoryEntry.create(
            content=content,
            role=item.get("role", "user"),
            source_user=source_user,
            memory_type=mem_type,
            importance=float(item.get("importance", 0.5)),
            decay_rate=float(
                item.get("decay_rate", 0.0 if mem_type == MemoryType.PERMANENT else 0.3)
            ),
        )
        entry.emotional_tags = list(item.get("tags", []))

        # 用相对天数回填 created_at, 让面板有时间分布
        age_days = int(item.get("age_days", 0))
        if age_days > 0:
            entry.created_at = entry.created_at - timedelta(days=age_days)

        # 永久记忆回填 last_accessed 让面板有数据显示
        if mem_type == MemoryType.PERMANENT:
            entry.last_accessed = entry.created_at + timedelta(days=1)
            entry.access_count = max(3, entry.access_count or 0)

        entries.append(entry)
    return entries


def build_relationship(persona_id: str, user_id: str) -> Relationship:
    rel = Relationship.create(persona_id=persona_id, user_id=user_id)
    # 模拟同住兄妹已有较高信任但亲密度仍在慢慢升温的状态
    rel.apply_delta(intimacy_delta=0.35, trust_delta=0.65, new_type="acquaintance")
    rel.apply_delta(intimacy_delta=0.1, trust_delta=0.1)
    rel.interaction_count = 42
    rel.notes = "对方是同住的哥哥, 大学生. 绫音嘴上冷淡, 实际会记住哥哥说的每一件小事; 面对面几乎不说话, 主要通过消息和纸条交流."
    return rel


async def run(args: argparse.Namespace) -> None:
    settings = load_settings()
    db_path = str(args.db) if args.db else str(settings.storage.memory_db_abs)

    toml_path = args.toml if args.toml.is_absolute() else PROJECT_ROOT / args.toml
    if not toml_path.exists():
        print(f"✗ TOML 文件不存在: {toml_path}", file=sys.stderr)
        sys.exit(1)

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    entries = build_entries(data, args.source_user)
    relationship = build_relationship(DEFAULT_PERSONA_ID, args.source_user)

    print(f"→ 目标数据库: {db_path}")
    print(f"→ TOML 数据源: {toml_path}")
    print(f"→ 将写入 {len(entries)} 条记忆 + 1 条 relationship")

    store = SqliteMemoryStore(db_path)
    await store.init_db()

    if args.wipe:
        deleted_mem = await store.delete_all_memories()
        deleted_rel = await store.delete_all_relationships()
        print(f"  ⚠ --wipe: 已清空 {deleted_mem} 条记忆 + {deleted_rel} 条关系")

    permanent = sum(1 for e in entries if e.memory_type == MemoryType.PERMANENT)
    normal = len(entries) - permanent
    for entry in entries:
        await store.save(entry)
    await store.save_relationship(relationship)

    print(f"✓ 写入完成: permanent={permanent}, normal={normal}")
    print(f"  relationship: type={relationship.type}, "
          f"intimacy={relationship.intimacy_score:.2f}, "
          f"trust={relationship.trust_level:.2f}, "
          f"interactions={relationship.interaction_count}")
    print()
    print("提示: 语义检索需要向量库, 请再跑一次:")
    print("  mnemosync memory reindex    # 或面板 → 记忆维护 → 重建索引")


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
