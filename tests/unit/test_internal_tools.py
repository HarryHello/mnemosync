"""内部 tool 注册表 + 身份绑定 tool 测试."""

import pytest
from src.core.tools.identity_binding import (
    BindingCodeStore,
    get_binding_code_store,
    register_identity_binding_tools,
)
from src.core.tools.internal_registry import InternalTool, InternalToolRegistry


def test_registry_register_and_get():
    async def handler(**kwargs):
        return {"ok": True}

    reg = InternalToolRegistry()
    tool = InternalTool(
        name="test_tool",
        description="test",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )
    reg.register(tool)
    assert reg.get("test_tool") is tool
    assert "test_tool" in reg.names
    assert not reg.is_empty()
    assert len(reg.to_openai_tools()) == 1
    assert reg.to_openai_tools()[0]["function"]["name"] == "test_tool"


def test_registry_empty():
    reg = InternalToolRegistry()
    assert reg.is_empty()
    assert reg.get("nonexistent") is None
    assert reg.to_openai_tools() == []


@pytest.mark.asyncio
async def test_binding_code_generate_and_verify():
    store = BindingCodeStore()
    code = await store.generate(actor_id="actor-A", space_id="space-1", display_name="UserA")
    assert len(code) == 6
    assert code.isdigit()

    entry = await store.verify(code)
    assert entry is not None
    assert entry["actor_id"] == "actor-A"

    # 验证后码应被消费
    entry2 = await store.verify(code)
    assert entry2 is None


@pytest.mark.asyncio
async def test_binding_code_invalid():
    store = BindingCodeStore()
    entry = await store.verify("000000")
    assert entry is None


@pytest.mark.asyncio
async def test_binding_code_self_bind_rejected():
    """不能与自身绑定."""
    from src.core.tools.identity_binding import _handle_confirm_binding

    store = get_binding_code_store()
    code = await store.generate(actor_id="actor-A", space_id=None, display_name=None)

    result = await _handle_confirm_binding(
        code=code, actor_id="actor-A", identity_store=None,
    )
    assert not result["success"]
    assert "自身" in result["error"]


@pytest.mark.asyncio
async def test_binding_code_missing_actor():
    """无 actor_id 时应失败."""
    from src.core.tools.identity_binding import _handle_initiate_binding

    result = await _handle_initiate_binding(
        actor_id=None, space_id=None, display_name=None, identity_store=None,
    )
    assert not result["success"]


def test_register_identity_binding_tools():
    reg = InternalToolRegistry()
    register_identity_binding_tools(reg)
    assert "initiate_identity_binding" in reg.names
    assert "confirm_identity_binding" in reg.names
    tools = reg.to_openai_tools()
    assert len(tools) == 2
    # confirm tool 应有 code 参数
    confirm_tool = next(t for t in tools if t["function"]["name"] == "confirm_identity_binding")
    assert "code" in confirm_tool["function"]["parameters"]["properties"]
    assert "code" in confirm_tool["function"]["parameters"]["required"]
