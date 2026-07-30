"""Fernet 对称加密工具.

供需要本地加密存储敏感字段 (API Key 等) 的 Store 复用,
统一密钥生成/加载、加密、解密逻辑, 避免重复实现.
"""

from __future__ import annotations

import base64

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

# 加密密钥在 config 表中的 key 名 (各 store 共用同一约定)
DEFAULT_ENCRYPTION_KEY_ID = "__encryption_key__"


class FernetEncryptor:
    """Fernet 对称加密器, 密钥自动生成并持久化到 SQLite config 表.

    Usage::

        encryptor = FernetEncryptor(
            db_path=self.db_path,
            config_table="api_key_config",
        )
        encrypted = await encryptor.encrypt("secret")
        decrypted = await encryptor.decrypt(encrypted)  # None on failure

    如果需要复用已有的长连接, 用 :meth:`encrypt_with` / :meth:`decrypt_with`
    直接传入 ``db`` 对象 (api_key_store 的使用模式).
    """

    def __init__(
        self,
        *,
        db_path: str,
        config_table: str,
        key_id: str = DEFAULT_ENCRYPTION_KEY_ID,
        raise_on_decrypt_failure: bool = False,
    ) -> None:
        self._db_path = db_path
        self._config_table = config_table
        self._key_id = key_id
        self._raise_on_decrypt_failure = raise_on_decrypt_failure
        self._fernet: Fernet | None = None

    async def _load_or_create_key(self, db: aiosqlite.Connection) -> bytes:
        async with db.execute(
            f"SELECT value FROM {self._config_table} WHERE key = ?", (self._key_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return base64.urlsafe_b64decode(row[0])
        key = Fernet.generate_key()
        await db.execute(
            f"INSERT OR REPLACE INTO {self._config_table} (key, value) VALUES (?, ?)",
            (self._key_id, base64.urlsafe_b64encode(key).decode()),
        )
        await db.commit()
        return key

    async def _get_fernet(self, db: aiosqlite.Connection) -> Fernet:
        if self._fernet is None:
            key = await self._load_or_create_key(db)
            self._fernet = Fernet(key)
        return self._fernet

    async def encrypt(self, plaintext: str) -> str:
        """加密文本 (自动打开临时连接)."""
        async with aiosqlite.connect(self._db_path) as db:
            return await self.encrypt_with(db, plaintext)

    async def decrypt(self, ciphertext: str) -> str | None:
        """解密文本 (自动打开临时连接). 返回 None 表示解密失败."""
        async with aiosqlite.connect(self._db_path) as db:
            return await self.decrypt_with(db, ciphertext)

    async def encrypt_with(self, db: aiosqlite.Connection, plaintext: str) -> str:
        """加密文本 (复用传入的 db 连接)."""
        f = await self._get_fernet(db)
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    async def decrypt_with(self, db: aiosqlite.Connection, ciphertext: str) -> str | None:
        """解密文本 (复用传入的 db 连接).

        解密失败 (密钥损坏/数据篡改) 时:
        - ``raise_on_decrypt_failure=False`` → 返回 None
        - ``raise_on_decrypt_failure=True``  → 抛出 ValueError
        """
        if not ciphertext:
            return None
        f = await self._get_fernet(db)
        try:
            return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as e:
            if self._raise_on_decrypt_failure:
                raise ValueError("API Key 解密失败（密钥损坏或数据被篡改）") from e
            return None
