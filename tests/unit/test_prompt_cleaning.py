"""测试提示词清洗 Agent 的单次重写与降级路径."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from src.api.schemas.forward import ChatMessage
from src.core.agents.factory import PromptCleaningOutput, run_prompt_cleaning
from src.core.agents.prompts.prompt_cleaning import (
    build_prompt_cleaning_user_prompt,
    load_prompt_cleaning_system,
)
from src.infra.llm_service.models import ModelType


def _mock_forwarder(response_content: str) -> AsyncMock:
    forwarder = AsyncMock()
    forwarder.chat.return_value = {
        "choices": [{"message": {"content": response_content}}],
    }
    return forwarder


def test_build_user_prompt_substitutes():
    result = build_prompt_cleaning_user_prompt("你好，我是谁？")

    assert "你好，我是谁？" in result
    assert "__SYSTEM_MESSAGE__" not in result
    assert "=== 客户端 system 消息 ===" in result


def test_prompt_system_is_stable():
    system_prompt = load_prompt_cleaning_system()

    assert "__SYSTEM_MESSAGE__" not in system_prompt
    assert "人格描述" in system_prompt
    assert "功能性指令" in system_prompt


async def test_cleaning_agent_rewrites_system_message_once():
    forwarder = _mock_forwarder(
        json.dumps(
            {
                "clean_prompt": "请用 JSON 格式回复。回复要简洁。",
                "reasoning": "剥离人格描述，保留格式要求",
            },
            ensure_ascii=False,
        )
    )

    result = await run_prompt_cleaning(
        forwarder=forwarder,
        system_message="你是一个傲娇的妹妹。请用 JSON 格式回复。回复要简洁。",
    )

    assert isinstance(result, PromptCleaningOutput)
    assert result.clean_prompt == "请用 JSON 格式回复。回复要简洁。"
    assert result.reasoning == "剥离人格描述，保留格式要求"
    assert result.steps == []
    forwarder.chat.assert_awaited_once()
    call = forwarder.chat.await_args
    assert call.args[0] == ModelType.ASSIST
    assert call.kwargs["temperature"] == 0.2
    assert "傲娇的妹妹" in call.kwargs["messages"][1]["content"]


async def test_cleaning_agent_handles_fenced_json():
    forwarder = _mock_forwarder(
        '```json\n{"clean_prompt":"请使用纯文本","reasoning":"保留格式"}\n```'
    )

    result = await run_prompt_cleaning(forwarder, "你是助手。请使用纯文本。")

    assert result.clean_prompt == "请使用纯文本"
    assert result.reasoning == "保留格式"


async def test_cleaning_agent_handles_empty_input():
    forwarder = _mock_forwarder(
        json.dumps({"clean_prompt": "", "reasoning": "空输入"}, ensure_ascii=False)
    )

    result = await run_prompt_cleaning(forwarder, "")

    assert result.clean_prompt == ""
    assert result.reasoning == "空输入"


async def test_cleaning_agent_degrades_on_forwarder_error():
    forwarder = AsyncMock()
    forwarder.chat.side_effect = RuntimeError("上游超时")

    result = await run_prompt_cleaning(forwarder, "你是一个助手")

    assert result.clean_prompt == ""
    assert result.raw_output == ""
    assert result.steps == []
    assert "上游超时" in result.reasoning


async def test_cleaning_agent_degrades_on_malformed_json():
    forwarder = _mock_forwarder("这不是 JSON")

    result = await run_prompt_cleaning(forwarder, "你是一个助手")

    assert result.clean_prompt == ""
    assert result.reasoning == ""
    assert result.raw_output == "这不是 JSON"


async def test_clean_prompt_can_merge_with_server_persona():
    server_prompt = "你是小夜。"
    forwarder = _mock_forwarder(
        json.dumps(
            {
                "clean_prompt": "请用 JSON 格式回复。回复要简洁。",
                "reasoning": "已剥离客户端人格",
            },
            ensure_ascii=False,
        )
    )

    result = await run_prompt_cleaning(
        forwarder,
        "你是一个傲娇的妹妹。请用 JSON 格式回复。回复要简洁。",
    )
    final_persona = server_prompt
    if result.clean_prompt:
        final_persona += "\n\n" + result.clean_prompt

    assert "小夜" in final_persona
    assert "JSON 格式" in final_persona
    assert "傲娇" not in final_persona


# ────────────────────────────────────────────────────────────────
# 模块化管线分支 (v0.4.1): 缓存命中 / in-flight 复用 / 超时后台 / 跳过
# ────────────────────────────────────────────────────────────────


@pytest.fixture
async def cache_store(tmp_path):
    from src.persistence.prompt_cache_store import PromptCacheStore
    s = PromptCacheStore(str(tmp_path / "pc.db"))
    await s.init_db()
    return s


def _req():
    return SimpleNamespace(app=SimpleNamespace(state=None))


@pytest.mark.asyncio
async def test_module_cache_hit_skips_llm(cache_store) -> None:
    """缓存命中: 直接返回缓存结果, 不调 LLM."""
    import hashlib

    from src.api.routes.forward.dispatch import _prepare_prompt
    from src.persistence.prompt_cache_store import PromptCacheEntry

    text = "# Header\nfunctional instruction"
    h = hashlib.sha256(text.encode()).hexdigest()
    await cache_store.save(PromptCacheEntry(
        frontend="cherry", module_hash=h, module_title="Header",
        module_text=text, clean_prompt="保留的功能指令",
    ))
    messages = [ChatMessage(role="system", content=text), ChatMessage(role="user", content="hi")]

    with (
        patch("src.api.routes.forward.dispatch._get_prompt_cache_store", return_value=cache_store),
        patch("src.api.routes.forward.dispatch._get_prompt_clean_semaphore", return_value=asyncio.Semaphore(4)),
        patch("src.api.routes.forward.dispatch._get_multi_forwarder"),
        patch("src.core.agents.factory.run_prompt_cleaning",
              new=AsyncMock(return_value=SimpleNamespace(clean_prompt="cleaned", reasoning=""))) as m,
    ):
        persona, result = await _prepare_prompt(messages, "BASE", _req(), "cherry")

    assert "保留的功能指令" in persona
    assert result["modules"][0]["cached"] is True
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_module_inflight_dedup(cache_store) -> None:
    """in-flight 复用: 并发清洗同模块只调一次 LLM."""
    from src.api.routes.forward.dispatch import _clean_one_module
    from src.core.agents.cleaning_module import CleaningModule

    mod = CleaningModule(title="Header", text="# Header\ncontent")
    sem = asyncio.Semaphore(10)
    calls = 0

    async def fake_clean(forwarder, system_message):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return SimpleNamespace(clean_prompt="cleaned", reasoning="")

    with patch("src.core.agents.factory.run_prompt_cleaning", fake_clean):
        r1, r2 = await asyncio.gather(
            _clean_one_module(mod, frontend="cherry", skip_titles=set(), cache=cache_store,
                              semaphore=sem, forwarder=object()),
            _clean_one_module(mod, frontend="cherry", skip_titles=set(), cache=cache_store,
                              semaphore=sem, forwarder=object()),
        )

    assert calls == 1
    assert {r1[0], r2[0]} == {"cleaned"}
    assert sum(1 for x in (r1[1], r2[1]) if x.get("inflight")) == 1


@pytest.mark.asyncio
async def test_module_timeout_defer_and_background_cache(cache_store, monkeypatch) -> None:
    """超时: 本次返回空 (丢弃), 后台继续完成后写缓存."""
    from src.api.routes.forward.dispatch import _clean_one_module
    from src.core.agents.cleaning_module import CleaningModule

    monkeypatch.setattr("src.api.routes.forward.dispatch.PROMPT_CLEAN_FRONT_TIMEOUT", 0.05)
    mod = CleaningModule(title="Header", text="# Header\ncontent")
    sem = asyncio.Semaphore(10)

    async def slow_clean(forwarder, system_message):
        await asyncio.sleep(0.3)
        return SimpleNamespace(clean_prompt="cleaned", reasoning="")

    with patch("src.core.agents.factory.run_prompt_cleaning", slow_clean):
        clean, meta = await _clean_one_module(
            mod, frontend="cherry", skip_titles=set(), cache=cache_store,
            semaphore=sem, forwarder=object(),
        )

    assert clean == ""
    assert meta.get("deferred") is True

    # 等待后台写缓存 (轮询)
    for _ in range(20):
        items, total = await cache_store.list_cache()
        if total >= 1:
            break
        await asyncio.sleep(0.05)
    items, total = await cache_store.list_cache()
    assert total == 1
    assert items[0].clean_prompt == "cleaned"


@pytest.mark.asyncio
async def test_module_skip_preserved(cache_store) -> None:
    """跳过配置: 模块原样保留, 不调 LLM."""
    from src.api.routes.forward.dispatch import _prepare_prompt

    text = "# Memory\n保留的记忆模块\n\n# Tools\n工具说明"
    await cache_store.set_skip("cherry", "Memory", True)
    messages = [ChatMessage(role="system", content=text), ChatMessage(role="user", content="hi")]

    with (
        patch("src.api.routes.forward.dispatch._get_prompt_cache_store", return_value=cache_store),
        patch("src.api.routes.forward.dispatch._get_prompt_clean_semaphore", return_value=asyncio.Semaphore(4)),
        patch("src.api.routes.forward.dispatch._get_multi_forwarder"),
        patch("src.core.agents.factory.run_prompt_cleaning",
              new=AsyncMock(return_value=SimpleNamespace(clean_prompt="cleaned", reasoning=""))) as m,
    ):
        persona, result = await _prepare_prompt(messages, "BASE", _req(), "cherry")

    assert "保留的记忆模块" in persona
    metas = {mod["title"]: mod for mod in result["modules"]}
    assert metas["Memory"]["skipped"] is True
    assert "Tools" in metas
    # Memory 跳过不洗, Tools 正常清洗 → 恰好 await 1 次
    assert m.await_count == 1
