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
            cur = await db.execute("DELETE FROM llm_services WHERE id = ?", (service_id,))
            await db.execute("DELETE FROM model_configs WHERE service_id = ?", (service_id,))
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
