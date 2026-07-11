"""LLM 服务提供商存储层.

提供 LLMServiceProvider 和 ModelConfiguration 的持久化能力.
API Key 使用 Fernet 对称加密存储, 加密密钥自动创建并存储在同一数据库中.
"""

import aiosqlite
import base64
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from typing import Optional

from src.models.llm_service import (
    LLMServiceProvider,
    ModelConfiguration,
    ModelType,
    ServiceAlreadyExistsError,
    ServiceNotFoundError,
    ModelNotFoundError,
)


class LLMServiceStore:
    """LLM 服务提供商 SQLite 存储实现.

    使用 Fernet 对称加密存储 API Key.
    加密密钥自动生成并存储在数据库的 config 表中, 无需环境变量.
    """

    _ENCRYPTION_KEY_ID = "__encryption_key__"

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._fernet: Optional[Fernet] = None

    async def _load_or_create_encryption_key(self) -> bytes:
        """从数据库加载或创建加密密钥.

        Returns:
            Fernet 密钥 (32 字节)
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 尝试读取已存储的密钥
            async with db.execute(
                "SELECT value FROM config WHERE key = ?",
                (self._ENCRYPTION_KEY_ID,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return base64.urlsafe_b64decode(row[0])

            # 生成新密钥并存储
            key = Fernet.generate_key()
            key_b64 = base64.urlsafe_b64encode(key).decode()
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (self._ENCRYPTION_KEY_ID, key_b64)
            )
            await db.commit()
            return key

    # ==================== 加密工具 ====================

    async def _encrypt(self, plaintext: str) -> str:
        """加密字符串.

        Args:
            plaintext: 明文

        Returns:
            加密后的 base64 字符串
        """
        fernet = await self._get_fernet()
        return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    async def _decrypt(self, ciphertext: str) -> str:
        """解密字符串.

        Args:
            ciphertext: 加密后的 base64 字符串

        Returns:
            解密后的明文

        Raises:
            ValueError: 解密失败 (密钥不匹配或数据被篡改)
        """
        fernet = await self._get_fernet()
        try:
            return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            raise ValueError(
                "API Key 解密失败. 加密密钥可能已损坏或数据被篡改."
            )

    async def _get_fernet(self) -> Fernet:
        """懒加载获取 Fernet 实例."""
        if self._fernet is None:
            key = await self._load_or_create_encryption_key()
            self._fernet = Fernet(key)
        return self._fernet

    # ==================== 初始化 ====================

    async def init_db(self) -> None:
        """初始化数据库表."""
        async with aiosqlite.connect(self.db_path) as db:
            # 配置表 (存储加密密钥等)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # LLM 服务提供商表
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

            # 模型配置表
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

    # ==================== LLMServiceProvider CRUD ====================

    async def save(self, service: LLMServiceProvider) -> LLMServiceProvider:
        """保存服务提供商 (新增或更新).

        Args:
            service: 服务提供商实例 (api_key 应为明文)

        Returns:
            保存后的实例 (api_key 已加密)

        Raises:
            ServiceAlreadyExistsError: service.id 已存在
        """
        encrypted_key = await self._encrypt(service.api_key)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?",
                (service.id,),
            )
            row = await cursor.fetchone()
            if row and row[0] > 0:
                raise ServiceAlreadyExistsError(f"服务 '{service.id}' 已存在")

            await db.execute(
                """
                INSERT INTO llm_services (id, base_url, api_key_encrypted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    service.id,
                    service.base_url,
                    encrypted_key,
                    service.created_at.isoformat(),
                    service.updated_at.isoformat(),
                ),
            )
            await db.commit()

        return service

    async def get_by_id(self, service_id: str) -> Optional[LLMServiceProvider]:
        """根据 ID 获取服务提供商 (自动解密 API Key).

        Args:
            service_id: 服务唯一标识

        Returns:
            服务提供商实例, 未找到返回 None
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, created_at, updated_at FROM llm_services WHERE id = ?",
                (service_id,),
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    return None

                return LLMServiceProvider(
                    id=row[0],
                    base_url=row[1],
                    api_key=await self._decrypt(row[2]),
                    created_at=self._parse_datetime(row[3]),
                    updated_at=self._parse_datetime(row[4]),
                )

    async def list_all(self) -> list[LLMServiceProvider]:
        """列出所有服务提供商 (自动解密 API Key).

        Returns:
            服务提供商列表, 按创建时间倒序
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, base_url, api_key_encrypted, created_at, updated_at FROM llm_services ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

                services = []
                for row in rows:
                    services.append(LLMServiceProvider(
                        id=row[0],
                        base_url=row[1],
                        api_key=await self._decrypt(row[2]),
                        created_at=self._parse_datetime(row[3]),
                        updated_at=self._parse_datetime(row[4]),
                    ))
                return services

    async def delete(self, service_id: str) -> bool:
        """删除服务提供商及其关联的模型配置.

        Args:
            service_id: 服务唯一标识

        Returns:
            是否删除成功
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM llm_services WHERE id = ?",
                (service_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def exists(self, service_id: str) -> bool:
        """检查服务是否存在.

        Args:
            service_id: 服务唯一标识

        Returns:
            是否存在
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?",
                (service_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] > 0 if row else False

    # ==================== ModelConfiguration CRUD ====================

    async def save_model(self, config: ModelConfiguration) -> ModelConfiguration:
        """保存模型配置 (新增或更新).

        Args:
            config: 模型配置实例

        Returns:
            保存后的实例

        Raises:
            ServiceNotFoundError: 关联的服务不存在
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 检查关联服务是否存在
            async with db.execute(
                "SELECT COUNT(*) FROM llm_services WHERE id = ?",
                (config.service_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] == 0:
                    raise ServiceNotFoundError(f"服务 '{config.service_id}' 不存在")

            # 同一 service_id + model_type 只允许一条记录 (覆盖更新)
            async with db.execute(
                """
                SELECT id FROM model_configs
                WHERE service_id = ? AND model_type = ?
                """,
                (config.service_id, config.model_type.value),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                # 更新已有配置
                await db.execute(
                    """
                    UPDATE model_configs
                    SET model = ?, updated_at = ?
                    WHERE service_id = ? AND model_type = ?
                    """,
                    (
                        config.model,
                        config.updated_at.isoformat(),
                        config.service_id,
                        config.model_type.value,
                    ),
                )
            else:
                # 新增配置
                await db.execute(
                    """
                    INSERT INTO model_configs (id, service_id, model, model_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.id,
                        config.service_id,
                        config.model,
                        config.model_type.value,
                        config.created_at.isoformat(),
                        config.updated_at.isoformat(),
                    ),
                )

            await db.commit()
            return config

    async def get_model(
        self,
        service_id: str,
        model_type: ModelType,
    ) -> Optional[ModelConfiguration]:
        """获取指定服务的指定类型模型配置.

        Args:
            service_id: 服务唯一标识
            model_type: 模型类型 (main/assist)

        Returns:
            模型配置实例, 未找到返回 None
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, service_id, model, model_type, created_at, updated_at
                FROM model_configs
                WHERE service_id = ? AND model_type = ?
                """,
                (service_id, model_type.value),
            ) as cursor:
                row = await cursor.fetchone()

                if not row:
                    return None

                return ModelConfiguration(
                    id=row[0],
                    service_id=row[1],
                    model=row[2],
                    model_type=ModelType(row[3]),
                    created_at=self._parse_datetime(row[4]),
                    updated_at=self._parse_datetime(row[5]),
                )

    async def list_models(self, service_id: str) -> list[ModelConfiguration]:
        """列出指定服务的所有模型配置.

        Args:
            service_id: 服务唯一标识

        Returns:
            模型配置列表
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, service_id, model, model_type, created_at, updated_at
                FROM model_configs
                WHERE service_id = ?
                ORDER BY model_type
                """,
                (service_id,),
            ) as cursor:
                rows = await cursor.fetchall()

                return [
                    ModelConfiguration(
                        id=row[0],
                        service_id=row[1],
                        model=row[2],
                        model_type=ModelType(row[3]),
                        created_at=self._parse_datetime(row[4]),
                        updated_at=self._parse_datetime(row[5]),
                    )
                    for row in rows
                ]

    async def get_main_model(self, service_id: str) -> Optional[ModelConfiguration]:
        """获取指定服务的主模型配置.

        Args:
            service_id: 服务唯一标识

        Returns:
            主模型配置实例
        """
        return await self.get_model(service_id, ModelType.MAIN)

    async def get_assist_model(self, service_id: str) -> Optional[ModelConfiguration]:
        """获取指定服务的辅助模型配置.

        Args:
            service_id: 服务唯一标识

        Returns:
            辅助模型配置实例
        """
        return await self.get_model(service_id, ModelType.ASSIST)

    # ==================== 工具方法 ====================

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> datetime:
        """解析时间戳."""
        if value is None:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(value)
