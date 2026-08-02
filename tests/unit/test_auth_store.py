"""SqliteAuthStore 单元测试.

覆盖:
- hash_password / verify_password / validate_password_strength
- User.create / SessionToken.generate / is_expired
- SqliteAuthStore: init_db, create_default_user, authenticate, create_session,
  get_session, invalidate_session, change_password, has_any_user
- _sign_token / _hash_token: HMAC 签名一致性
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from src.persistence.auth_store import (
    SessionToken,
    SqliteAuthStore,
    User,
    _hash_token,
    _sign_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self) -> None:
        h = hash_password("mypassword123")
        assert h != "mypassword123"
        assert verify_password("mypassword123", h) is True

    def test_wrong_password_fails(self) -> None:
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_hash_varies_with_salt(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # different salts
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


# ---------------------------------------------------------------------------
# validate_password_strength
# ---------------------------------------------------------------------------

class TestValidatePasswordStrength:
    def test_too_short(self) -> None:
        ok, err = validate_password_strength("ab")
        assert ok is False
        assert "6" in err

    def test_default_password_rejected(self) -> None:
        ok, err = validate_password_strength("mnemosync")
        assert ok is False
        assert "默认" in err

    def test_default_password_allowed_when_flag(self) -> None:
        ok, err = validate_password_strength("mnemosync", allow_default=True)
        assert ok is True
        assert err is None

    def test_valid_password(self) -> None:
        ok, err = validate_password_strength("strongpass")
        assert ok is True
        assert err is None

    def test_exactly_6_chars_valid(self) -> None:
        ok, _ = validate_password_strength("123456")
        assert ok is True


# ---------------------------------------------------------------------------
# User.create
# ---------------------------------------------------------------------------

class TestUser:
    def test_create_sets_fields(self) -> None:
        u = User.create("alice", "hash123")
        assert u.username == "alice"
        assert u.password_hash == "hash123"
        assert u.must_change_password is True
        assert u.is_active is True
        assert u.id.startswith("")  # hex string
        assert len(u.id) == 32
        assert u.last_login_at is None

    def test_create_with_must_change_false(self) -> None:
        u = User.create("bob", "h", must_change_password=False)
        assert u.must_change_password is False


# ---------------------------------------------------------------------------
# SessionToken
# ---------------------------------------------------------------------------

class TestSessionToken:
    def test_generate_fields(self) -> None:
        t = SessionToken.generate("user_1")
        assert t.user_id == "user_1"
        assert len(t.raw_token) > 0
        assert len(t.token_hash) == 64  # SHA-256 hex
        assert t.is_valid is True
        assert t.expires_at > datetime.now(UTC)

    def test_is_expired_future(self) -> None:
        t = SessionToken(
            id="x", user_id="u", token_hash="h", raw_token="r",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert t.is_expired() is False

    def test_is_expired_past(self) -> None:
        t = SessionToken(
            id="x", user_id="u", token_hash="h", raw_token="r",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        assert t.is_expired() is True


# ---------------------------------------------------------------------------
# _sign_token / _hash_token
# ---------------------------------------------------------------------------

class TestTokenSigning:
    def test_deterministic(self) -> None:
        key = b"secret-key-for-testing-12345678"
        h1 = _sign_token("raw-token", key)
        h2 = _sign_token("raw-token", key)
        assert h1 == h2

    def test_different_tokens_different_hashes(self) -> None:
        key = b"secret-key-for-testing-12345678"
        h1 = _sign_token("token-a", key)
        h2 = _sign_token("token-b", key)
        assert h1 != h2

    def test_hash_token_matches_sign(self) -> None:
        key = b"secret-key-for-testing-12345678"
        with patch("src.persistence.auth_store._load_or_create_session_key", return_value=key):
            h1 = _hash_token("test")
            h2 = _sign_token("test", key)
            assert h1 == h2


# ---------------------------------------------------------------------------
# SqliteAuthStore (in-memory via fixture)
# ---------------------------------------------------------------------------

class TestSqliteAuthStore:
    @pytest.mark.asyncio
    async def test_has_any_user_empty(self, auth_store: SqliteAuthStore) -> None:
        assert await auth_store.has_any_user() is False

    @pytest.mark.asyncio
    async def test_create_default_user(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("mypassword123")
        assert user.username == "mnemosync"
        assert user.must_change_password is True
        assert await auth_store.has_any_user() is True

    @pytest.mark.asyncio
    async def test_create_default_user_rejects_duplicate(self, auth_store: SqliteAuthStore) -> None:
        await auth_store.create_default_user("mypassword123")
        with pytest.raises(ValueError, match="已存在"):
            await auth_store.create_default_user("other123")

    @pytest.mark.asyncio
    async def test_authenticate_success(self, auth_store: SqliteAuthStore) -> None:
        await auth_store.create_default_user("mypassword123")
        user = await auth_store.authenticate("mnemosync", "mypassword123")
        assert user.username == "mnemosync"
        # last_login_at is updated in DB but not on the returned object (by design)
        # Verify the DB was updated
        db_user = await auth_store.get_user_by_id(user.id)
        assert db_user is not None
        assert db_user.last_login_at is not None

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, auth_store: SqliteAuthStore) -> None:
        await auth_store.create_default_user("mypassword123")
        with pytest.raises(ValueError, match="错误"):
            await auth_store.authenticate("mnemosync", "wrong")

    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_user(self, auth_store: SqliteAuthStore) -> None:
        with pytest.raises(ValueError, match="错误"):
            await auth_store.authenticate("nobody", "x")

    @pytest.mark.asyncio
    async def test_get_user_by_username(self, auth_store: SqliteAuthStore) -> None:
        await auth_store.create_default_user("pass1234")
        user = await auth_store.get_user_by_username("mnemosync")
        assert user is not None
        assert user.id

    @pytest.mark.asyncio
    async def test_get_user_by_username_missing(self, auth_store: SqliteAuthStore) -> None:
        assert await auth_store.get_user_by_username("nope") is None

    @pytest.mark.asyncio
    async def test_get_user_by_id(self, auth_store: SqliteAuthStore) -> None:
        created = await auth_store.create_default_user("pass1234")
        found = await auth_store.get_user_by_id(created.id)
        assert found is not None
        assert found.username == "mnemosync"

    @pytest.mark.asyncio
    async def test_get_user_by_id_missing(self, auth_store: SqliteAuthStore) -> None:
        assert await auth_store.get_user_by_id("nonexistent") is None

    @pytest.mark.asyncio
    async def test_create_session_and_get(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        session = await auth_store.create_session(user.id)
        assert session.raw_token
        assert session.token_hash

        retrieved = await auth_store.get_session(session.raw_token)
        assert retrieved.user_id == user.id
        assert retrieved.is_expired() is False

    @pytest.mark.asyncio
    async def test_get_session_invalid_token(self, auth_store: SqliteAuthStore) -> None:
        with pytest.raises(ValueError, match="无效"):
            await auth_store.get_session("totally-fake-token")

    @pytest.mark.asyncio
    async def test_invalidate_session(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        session = await auth_store.create_session(user.id)
        await auth_store.invalidate_session(session.raw_token)

        with pytest.raises(ValueError, match="无效"):
            await auth_store.get_session(session.raw_token)

    @pytest.mark.asyncio
    async def test_change_password(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        updated = await auth_store.change_password(user.id, "pass1234", "newpass5678")
        assert updated.must_change_password is False

        # old password should fail
        with pytest.raises(ValueError):
            await auth_store.authenticate("mnemosync", "pass1234")

        # new password works
        authed = await auth_store.authenticate("mnemosync", "newpass5678")
        assert authed.id == user.id

    @pytest.mark.asyncio
    async def test_change_password_wrong_old(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        with pytest.raises(ValueError, match="原密码"):
            await auth_store.change_password(user.id, "wrong", "newpass123")

    @pytest.mark.asyncio
    async def test_change_password_weak_new(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        with pytest.raises(ValueError):
            await auth_store.change_password(user.id, "pass1234", "ab")

    @pytest.mark.asyncio
    async def test_change_password_nonexistent_user(self, auth_store: SqliteAuthStore) -> None:
        with pytest.raises(ValueError, match="不存在"):
            await auth_store.change_password("fake", "old", "newpass123")

    @pytest.mark.asyncio
    async def test_init_db_idempotent(self, auth_store: SqliteAuthStore) -> None:
        await auth_store.init_db()
        await auth_store.init_db()
        assert await auth_store.has_any_user() is False

    @pytest.mark.asyncio
    async def test_change_username_and_password(self, auth_store: SqliteAuthStore) -> None:
        user = await auth_store.create_default_user("pass1234")
        updated = await auth_store.change_username_and_password(
            user.id, "pass1234", "newadmin", "newpass5678",
        )
        assert updated.username == "newadmin"
        assert await auth_store.get_user_by_username("newadmin") is not None
        assert await auth_store.get_user_by_username("mnemosync") is None

    @pytest.mark.asyncio
    async def test_change_username_duplicate_rejected(self, auth_store: SqliteAuthStore) -> None:
        user1 = await auth_store.create_default_user("pass1234")
        # Create a second user to create a real duplicate
        from src.persistence.auth_store import hash_password
        async with auth_store._conn() as db:
            await db.execute(
                "INSERT INTO users (id, username, password_hash, must_change_password, "
                "created_at, updated_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("user2_id", "taken_name", hash_password("other123"), 1,
                 "2025-01-01T00:00:00", "2025-01-01T00:00:00", 1),
            )
            await db.commit()
        with pytest.raises(ValueError, match="占用"):
            await auth_store.change_username_and_password(
                user1.id, "pass1234", "taken_name", "newpass123",
            )
