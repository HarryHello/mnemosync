"""身份绑定指令处理测试.

覆盖 _handle_identity_binding 的三种分支:
1. 绑定指令 (发起绑定)
2. 确认指令 (确认绑定)
3. 非绑定消息 (返回 None)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_request():
    """Mock HTTP request."""
    req = MagicMock()
    req.app.state.resolver = None
    req.app.state.identity_store = MagicMock()
    req.app.state.relationship_store = MagicMock()
    return req


@pytest.fixture
def mock_settings():
    """Mock settings with bind commands."""
    settings = MagicMock()
    settings.runtime.identity_bind_command = "绑定"
    settings.runtime.identity_bind_confirm_prefix = "绑定"
    return settings


@pytest.mark.asyncio
async def test_no_actor_id_returns_none(mock_request, mock_settings):
    """无 actor_id 时返回 None."""
    from src.api.routes.forward.dispatch import _handle_identity_binding

    with patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), [], None, None, "用户",
        )
    assert result is None


@pytest.mark.asyncio
async def test_non_bind_message_returns_none(mock_request, mock_settings):
    """非绑定消息返回 None."""
    from src.api.routes.forward.dispatch import _handle_identity_binding

    messages = [{"role": "user", "content": "你好"}]

    with patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", None, "用户",
        )
    assert result is None


@pytest.mark.asyncio
async def test_bind_command_returns_bind_context(mock_request, mock_settings):
    """绑定指令返回 BindContext."""
    from src.api.routes.forward.dispatch import BindContext, _handle_identity_binding

    messages = [{"role": "user", "content": "绑定"}]

    mock_code_store = AsyncMock()
    mock_code_store.generate = AsyncMock(return_value="123456")

    with (
        patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings),
        patch("src.core.tools.identity_binding.get_binding_code_store", return_value=mock_code_store),
    ):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", "space-1", "用户",
        )

    assert result is not None
    assert isinstance(result, BindContext)
    assert result.kind == "initiate"
    assert result.success is True
    assert result.code == "123456"
    assert "123456" in result.prompt_hint


@pytest.mark.asyncio
async def test_bind_confirm_with_valid_code(mock_request, mock_settings):
    """有效验证码确认绑定."""
    from src.api.routes.forward.dispatch import BindContext, _handle_identity_binding

    messages = [{"role": "user", "content": "绑定 123456"}]

    mock_code_store = AsyncMock()
    mock_code_store.verify = AsyncMock(return_value={
        "actor_id": "target-actor",
        "space_id": "space-1",
        "display_name": "Alice",
    })

    mock_identity_store = AsyncMock()
    mock_identity_store.list_actor_groups = AsyncMock(return_value=[])
    mock_identity_store.create_group = AsyncMock(return_value=MagicMock(id="group-1"))
    mock_identity_store.bind_actor_to_group = AsyncMock()

    mock_request.app.state.identity_store = mock_identity_store

    with (
        patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings),
        patch("src.core.tools.identity_binding.get_binding_code_store", return_value=mock_code_store),
        patch("src.api.routes.forward.dispatch._get_identity_store", return_value=mock_identity_store),
        patch("src.api.routes.forward.dispatch._migrate_relationships", new=AsyncMock(return_value=0)),
    ):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", "space-1", "用户",
        )

    assert result is not None
    assert isinstance(result, BindContext)
    assert result.kind == "confirm"
    assert result.success is True
    assert "绑定成功" in result.prompt_hint


@pytest.mark.asyncio
async def test_bind_confirm_with_invalid_code(mock_request, mock_settings):
    """无效验证码返回错误."""
    from src.api.routes.forward.dispatch import BindContext, _handle_identity_binding

    messages = [{"role": "user", "content": "绑定 999999"}]

    mock_code_store = AsyncMock()
    mock_code_store.verify = AsyncMock(return_value=None)

    with (
        patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings),
        patch("src.core.tools.identity_binding.get_binding_code_store", return_value=mock_code_store),
    ):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", "space-1", "用户",
        )

    assert result is not None
    assert isinstance(result, BindContext)
    assert result.success is False
    assert "无效" in result.prompt_hint or "过期" in result.prompt_hint


@pytest.mark.asyncio
async def test_bind_confirm_already_bound(mock_request, mock_settings):
    """已绑定时返回提示."""
    from src.api.routes.forward.dispatch import BindContext, _handle_identity_binding

    messages = [{"role": "user", "content": "绑定 123456"}]

    mock_code_store = AsyncMock()
    mock_code_store.verify = AsyncMock(return_value={
        "actor_id": "target-actor", "space_id": None, "display_name": "Alice",
    })

    mock_identity_store = AsyncMock()
    mock_identity_store.list_actor_groups = AsyncMock(return_value=[MagicMock(id="existing-group")])

    mock_request.app.state.identity_store = mock_identity_store

    with (
        patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings),
        patch("src.core.tools.identity_binding.get_binding_code_store", return_value=mock_code_store),
        patch("src.api.routes.forward.dispatch._get_identity_store", return_value=mock_identity_store),
    ):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", "space-1", "用户",
        )

    assert result is not None
    assert isinstance(result, BindContext)
    assert result.success is False
    assert "绑定" in result.prompt_hint


@pytest.mark.asyncio
async def test_bind_confirm_self_bind_rejected(mock_request, mock_settings):
    """不能与自身绑定."""
    from src.api.routes.forward.dispatch import BindContext, _handle_identity_binding

    messages = [{"role": "user", "content": "绑定 123456"}]

    mock_code_store = AsyncMock()
    mock_code_store.verify = AsyncMock(return_value={
        "actor_id": "actor-1",  # same as the requesting actor
        "space_id": "space-1",
        "display_name": "Self",
    })

    with (
        patch("src.api.routes.forward.dispatch.get_settings", return_value=mock_settings),
        patch("src.core.tools.identity_binding.get_binding_code_store", return_value=mock_code_store),
        patch("src.api.routes.forward.dispatch._get_identity_store", return_value=MagicMock()),
    ):
        result = await _handle_identity_binding(
            mock_request, MagicMock(), messages, "actor-1", "space-1", "用户",
        )

    assert result is not None
    assert isinstance(result, BindContext)
    assert result.success is False
    assert "自身" in result.prompt_hint
