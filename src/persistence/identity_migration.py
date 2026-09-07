"""身份迁移: 旧的手动 api_key_bound 策略升级为内建语义.

背景:
- v0.4.1 起 api_key_bound (Key 即身份) 成为内建默认策略, 用 `strategy_id="api_key_bound"`
  标记, external_key = Key id, display_name = Key 备注.
- 早期版本 api_key_bound 是手动创建的策略 (strategy_id = UUID, config 驱动
  external_key/display_name), 且所有 Key 共享同一 external_key (默认 "api-key-bound"),
  导致不同 Key 被识别为同一人.

迁移动作 (幂等, 启动时执行):
1. 找到所有 strategy_type = "api_key_bound" 的手动策略
2. 把绑定这些策略的 API Key 的 strategy_id 改为内建标记 "api_key_bound"
3. 删除这些手动策略记录
"""

from __future__ import annotations

import logging

from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)

BUILTIN_API_KEY_BOUND = "api_key_bound"


async def migrate_legacy_api_key_bound(
    identity_store: SqliteIdentityStore,
    api_key_store: SqliteApiKeyStore,
) -> int:
    """把旧的手动 api_key_bound 策略迁移为内建语义.

    Returns:
        迁移的 API Key 数量.
    """
    strategies, _ = await identity_store.list_strategies(limit=500)
    legacy = [s for s in strategies if s.strategy_type == "api_key_bound"]
    if not legacy:
        return 0

    keys = await api_key_store.list_all()
    migrated = 0
    for s in legacy:
        for k in keys:
            if k.strategy_id == s.id:
                ok = await api_key_store.update_strategy_id(k.id, BUILTIN_API_KEY_BOUND)
                if ok:
                    migrated += 1
        await identity_store.delete_strategy(s.id)
        logger.info(
            "迁移旧 api_key_bound 策略 %s (%s): %d 个 Key 改为内建标记",
            s.id, s.name, sum(1 for k in keys if k.strategy_id == s.id),
        )

    if migrated:
        logger.info("✅ 旧 api_key_bound 策略迁移完成: %d 个 Key 升级为 Key 即身份", migrated)
    return migrated
