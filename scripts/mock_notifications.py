"""临时脚本: 往 data/notifications.db 灌一批 mock 通知, 用于手动预览通知中心 UI.

用法:
    uv run python scripts/mock_notifications.py            # 默认追加 8 条
    uv run python scripts/mock_notifications.py --clear    # 先清空再灌
    uv run python scripts/mock_notifications.py --count 20 # 指定数量

不要在生产使用. 脚本本身不属于产品代码, 后续可删。
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone

import aiosqlite

from src.persistence.notification_store import NotificationStore

DB_PATH = "data/notifications.db"


SAMPLES: list[dict] = [
    {
        "level": "error",
        "category": "memory_write_failed",
        "title": "记忆入库失败",
        "message": "dashscope embedding request failed: 401 Unauthorized",
        "meta": {
            "stage": "embed",
            "content_preview": "用户提到最近在准备下周的季度评审, 想让我帮忙梳理今年的重点交付……",
            "error_type": "UpstreamError",
            "upstream_status": 401,
        },
    },
    {
        "level": "warning",
        "category": "memory_write_failed",
        "title": "记忆入库失败",
        "message": "embedding dimension mismatch: expected 1024, got 768",
        "meta": {
            "stage": "vector_lock",
            "content_preview": "换了个嵌入模型 bge-small-zh, 之前锁的是 text-embedding-v3 (1024 维)",
            "error_type": "EmbeddingDimensionMismatch",
        },
    },
    {
        "level": "warning",
        "category": "memory_write_failed",
        "title": "记忆入库失败",
        "message": "sqlite disk I/O error while inserting memory row",
        "meta": {
            "stage": "persist",
            "content_preview": "凌晨定时任务处理短期记忆时候撞到 disk full 了",
            "error_type": "OperationalError",
        },
    },
    {
        "level": "info",
        "category": "reindex_completed",
        "title": "向量库重建完成",
        "message": "Reindex 已完成, 共处理 1287 条记忆, 清理 43 条低优先级",
        "meta": {
            "processed": 1287,
            "pruned": 43,
            "duration_seconds": 96,
        },
    },
    {
        "level": "info",
        "category": "persona_reset",
        "title": "人格已重置为初装",
        "message": "所有关系状态、短期记忆缓存已清空",
        "meta": {"backup_id": "persona_backup_20260721_083012"},
    },
    {
        "level": "error",
        "category": "upstream_outage",
        "title": "上游长时间不可达",
        "message": "dashscope 主模型连续 5 次超时, 已切换到 fallback (deepseek)",
        "meta": {
            "primary": "dashscope/qwen-max",
            "fallback": "deepseek/deepseek-chat",
            "failure_count": 5,
            "window_seconds": 300,
        },
    },
    {
        "level": "warning",
        "category": "api_key_near_expiry",
        "title": "API Key 即将过期",
        "message": "有 1 个面板 API Key 将在 3 天后过期, 请前往 API Key 页面续期",
        "meta": {
            "key_prefix": "sk-mnem-...c4f2",
            "expires_at": "2026-07-24T00:00:00Z",
        },
    },
    {
        "level": "info",
        "category": "system_start",
        "title": "服务已启动",
        "message": "Mnemosync 已在 http://0.0.0.0:16125 启动",
        "meta": {"pid": 30412, "version": "0.2.13"},
    },
]


async def clear(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("DELETE FROM notifications")
        await db.commit()
        return cur.rowcount or 0


async def seed(store: NotificationStore, count: int, mark_some_read: bool) -> list[int]:
    """按时间倒推随机分布地插入 count 条; 前 1/3 自动置为已读, 便于观察灰化样式."""
    ids: list[int] = []
    now = datetime.now(timezone.utc)
    samples = [dict(s) for s in SAMPLES]
    random.shuffle(samples)
    for i in range(count):
        s = samples[i % len(samples)]
        # 用直接 INSERT 让 created_at 在过去 12h 内分布
        offset = timedelta(minutes=random.randint(1, 12 * 60))
        stamp = (now - offset).isoformat()
        meta_json = None
        if s.get("meta"):
            import json as _json
            meta_json = _json.dumps(s["meta"], ensure_ascii=False)
        async with aiosqlite.connect(store.db_path) as db:
            cur = await db.execute(
                "INSERT INTO notifications "
                "(created_at, level, category, title, message, meta_json, read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (stamp, s["level"], s["category"], s["title"], s["message"], meta_json),
            )
            await db.commit()
            ids.append(cur.lastrowid or 0)

    if mark_some_read and ids:
        cutoff = len(ids) // 3
        if cutoff:
            targets = ids[:cutoff]
            read_stamp = (now - timedelta(minutes=5)).isoformat()
            async with aiosqlite.connect(store.db_path) as db:
                await db.executemany(
                    "UPDATE notifications SET read_at = ? WHERE id = ?",
                    [(read_stamp, i) for i in targets],
                )
                await db.commit()
    return ids


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="先清空表再灌")
    parser.add_argument("--count", type=int, default=8, help="要插入的条数")
    parser.add_argument("--no-read", action="store_true", help="不要预标记任何为已读")
    args = parser.parse_args()

    store = NotificationStore(DB_PATH)
    await store.init_db()

    if args.clear:
        n = await clear(DB_PATH)
        print(f"🧹 清空原有通知: {n} 条")

    ids = await seed(store, args.count, mark_some_read=not args.no_read)
    print(f"✅ 已写入 {len(ids)} 条 mock 通知; 前 1/3 已标记为已读, 用来看灰化效果")
    print("👉 打开面板 → 头像下拉 → 显示通知 (或点刷新)")


if __name__ == "__main__":
    asyncio.run(main())
