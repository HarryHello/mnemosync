"""测试提示词清洗 Agent 的单次重写与降级路径."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

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
