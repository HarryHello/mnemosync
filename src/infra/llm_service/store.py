"""LLM 服务商存储（SQLite + Fernet 加密）.

迁移自旧 storage/llm_service_store.py, 保持 Fernet 加密机制不变.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import aiosqlite

from src.infra.crypto import FernetEncryptor

from .models import (
    ApiFormat,
    LLMServiceProvider,
    ModelConfiguration,
    ModelRegistryEntry,
    ModelType,
    ResolvedCandidate,
    RoleBinding,
)

# ============================================================================
# 迁移函数 (v0.4.1 模型注册表)
# ============================================================================


async def _backfill_models_from_bindings(db: aiosqlite.Connection) -> None:
    """迁移 007: 从 role_bindings 去重回填 models 注册表 (能力字段收拢).

    同 service+model 多绑定取 MAX 能力值; COALESCE 防旧库 NULL 违反 NOT NULL.
    INSERT OR IGNORE 保证幂等 (MigrationRunner 只跑一次, 双保险).
    """
    await db.execute(
        """
        INSERT OR IGNORE INTO models
            (id, service_id, model, display_name,
             input_modalities, output_modalities,
             context_length, embedding_dim, send_dimensions,
             concurrency, enabled, created_at, updated_at)
        SELECT
            rb.service_id || ':' || rb.model,
            rb.service_id,
            rb.model,
            NULL,
            (SELECT COALESCE(rb2.input_modalities, '["text"]') FROM role_bindings rb2
             WHERE rb2.service_id = rb.service_id AND rb2.model = rb.model
             ORDER BY LENGTH(rb2.input_modalities) DESC LIMIT 1),
            (SELECT COALESCE(rb2.output_modalities, '["text"]') FROM role_bindings rb2
             WHERE rb2.service_id = rb.service_id AND rb2.model = rb.model
             ORDER BY LENGTH(rb2.output_modalities) DESC LIMIT 1),
            MAX(rb.context_length),
            MAX(rb.embedding_dim),
            COALESCE(MAX(rb.send_dimensions), 0),
            20,
            1,
            datetime('now'),
            datetime('now')
        FROM role_bindings rb
        GROUP BY rb.service_id, rb.model
        """
    )


async def _backfill_models_from_model_configs(db: aiosqlite.Connection) -> None:
    """迁移 008: 旧 model_configs (弱注册) 并入 models (无能力字段, 走默认值)."""
    await db.execute(
        """
        INSERT OR IGNORE INTO models
            (id, service_id, model, display_name,
             input_modalities, output_modalities,
             context_length, embedding_dim, send_dimensions,
             concurrency, enabled, created_at, updated_at)
        SELECT
            service_id || ':' || model,
            service_id,
            model,
            NULL,
            '["text"]', '["text"]',
            NULL, NULL, 0,
            20, 1,
            datetime('now'), datetime('now')
        FROM model_configs
        GROUP BY service_id, model
        """
    )


async def _backfill_binding_model_id(db: aiosqlite.Connection) -> None:
    """迁移 010: 按复合 id 回填 role_bindings.model_id (009 加列之后执行)."""
    await db.execute(
        "UPDATE role_bindings SET model_id = service_id || ':' || model WHERE model_id IS NULL"
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
                    api_format TEXT NOT NULL DEFAULT 'openai',
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
                    input_modalities TEXT DEFAULT '["text"]',
                    output_modalities TEXT DEFAULT '["text"]',
                    PRIMARY KEY (role, priority),
                    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_role_priority ON role_bindings(role, priority)"
            )
            # v0.4.1: 模型注册表 (模型一等实体, RFC model-registry)
            # id = 复合主键 {service_id}:{model}; 能力字段从 role_bindings 收拢至此;
            # concurrency 默认 20 (0 = 不限), 本版本仅管理, 执行层限流 v0.5
            await db.execute("""
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    display_name TEXT,
                    input_modalities TEXT NOT NULL DEFAULT '["text"]',
                    output_modalities TEXT NOT NULL DEFAULT '["text"]',
                    context_length INTEGER,
                    output_limit INTEGER,
                    supports_tools INTEGER NOT NULL DEFAULT 0,
                    embedding_dim INTEGER,
                    send_dimensions INTEGER NOT NULL DEFAULT 0,
                    concurrency INTEGER NOT NULL DEFAULT 20,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    UNIQUE (service_id, model),
                    FOREIGN KEY (service_id) REFERENCES llm_services(id) ON DELETE CASCADE
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_models_service ON models(service_id)")
            # 命名迁移: 幂等补列/回填 (旧库升级用; 新库 CREATE TABLE 已包含全部列, 自动跳过)
            from src.persistence.migrations import MigrationRunner, add_column_if_missing

            await MigrationRunner([
                ("001_add_context_length", add_column_if_missing("role_bindings", "context_length", "INTEGER")),
                ("002_add_embedding_dim", add_column_if_missing("role_bindings", "embedding_dim", "INTEGER")),
                ("003_add_send_dimensions", add_column_if_missing("role_bindings", "send_dimensions", "INTEGER NOT NULL DEFAULT 0")),
                ("004_add_input_modalities", add_column_if_missing("role_bindings", "input_modalities", "TEXT DEFAULT '[\"text\"]'")),
                ("005_add_output_modalities", add_column_if_missing("role_bindings", "output_modalities", "TEXT DEFAULT '[\"text\"]'")),
                ("006_add_api_format", add_column_if_missing("llm_services", "api_format", "TEXT NOT NULL DEFAULT 'openai'")),
                ("007_backfill_models_from_bindings", _backfill_models_from_bindings),
                ("008_backfill_models_from_model_configs", _backfill_models_from_model_configs),
                ("009_add_binding_model_id", add_column_if_missing("role_bindings", "model_id", "TEXT")),
                ("010_backfill_binding_model_id", _backfill_binding_model_id),
                ("011_add_output_limit", add_column_if_missing("models", "output_limit", "INTEGER")),
                ("012_add_supports_tools", add_column_if_missing("models", "supports_tools", "INTEGER NOT NULL DEFAULT 0")),
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
                "INSERT INTO llm_services (id, base_url, api_key_encrypted, api_format, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (service.id, service.base_url, encrypted, service.api_format,
                 service.created_at.isoformat(), service.updated_at.isoformat()),
            )
            await db.commit()
        return service

    async def get_service(self, service_id: str) -> LLMServiceProvider | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, api_format, created_at, updated_at FROM llm_services WHERE id = ?",
                (service_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return LLMServiceProvider(
                    id=row[0], base_url=row[1],
                    api_key=await self._decrypt(row[2]),
                    api_format=row[3] or "openai",
                    created_at=self._parse_dt(row[4]),
                    updated_at=self._parse_dt(row[5]),
                )

    async def list_services(self) -> list[LLMServiceProvider]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, api_format, created_at, updated_at FROM llm_services ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMServiceProvider(
                        id=r[0], base_url=r[1], api_key=await self._decrypt(r[2]),
                        api_format=r[3] or "openai",
                        created_at=self._parse_dt(r[4]), updated_at=self._parse_dt(r[5]),
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
    ) -> ModelConfiguration | None:
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
    def _parse_dt(v: str | None) -> datetime:
        if v is None:
            return datetime.now(UTC)
        return datetime.fromisoformat(v)

    # ============ 模型注册表 CRUD (v0.4.1, RFC model-registry) ============

    _MODEL_COLUMNS = (
        "id, service_id, model, display_name, input_modalities, output_modalities, "
        "context_length, output_limit, supports_tools, embedding_dim, send_dimensions, "
        "concurrency, enabled, created_at, updated_at"
    )

    def _model_row_to_entry(self, row: Any) -> ModelRegistryEntry:
        return ModelRegistryEntry(
            id=row[0],
            service_id=row[1],
            model=row[2],
            display_name=row[3],
            input_modalities=json.loads(row[4]) if row[4] else ["text"],
            output_modalities=json.loads(row[5]) if row[5] else ["text"],
            context_length=row[6],
            output_limit=row[7],
            supports_tools=bool(row[8]),
            embedding_dim=row[9],
            send_dimensions=bool(row[10]),
            concurrency=row[11] if row[11] is not None else 20,  # 0 = 不限, 不能 or 掉
            enabled=bool(row[12]),
            created_at=self._parse_dt(row[13]),
            updated_at=self._parse_dt(row[14]),
        )

    async def save_model_registry(self, entry: ModelRegistryEntry) -> ModelRegistryEntry:
        """upsert 注册表条目 (复合 id {service_id}:{model})."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?", (entry.service_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] == 0:
                    raise ValueError(f"服务 '{entry.service_id}' 不存在")
            await db.execute(
                "INSERT INTO models (id, service_id, model, display_name, input_modalities, "
                "output_modalities, context_length, output_limit, supports_tools, "
                "embedding_dim, send_dimensions, concurrency, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "display_name = excluded.display_name, "
                "input_modalities = excluded.input_modalities, "
                "output_modalities = excluded.output_modalities, "
                "context_length = excluded.context_length, "
                "output_limit = excluded.output_limit, "
                "supports_tools = excluded.supports_tools, "
                "embedding_dim = excluded.embedding_dim, "
                "send_dimensions = excluded.send_dimensions, "
                "concurrency = excluded.concurrency, "
                "enabled = excluded.enabled, "
                "updated_at = excluded.updated_at",
                (
                    entry.id, entry.service_id, entry.model, entry.display_name,
                    json.dumps(entry.input_modalities),
                    json.dumps(entry.output_modalities),
                    entry.context_length, entry.output_limit,
                    1 if entry.supports_tools else 0, entry.embedding_dim,
                    1 if entry.send_dimensions else 0,
                    entry.concurrency, 1 if entry.enabled else 0,
                    entry.created_at.isoformat(), entry.updated_at.isoformat(),
                ),
            )
            await db.commit()
        return entry

    async def get_model_registry(self, model_id: str) -> ModelRegistryEntry | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT {self._MODEL_COLUMNS} FROM models WHERE id = ?", (model_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return self._model_row_to_entry(row) if row else None

    async def list_model_registry(
        self, service_id: str | None = None, *, enabled_only: bool = False
    ) -> list[ModelRegistryEntry]:
        query = f"SELECT {self._MODEL_COLUMNS} FROM models"
        conds: list[str] = []
        params: list[Any] = []
        if service_id is not None:
            conds.append("service_id = ?")
            params.append(service_id)
        if enabled_only:
            conds.append("enabled = 1")
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY service_id, model"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [self._model_row_to_entry(r) for r in rows]

    async def update_model_registry(
        self,
        model_id: str,
        *,
        display_name: str | None = None,
        clear_display_name: bool = False,
        input_modalities: list[str] | None = None,
        output_modalities: list[str] | None = None,
        context_length: int | None = None,
        clear_context_length: bool = False,
        output_limit: int | None = None,
        clear_output_limit: bool = False,
        supports_tools: bool | None = None,
        embedding_dim: int | None = None,
        clear_embedding_dim: bool = False,
        send_dimensions: bool | None = None,
        concurrency: int | None = None,
        enabled: bool | None = None,
    ) -> ModelRegistryEntry | None:
        """就地更新注册表条目. None 语义 = 不修改; clear_* 显式清空. 找不到返回 None."""
        if concurrency is not None and concurrency < 0:
            raise ValueError("concurrency 必须 >= 0 (0 = 不限)")
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT {self._MODEL_COLUMNS} FROM models WHERE id = ?", (model_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            sets: list[str] = []
            params: list[Any] = []
            if clear_display_name:
                sets.append("display_name = NULL")
            elif display_name is not None:
                sets.append("display_name = ?")
                params.append(display_name or None)
            if input_modalities is not None:
                sets.append("input_modalities = ?")
                params.append(json.dumps(input_modalities))
            if output_modalities is not None:
                sets.append("output_modalities = ?")
                params.append(json.dumps(output_modalities))
            if clear_context_length:
                sets.append("context_length = NULL")
            elif context_length is not None:
                sets.append("context_length = ?")
                params.append(context_length)
            if clear_output_limit:
                sets.append("output_limit = NULL")
            elif output_limit is not None:
                sets.append("output_limit = ?")
                params.append(output_limit)
            if supports_tools is not None:
                sets.append("supports_tools = ?")
                params.append(1 if supports_tools else 0)
            if clear_embedding_dim:
                sets.append("embedding_dim = NULL")
            elif embedding_dim is not None:
                sets.append("embedding_dim = ?")
                params.append(embedding_dim)
            if send_dimensions is not None:
                sets.append("send_dimensions = ?")
                params.append(1 if send_dimensions else 0)
            if concurrency is not None:
                sets.append("concurrency = ?")
                params.append(concurrency)
            if enabled is not None:
                sets.append("enabled = ?")
                params.append(1 if enabled else 0)
            if sets:
                sets.append("updated_at = ?")
                params.append(datetime.now(UTC).isoformat())
                params.append(model_id)
                await db.execute(
                    f"UPDATE models SET {', '.join(sets)} WHERE id = ?", tuple(params),
                )
                await db.commit()
        return await self.get_model_registry(model_id)

    async def delete_model_registry(self, model_id: str) -> bool:
        """删除注册表条目. 被 role_bindings 引用时拒绝 (需先解绑)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM role_bindings WHERE model_id = ?", (model_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row and row[0] > 0:
                raise ValueError("模型被角色绑定引用, 请先在『模型管理』中解绑")
            cur = await db.execute("DELETE FROM models WHERE id = ?", (model_id,))
            await db.commit()
            return cur.rowcount > 0

    async def import_model_registry(
        self, service_id: str, entries: list[ModelRegistryEntry]
    ) -> tuple[int, int]:
        """批量导入模型注册表条目 (来自上游 /v1/models, 含能力解析).

        display_name 为空时默认 {service_id}/{model}; 已存在 (同 service+model) 跳过.
        返回 (added, skipped).
        """
        added = 0
        skipped = 0
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?", (service_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] == 0:
                raise ValueError(f"服务 '{service_id}' 不存在")
            for entry in entries:
                name = entry.model.strip()
                if not name:
                    continue
                mid = f"{service_id}:{name}"
                display = (entry.display_name or "").strip() or f"{service_id}/{name}"
                cur = await db.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id, service_id, model, display_name, input_modalities, "
                    "output_modalities, context_length, output_limit, supports_tools, "
                    "embedding_dim, send_dimensions, concurrency, enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, ?, ?)",
                    (
                        mid, service_id, name, display,
                        json.dumps(entry.input_modalities),
                        json.dumps(entry.output_modalities),
                        entry.context_length, entry.output_limit,
                        1 if entry.supports_tools else 0,
                        1 if entry.send_dimensions else 0,
                        entry.concurrency if entry.concurrency else 20,
                        now, now,
                    ),
                )
                if cur.rowcount and cur.rowcount > 0:
                    added += 1
                else:
                    skipped += 1
            await db.commit()
        return added, skipped

    # ============ 角色绑定 (role_bindings) ============

    async def list_role_bindings(self, role: ModelType | None = None) -> list[RoleBinding]:
        """列出角色绑定. role 为 None 时返回所有角色的绑定, 已按 (role, priority) 排序.

        v0.4.1: LEFT JOIN models 注册表 — 能力字段优先取注册表 (COALESCE),
        旧行 (迁移前能力列值) 作为兜底; 同时带出 model_id / display_name.
        """
        select_cols = (
            "b.role, b.priority, b.service_id, b.model, b.created_at, "
            "COALESCE(m.context_length, b.context_length), "
            "COALESCE(m.embedding_dim, b.embedding_dim), "
            "COALESCE(m.send_dimensions, b.send_dimensions, 0), "
            "COALESCE(m.input_modalities, b.input_modalities, '[\"text\"]'), "
            "COALESCE(m.output_modalities, b.output_modalities, '[\"text\"]'), "
            "b.model_id, m.display_name "
        )
        async with aiosqlite.connect(self.db_path) as db:
            if role is None:
                query = (
                    f"SELECT {select_cols} "
                    "FROM role_bindings b "
                    "LEFT JOIN models m ON m.id = b.model_id "
                    "ORDER BY b.role, b.priority"
                )
                params: tuple[Any, ...] = ()
            else:
                query = (
                    f"SELECT {select_cols} "
                    "FROM role_bindings b "
                    "LEFT JOIN models m ON m.id = b.model_id "
                    "WHERE b.role = ? ORDER BY b.priority"
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
                model_id=r[10],
                display_name=r[11],
                created_at=self._parse_dt(r[4]),
                context_length=r[5],
                embedding_dim=r[6],
                send_dimensions=bool(r[7]),
                input_modalities=json.loads(r[8]) if r[8] else ["text"],
                output_modalities=json.loads(r[9]) if r[9] else ["text"],
            )
            for r in rows
        ]

    async def add_role_binding(
        self,
        role: ModelType,
        model_id: str,
        priority: int | None = None,
    ) -> RoleBinding:
        """追加一条角色绑定 (v0.4.1: 从模型注册表引用).

        模型的服务商/能力字段随注册表 (model_id 解析); 绑定表只冗余 service_id+model.
        priority 省略时排到列表末尾. 指定 priority 时若已被占用, 后续所有条目
        priority += 1 让位. EMBEDDING 角色只允许一条绑定 (换模型会破坏向量语义空间,
        走 reindex 流程). 模型不存在或已禁用时拒绝绑定.
        """
        entry = await self.get_model_registry(model_id)
        if entry is None:
            raise ValueError(f"模型不存在: {model_id}")
        if not entry.enabled:
            raise ValueError(f"模型已禁用: {model_id}")
        service_id, model = entry.service_id, entry.model
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
                "(role, priority, service_id, model, model_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (role.value, priority, service_id, model, model_id, now.isoformat()),
            )
            await db.commit()

        return RoleBinding(
            role=role,
            priority=priority,
            service_id=service_id,
            model=model,
            model_id=model_id,
            display_name=entry.display_name,
            created_at=now,
            context_length=entry.context_length,
            embedding_dim=entry.embedding_dim,
            send_dimensions=entry.send_dimensions,
            input_modalities=entry.input_modalities,
            output_modalities=entry.output_modalities,
        )

    async def update_role_binding(
        self,
        role: ModelType,
        priority: int,
        *,
        service_id: str | None = None,
        model: str | None = None,
        model_id: str | None = None,
        context_length: int | None = None,
        embedding_dim: int | None = None,
        send_dimensions: bool | None = None,
        clear_context_length: bool = False,
        clear_embedding_dim: bool = False,
        input_modalities: list[str] | None = None,
        output_modalities: list[str] | None = None,
    ) -> RoleBinding | None:
        """就地更新一条角色绑定的可编辑字段. role/priority 由主键定位, 不可改.

        清空整型字段需显式传对应 clear_* 标志 (None 语义为 "不修改").
        service_id 若变更, 校验目标服务存在.
        model_id 传参 = 更换模型 (v0.4.1 注册表引用), 同步更新 service_id/model/model_id.
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

            if model_id is not None:
                entry = await self.get_model_registry(model_id)
                if entry is None:
                    raise ValueError(f"模型不存在: {model_id}")
                if not entry.enabled:
                    raise ValueError(f"模型已禁用: {model_id}")
                service_id = entry.service_id
                model = entry.model

            if service_id is not None:
                async with db.execute(
                    "SELECT COUNT(*) FROM llm_services WHERE id = ?", (service_id,)
                ) as cursor:
                    svc_row = await cursor.fetchone()
                    if not svc_row or svc_row[0] == 0:
                        raise ValueError(f"服务 '{service_id}' 不存在")

            sets: list[str] = []
            params: list[Any] = []
            if service_id is not None:
                sets.append("service_id = ?")
                params.append(service_id)
            if model is not None:
                sets.append("model = ?")
                params.append(model)
            if model_id is not None:
                sets.append("model_id = ?")
                params.append(model_id)
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
            if input_modalities is not None:
                sets.append("input_modalities = ?")
                params.append(json.dumps(input_modalities))
            if output_modalities is not None:
                sets.append("output_modalities = ?")
                params.append(json.dumps(output_modalities))

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
                "context_length, embedding_dim, send_dimensions, "
                "input_modalities, output_modalities, model_id "
                "FROM role_bindings WHERE role = ? AND priority = ?",
                (role.value, priority),
            ) as cursor:
                r = await cursor.fetchone()
                assert r is not None
                binding = RoleBinding(
                    role=ModelType(r[0]),
                    priority=r[1],
                    service_id=r[2],
                    model=r[3],
                    model_id=r[10],
                    created_at=self._parse_dt(r[4]),
                    context_length=r[5],
                    embedding_dim=r[6],
                    send_dimensions=bool(r[7]),
                    input_modalities=json.loads(r[8]) if r[8] else ["text"],
                    output_modalities=json.loads(r[9]) if r[9] else ["text"],
                )
                if binding.model_id:
                    reg = await self.get_model_registry(binding.model_id)
                    binding.display_name = reg.display_name if reg else None
                return binding

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
                    "SELECT base_url, api_key_encrypted, api_format FROM llm_services WHERE id = ?",
                    (b.service_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if not row:
                    # 服务被删除但绑定残留 (FK 应该已 cascade, 兜底忽略)
                    continue
                base_url, encrypted = row[0], row[1]
                raw_api_format = row[2] or "openai"
                api_format = cast(ApiFormat, raw_api_format)
                api_key = await self._decrypt(encrypted)
                resolved.append(
                    ResolvedCandidate(
                        role=b.role,
                        priority=b.priority,
                        service_id=b.service_id,
                        base_url=base_url,
                        api_key=api_key,
                        model=b.model,
                        api_format=api_format,
                        context_length=b.context_length,
                        embedding_dim=b.embedding_dim,
                        send_dimensions=b.send_dimensions,
                        input_modalities=b.input_modalities,
                        output_modalities=b.output_modalities,
                    )
                )
        return resolved
