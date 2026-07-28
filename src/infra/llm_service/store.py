"""LLM 服务商存储（SQLite + Fernet 加密）.

迁移自旧 storage/llm_service_store.py, 保持 Fernet 加密机制不变.
"""

from __future__ import annotations

import aiosqlite
from datetime import datetime, UTC
from typing import Optional

from src.infra.crypto import FernetEncryptor

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
        self._encryptor = FernetEncryptor(
            db_path=self.db_path,
            config_table="config",
            key_id=self._ENCRYPTION_KEY_ID,
            raise_on_decrypt_failure=True,
        )

    # ============ 加密 ============

    async def _encrypt(self, plaintext: str) -> str:
        return await self._encryptor.encrypt(plaintext)

    async def _decrypt(self, ciphertext: str) -> str:
        result = await self._encryptor.decrypt(ciphertext)
        if result is None:
            raise ValueError("API Key 解密失败（密钥损坏或数据被篡改）")
        return result

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
                    send_dimensions INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (role, priority),
                    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_role_priority ON role_bindings(role, priority)"
            )
            # 命名迁移: 幂等补列 (旧库升级用; 新库 CREATE TABLE 已包含全部列, 自动跳过)
            from src.persistence.migrations import MigrationRunner, add_column_if_missing

            await MigrationRunner([
                ("001_add_context_length", add_column_if_missing("role_bindings", "context_length", "INTEGER")),
                ("002_add_embedding_dim", add_column_if_missing("role_bindings", "embedding_dim", "INTEGER")),
                ("003_add_send_dimensions", add_column_if_missing("role_bindings", "send_dimensions", "INTEGER NOT NULL DEFAULT 0")),
            ]).apply(db)
            await db.commit()

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
            return datetime.now(UTC)
        return datetime.fromisoformat(v)

    # ============ 角色绑定 (role_bindings) ============

    async def list_role_bindings(self, role: Optional[ModelType] = None) -> list[RoleBinding]:
        """列出角色绑定. role 为 None 时返回所有角色的绑定, 已按 (role, priority) 排序."""
        async with aiosqlite.connect(self.db_path) as db:
            if role is None:
                query = (
                    "SELECT role, priority, service_id, model, created_at, "
                    "context_length, embedding_dim, send_dimensions "
                    "FROM role_bindings ORDER BY role, priority"
                )
                params: tuple = ()
            else:
                query = (
                    "SELECT role, priority, service_id, model, created_at, "
                    "context_length, embedding_dim, send_dimensions "
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
                send_dimensions=bool(r[7]),
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
        send_dimensions: bool = False,
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

            now = datetime.now(UTC)
            await db.execute(
                "INSERT INTO role_bindings "
                "(role, priority, service_id, model, created_at, "
                "context_length, embedding_dim, send_dimensions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    role.value,
                    priority,
                    service_id,
                    model,
                    now.isoformat(),
                    context_length,
                    embedding_dim,
                    1 if send_dimensions else 0,
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
            send_dimensions=send_dimensions,
        )

    async def update_role_binding(
        self,
        role: ModelType,
        priority: int,
        *,
        service_id: Optional[str] = None,
        model: Optional[str] = None,
        context_length: Optional[int] = None,
        embedding_dim: Optional[int] = None,
        send_dimensions: Optional[bool] = None,
        clear_context_length: bool = False,
        clear_embedding_dim: bool = False,
    ) -> Optional[RoleBinding]:
        """就地更新一条角色绑定的可编辑字段. role/priority 由主键定位, 不可改.

        清空整型字段需显式传对应 clear_* 标志 (None 语义为 "不修改").
        service_id 若变更, 校验目标服务存在.
        找不到目标绑定时返回 None.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT role, priority, service_id, model, created_at, "
                "context_length, embedding_dim, send_dimensions "
                "FROM role_bindings WHERE role = ? AND priority = ?",
                (role.value, priority),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None

            if service_id is not None:
                async with db.execute(
                    "SELECT COUNT(*) FROM llm_services WHERE id = ?", (service_id,)
                ) as cursor:
                    svc_row = await cursor.fetchone()
                    if not svc_row or svc_row[0] == 0:
                        raise ValueError(f"服务 '{service_id}' 不存在")

            sets: list[str] = []
            params: list = []
            if service_id is not None:
                sets.append("service_id = ?")
                params.append(service_id)
            if model is not None:
                sets.append("model = ?")
                params.append(model)
            if clear_context_length:
                sets.append("context_length = NULL")
            elif context_length is not None:
                sets.append("context_length = ?")
                params.append(context_length)
            if clear_embedding_dim:
                sets.append("embedding_dim = NULL")
            elif embedding_dim is not None:
                sets.append("embedding_dim = ?")
                params.append(embedding_dim)
            if send_dimensions is not None:
                sets.append("send_dimensions = ?")
                params.append(1 if send_dimensions else 0)

            if sets:
                params.extend([role.value, priority])
                await db.execute(
                    f"UPDATE role_bindings SET {', '.join(sets)} "
                    "WHERE role = ? AND priority = ?",
                    tuple(params),
                )
                await db.commit()

            async with db.execute(
                "SELECT role, priority, service_id, model, created_at, "
                "context_length, embedding_dim, send_dimensions "
                "FROM role_bindings WHERE role = ? AND priority = ?",
                (role.value, priority),
            ) as cursor:
                r = await cursor.fetchone()
                assert r is not None
                return RoleBinding(
                    role=ModelType(r[0]),
                    priority=r[1],
                    service_id=r[2],
                    model=r[3],
                    created_at=self._parse_dt(r[4]),
                    context_length=r[5],
                    embedding_dim=r[6],
                    send_dimensions=bool(r[7]),
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
                        send_dimensions=b.send_dimensions,
                    )
                )
        return resolved
