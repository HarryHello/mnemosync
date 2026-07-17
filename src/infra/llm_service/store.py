"""LLM 服务商存储（SQLite + Fernet 加密）.

迁移自旧 storage/llm_service_store.py, 保持 Fernet 加密机制不变.
"""

from __future__ import annotations

import aiosqlite
import base64
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .models import (
    LLMServiceProvider,
    ModelConfiguration,
    ModelType,
    ResolvedCandidate,
    RoleBinding,
)


class LLMServiceStore:
    """LLM 服务商 + 模型配置存储.

    Fernet 对称加密 API Key, 密钥自动生成并存于同库 config 表.
    """

    _ENCRYPTION_KEY_ID = "__encryption_key__"

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._fernet: Optional[Fernet] = None

    # ============ 加密 ============

    async def _load_or_create_key(self) -> bytes:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM config WHERE key = ?", (self._ENCRYPTION_KEY_ID,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return base64.urlsafe_b64decode(row[0])
            key = Fernet.generate_key()
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (self._ENCRYPTION_KEY_ID, base64.urlsafe_b64encode(key).decode()),
            )
            await db.commit()
            return key

    async def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            key = await self._load_or_create_key()
            self._fernet = Fernet(key)
        return self._fernet

    async def _encrypt(self, plaintext: str) -> str:
        f = await self._get_fernet()
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    async def _decrypt(self, ciphertext: str) -> str:
        f = await self._get_fernet()
        try:
            return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            raise ValueError("API Key 解密失败（密钥损坏或数据被篡改）")

    # ============ 初始化 ============

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS llm_services (
                    id TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_service_id ON llm_services(id)")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS model_configs (
                    id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_model_service ON model_configs(service_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_model_type ON model_configs(model_type)")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS role_bindings (
                    role TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    service_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    context_length INTEGER,
                    embedding_dim INTEGER,
                    PRIMARY KEY (role, priority),
                    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_role_priority ON role_bindings(role, priority)"
            )
            await self._ensure_role_binding_columns(db)
            await db.commit()

    async def _ensure_role_binding_columns(self, db) -> None:
        """v0.2.4 迁移: 为 role_bindings 表补 context_length / embedding_dim 两列.

        SQLite ALTER TABLE ADD COLUMN 是 O(1) 元数据更新, 幂等.
        """
        async with db.execute("PRAGMA table_info(role_bindings)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        for col in ("context_length", "embedding_dim"):
            if col not in cols:
                await db.execute(
                    f"ALTER TABLE role_bindings ADD COLUMN {col} INTEGER"
                )

    # ============ 服务商 CRUD ============

    async def save_service(self, service: LLMServiceProvider) -> LLMServiceProvider:
        encrypted = await self._encrypt(service.api_key)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?", (service.id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] > 0:
                    raise ValueError(f"服务 '{service.id}' 已存在")
            await db.execute(
                "INSERT INTO llm_services (id, base_url, api_key_encrypted, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (service.id, service.base_url, encrypted,
                 service.created_at.isoformat(), service.updated_at.isoformat()),
            )
            await db.commit()
        return service

    async def get_service(self, service_id: str) -> Optional[LLMServiceProvider]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, created_at, updated_at FROM llm_services WHERE id = ?",
                (service_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return LLMServiceProvider(
                    id=row[0], base_url=row[1],
                    api_key=await self._decrypt(row[2]),
                    created_at=self._parse_dt(row[3]),
                    updated_at=self._parse_dt(row[4]),
                )

    async def list_services(self) -> list[LLMServiceProvider]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, created_at, updated_at FROM llm_services ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMServiceProvider(
                        id=r[0], base_url=r[1], api_key=await self._decrypt(r[2]),
                        created_at=self._parse_dt(r[3]), updated_at=self._parse_dt(r[4]),
                    )
                    for r in rows
                ]

    async def delete_service(self, service_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            cur = await db.execute("DELETE FROM llm_services WHERE id = ?", (service_id,))
            await db.execute("DELETE FROM model_configs WHERE service_id = ?", (service_id,))
            # role_bindings 依赖 FK ON DELETE CASCADE 自动清理
            await db.commit()
            return cur.rowcount > 0

    # ============ 模型配置 CRUD ============

    async def save_model(self, config: ModelConfiguration) -> ModelConfiguration:
        async with aiosqlite.connect(self.db_path) as db:
            # 检查关联服务存在
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?", (config.service_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] == 0:
                    raise ValueError(f"服务 '{config.service_id}' 不存在")

            # 同 service_id + model_type 唯一（覆盖更新）
            async with db.execute(
                "SELECT id FROM model_configs WHERE service_id = ? AND model_type = ?",
                (config.service_id, config.model_type.value),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                await db.execute(
                    "UPDATE model_configs SET model = ?, updated_at = ? WHERE service_id = ? AND model_type = ?",
                    (config.model, config.updated_at.isoformat(),
                     config.service_id, config.model_type.value),
                )
            else:
                await db.execute(
                    "INSERT INTO model_configs (id, service_id, model, model_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (config.id, config.service_id, config.model, config.model_type.value,
                     config.created_at.isoformat(), config.updated_at.isoformat()),
                )
            await db.commit()
        return config

    async def get_model(
        self, service_id: str, model_type: ModelType
    ) -> Optional[ModelConfiguration]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, service_id, model, model_type, created_at, updated_at FROM model_configs WHERE service_id = ? AND model_type = ?",
                (service_id, model_type.value),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return ModelConfiguration(
                    id=row[0], service_id=row[1], model=row[2],
                    model_type=ModelType(row[3]),
                    created_at=self._parse_dt(row[4]), updated_at=self._parse_dt(row[5]),
                )

    async def list_models(self, service_id: str) -> list[ModelConfiguration]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, service_id, model, model_type, created_at, updated_at FROM model_configs WHERE service_id = ? ORDER BY model_type",
                (service_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    ModelConfiguration(
                        id=r[0], service_id=r[1], model=r[2], model_type=ModelType(r[3]),
                        created_at=self._parse_dt(r[4]), updated_at=self._parse_dt(r[5]),
                    )
                    for r in rows
                ]

    @staticmethod
    def _parse_dt(v: Optional[str]) -> datetime:
        if v is None:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(v)

    # ============ 角色绑定 (role_bindings) ============

    async def list_role_bindings(self, role: Optional[ModelType] = None) -> list[RoleBinding]:
        """列出角色绑定. role 为 None 时返回所有角色的绑定, 已按 (role, priority) 排序."""
        async with aiosqlite.connect(self.db_path) as db:
            if role is None:
                query = (
                    "SELECT role, priority, service_id, model, created_at, "
                    "context_length, embedding_dim "
                    "FROM role_bindings ORDER BY role, priority"
                )
                params: tuple = ()
            else:
                query = (
                    "SELECT role, priority, service_id, model, created_at, "
                    "context_length, embedding_dim "
                    "FROM role_bindings WHERE role = ? ORDER BY priority"
                )
                params = (role.value,)
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [
            RoleBinding(
                role=ModelType(r[0]),
                priority=r[1],
                service_id=r[2],
                model=r[3],
                created_at=self._parse_dt(r[4]),
                context_length=r[5],
                embedding_dim=r[6],
            )
            for r in rows
        ]

    async def add_role_binding(
        self,
        role: ModelType,
        service_id: str,
        model: str,
        priority: Optional[int] = None,
        context_length: Optional[int] = None,
        embedding_dim: Optional[int] = None,
    ) -> RoleBinding:
        """追加一条角色绑定. priority 省略时排到列表末尾.

        指定 priority 时若已被占用, 后续所有条目 priority += 1 让位.
        EMBEDDING 角色只允许一条绑定 (换模型会破坏向量语义空间, 走 reindex 流程).
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?", (service_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] == 0:
                    raise ValueError(f"服务 '{service_id}' 不存在")

            if role == ModelType.EMBEDDING:
                async with db.execute(
                    "SELECT COUNT(*) FROM role_bindings WHERE role = ?",
                    (role.value,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0] > 0:
                        raise ValueError(
                            "嵌入模型只允许一条绑定, 请先删除现有绑定 (换模型需走 reindex)"
                        )

            async with db.execute(
                "SELECT COALESCE(MAX(priority), -1) FROM role_bindings WHERE role = ?",
                (role.value,),
            ) as cursor:
                row = await cursor.fetchone()
                max_priority = row[0] if row else -1

            if priority is None:
                priority = max_priority + 1
            else:
                if priority < 0:
                    raise ValueError("priority 必须 >= 0")
                if priority <= max_priority:
                    # 让位: 先把 [priority, max_priority] 平移到负数区避免 UNIQUE 冲突,
                    # 再一次性拉回 (+2, 净效果 +1)
                    await db.execute(
                        "UPDATE role_bindings SET priority = -priority - 1 "
                        "WHERE role = ? AND priority >= ?",
                        (role.value, priority),
                    )
                    await db.execute(
                        "UPDATE role_bindings SET priority = -priority "
                        "WHERE role = ? AND priority < 0",
                        (role.value,),
                    )

            now = datetime.now(timezone.utc)
            await db.execute(
                "INSERT INTO role_bindings "
                "(role, priority, service_id, model, created_at, context_length, embedding_dim) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    role.value,
                    priority,
                    service_id,
                    model,
                    now.isoformat(),
                    context_length,
                    embedding_dim,
                ),
            )
            await db.commit()

        return RoleBinding(
            role=role,
            priority=priority,
            service_id=service_id,
            model=model,
            created_at=now,
            context_length=context_length,
            embedding_dim=embedding_dim,
        )

    async def delete_role_binding(self, role: ModelType, priority: int) -> bool:
        """删除某条绑定, 并将其后所有条目的 priority 前移一位, 保持连续."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM role_bindings WHERE role = ? AND priority = ?",
                (role.value, priority),
            )
            if cur.rowcount == 0:
                await db.commit()
                return False
            await db.execute(
                "UPDATE role_bindings SET priority = priority - 1 "
                "WHERE role = ? AND priority > ?",
                (role.value, priority),
            )
            await db.commit()
            return True

    async def reorder_role_bindings(
        self, role: ModelType, order: list[tuple[str, str]]
    ) -> list[RoleBinding]:
        """重排某角色的所有绑定. order 是按新优先级排序的 (service_id, model) 列表.

        要求 order 必须包含且仅包含现有的全部绑定 (service_id, model 对), 否则 ValueError.
        整体在一个事务中原子完成.
        EMBEDDING 角色单绑定, reorder 无意义, 直接拒绝.
        """
        if role == ModelType.EMBEDDING:
            raise ValueError("嵌入角色只允许一条绑定, reorder 无意义")
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT service_id, model FROM role_bindings WHERE role = ?",
                (role.value,),
            ) as cursor:
                current = {(r[0], r[1]) for r in await cursor.fetchall()}
            if set(order) != current:
                raise ValueError(
                    f"reorder 参数与现有绑定不匹配 (missing={current - set(order)}, "
                    f"extra={set(order) - current})"
                )
            if len(order) != len(set(order)):
                raise ValueError("reorder 参数含重复项")

            # 两步走: 先把 priority 全部改成负数偏移, 再改回目标值, 避免 UNIQUE 冲突
            await db.execute(
                "UPDATE role_bindings SET priority = -priority - 1 WHERE role = ?",
                (role.value,),
            )
            for new_priority, (service_id, model) in enumerate(order):
                await db.execute(
                    "UPDATE role_bindings SET priority = ? "
                    "WHERE role = ? AND service_id = ? AND model = ?",
                    (new_priority, role.value, service_id, model),
                )
            await db.commit()

        return await self.list_role_bindings(role)

    async def resolve_role(self, role: ModelType) -> list[ResolvedCandidate]:
        """给定角色, 返回按优先级排序的候选列表, 已解密 api_key."""
        bindings = await self.list_role_bindings(role)
        if not bindings:
            return []
        resolved: list[ResolvedCandidate] = []
        async with aiosqlite.connect(self.db_path) as db:
            for b in bindings:
                async with db.execute(
                    "SELECT base_url, api_key_encrypted FROM llm_services WHERE id = ?",
                    (b.service_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if not row:
                    # 服务被删除但绑定残留 (FK 应该已 cascade, 兜底忽略)
                    continue
                base_url, encrypted = row[0], row[1]
                api_key = await self._decrypt(encrypted)
                resolved.append(
                    ResolvedCandidate(
                        role=b.role,
                        priority=b.priority,
                        service_id=b.service_id,
                        base_url=base_url,
                        api_key=api_key,
                        model=b.model,
                        context_length=b.context_length,
                        embedding_dim=b.embedding_dim,
                    )
                )
        return resolved
