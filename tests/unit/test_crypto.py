"""FernetEncryptor 加密/解密工具测试.

覆盖: 密钥自动生成与持久化、加密/解密往返、解密失败语义 (None vs raise)、
复用长连接 (encrypt_with/decrypt_with) 路径。
"""

from __future__ import annotations

import aiosqlite
import pytest
from src.infra.crypto import DEFAULT_ENCRYPTION_KEY_ID, FernetEncryptor

CONFIG_SCHEMA = """
    CREATE TABLE IF NOT EXISTS test_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""


@pytest.fixture
async def encryptor(tmp_path):
    """包装一个使用临时 SQLite config 表的 FernetEncryptor."""
    db_path = str(tmp_path / "crypto.db")
    # 建 config 表 (与 api_key_config 同构)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CONFIG_SCHEMA)
        await db.commit()

    enc = FernetEncryptor(db_path=db_path, config_table="test_config")
    yield enc


async def test_encrypt_decrypt_roundtrip(encryptor: FernetEncryptor) -> None:
    """明文 -> 密文 -> 明文 往返一致."""
    plaintext = "sk-1234567890-super-secret"
    cipher = await encryptor.encrypt(plaintext)
    assert cipher != plaintext  # 确确实实被加密了
    assert await encryptor.decrypt(cipher) == plaintext


async def test_encrypt_returns_different_ciphertext_each_time(encryptor: FernetEncryptor) -> None:
    """Fernet 每次生成随机盐, 同一明文两次加密结果不同."""
    c1 = await encryptor.encrypt("same")
    c2 = await encryptor.encrypt("same")
    assert c1 != c2
    assert await encryptor.decrypt(c1) == "same"
    assert await encryptor.decrypt(c2) == "same"


async def test_decrypt_empty_returns_none(encryptor: FernetEncryptor) -> None:
    """空密文直接返回 None, 不触发解密."""
    assert await encryptor.decrypt("") is None


async def test_decrypt_tampered_returns_none(encryptor: FernetEncryptor) -> None:
    """被篡改的密文默认返回 None (不抛异常)."""
    cipher = await encryptor.encrypt("secret")
    tampered = cipher[:-3] + ("X" if cipher[-3] != "X" else "Y")
    assert await encryptor.decrypt(tampered) is None


async def test_decrypt_raise_on_failure(tmp_path) -> None:
    """raise_on_decrypt_failure=True 时, 解密失败抛 ValueError."""
    db_path = str(tmp_path / "raise.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CONFIG_SCHEMA)
        await db.commit()
    enc = FernetEncryptor(
        db_path=db_path, config_table="test_config", raise_on_decrypt_failure=True
    )
    cipher = await enc.encrypt("secret")
    tampered = cipher[:-3] + ("X" if cipher[-3] != "X" else "Y")
    with pytest.raises(ValueError):
        await enc.decrypt(tampered)


async def test_key_generated_and_persisted(encryptor: FernetEncryptor) -> None:
    """首次加密会生成密钥并写入 config 表, 后续复用同一密钥."""
    await encryptor.encrypt("data")
    async with aiosqlite.connect(encryptor._db_path) as db:
        async with db.execute(
            "SELECT value FROM test_config WHERE key = ?", (DEFAULT_ENCRYPTION_KEY_ID,)
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None and row[0]

    # 用已持久化的密钥能解出同一密文 → 证明同密钥
    cipher = await encryptor.encrypt("again")
    async with aiosqlite.connect(encryptor._db_path) as db:
        new_enc = FernetEncryptor(db_path=encryptor._db_path, config_table="test_config")
        assert await new_enc.decrypt_with(db, cipher) == "again"


async def test_encrypt_with_reuses_connection(encryptor: FernetEncryptor) -> None:
    """encrypt_with/decrypt_with 复用传入的连接, 与独立连接结果一致."""
    async with aiosqlite.connect(encryptor._db_path) as db:
        cipher = await encryptor.encrypt_with(db, "conn-data")
        assert await encryptor.decrypt_with(db, cipher) == "conn-data"
