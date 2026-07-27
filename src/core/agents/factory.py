"""Agent 执行函数: prompt + 工具 + ReAct 循环组装.

角色 → 模型 由 ``MultiForwarder`` + ``RoleResolver`` 解析, 无需显式传 ``model``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.core.agents.base import (
    run_react_loop,
    run_simple_completion,
)
from src.core.agents.prompts import (
    build_memory_analysis_prompt,
    build_prompt_cleaning_user_prompt,
    build_proxy_thinking_prompt,
    build_relationship_analysis_prompt,
    load_prompt_cleaning_system,
)
from src.core.memory.models import CandidateMemory, DecayEvaluation, DecayState, MemoryType
from src.infra.debug_context import use_agent
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

logger = logging.getLogger(__name__)


@dataclass
class MainDialogueResult:
    """主对话 Agent 的完整响应.

    v0.3.0 起保留上游完整 message, 不再压缩为 (content, usage).
    """

    message: dict[str, Any]   # 保留 content, tool_calls, reasoning_content 等
    finish_reason: str | None
    usage: dict[str, Any] | None


@dataclass
class ExpressorConfig:
    """Expressor 表达改写配置."""

    enabled: bool = False
    temperature: float = 0.4
    max_input_length: int = 2000
    min_rewrite_length: int = 10  # 低于此长度不改写


@dataclass
class MemoryAnalysisOutput:
    """记忆分析 Agent 的解析输出."""

    new_memories: list[CandidateMemory]
    decay_evaluations: list[DecayEvaluation]
    raw_output: str
    steps: list

    @property
    def succeeded(self) -> bool:
        return bool(self.raw_output)


def _extract_json(content: str) -> dict | None:
    """从模型输出中提取 JSON, 支持代码围栏与嵌套对象."""
    TRIPLE = "```"
    if TRIPLE in content:
        parts = content.split(TRIPLE)
        if len(parts) >= 3:
            content = parts[-2]
    content = content.strip()

    start = content.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None

    json_str = content[start : end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        import re
        fixed = re.sub(r'(\s*)(\w+)(\s*:)', r'\1"\2"\3', json_str)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


def _parse_candidate(d: dict) -> CandidateMemory:
    memory_type_str = d.get("memory_type", "NORMAL").upper()
    try:
        memory_type = MemoryType(memory_type_str.lower())
    except ValueError:
        memory_type = MemoryType.NORMAL
    return CandidateMemory(
        content=d.get("content", ""),
        role=d.get("role", "user"),
        memory_type=memory_type,
        importance=float(d.get("importance", 0.5)),
        decay_rate=float(d.get("decay_rate", 0.3)),
        emotional_tags=d.get("emotional_tags", []) or [],
        expires_at=None,
        overrides=d.get("overrides"),
        related_to=d.get("related_to", []) or [],
        reasoning=d.get("reasoning", ""),
    )


def _parse_decay_eval(d: dict) -> DecayEvaluation:
    decision_str = d.get("decision", "ACTIVE").upper()
    try:
        decision = DecayState(decision_str.lower())
    except ValueError:
        decision = DecayState.ACTIVE
    return DecayEvaluation(
        memory_id=d.get("memory_id", ""),
        current_priority=float(d.get("current_priority", 0.5)),
        new_priority=float(d.get("new_priority", 0.5)),
        decision=decision,
        factors=d.get("factors", {}) or {},
        reflection=d.get("reflection", ""),
    )


async def run_main_dialogue(
    forwarder: MultiForwarder,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    parallel_tool_calls: bool | None = None,
) -> MainDialogueResult:
    """主对话 Agent: 使用 MAIN 角色候选生成回复.

    保留完整 assistant message 和 finish_reason, 使客户端工具调用能够
    通过 Mnemosync 往返. 客户端工具只传给 MAIN, 不传给内部辅助 Agent.
    """
    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tools"] = tools
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    with use_agent("main_dialogue"):
        resp = await forwarder.chat(
            ModelType.MAIN,
            messages=messages,
            temperature=temperature,
            **kwargs,
        )
    choice = resp["choices"][0]
    message = dict(choice.get("message") or {})
    message.setdefault("role", "assistant")
    message.setdefault("content", None)
    return MainDialogueResult(
        message=message,
        finish_reason=choice.get("finish_reason"),
        usage=resp.get("usage"),
    )


async def run_expressor(
    forwarder: MultiForwarder,
    original_text: str,
    current_speaker: str,
    channel_type: str | None,
    relationship_summary: str,
    *,
    config: ExpressorConfig | None = None,
) -> str:
    """Expressor: 将最终文本改写为适合当前聊天场景的表达.

    只处理最终文本, 不处理 tool_calls 消息. 调用方必须确保:
    - 原文是 MAIN 最终文本 (finish_reason=stop)
    - 原文不含 tool_calls

    Args:
        forwarder: 多候选转发器 (使用 ASSIST 角色, 低成本)
        original_text: 待改写的原始文本
        current_speaker: 当前发言者标签 (如 "马达 | astrbot 486394990")
        channel_type: 会话类型 (group / direct)
        relationship_summary: 关系状态摘要
        config: Expressor 配置; 为 None 时直接使用默认

    Returns:
        改写后的文本; 原文过短或启用失败时返回原文
    """
    cfg = config or ExpressorConfig()
    if not cfg.enabled:
        return original_text
    if len(original_text) < cfg.min_rewrite_length:
        return original_text
    if len(original_text) > cfg.max_input_length:
        # 超过最大输入长度: 截断后半句不改写, 保持语义完整
        original_text = original_text[:cfg.max_input_length]

    from src.core.prompts import get_prompt_store

    tmpl = get_prompt_store().load("expressor")
    prompt = (
        tmpl.replace("__ORIGINAL_TEXT__", original_text)
        .replace("__CURRENT_SPEAKER__", current_speaker)
        .replace("__CHANNEL_TYPE__", "群聊" if channel_type == "group" else "私聊" if channel_type == "direct" else "未标明")
        .replace("__RELATIONSHIP_SUMMARY__", relationship_summary)
    )

    try:
        with use_agent("expressor"):
            content = await run_simple_completion(
                forwarder=forwarder,
                role=ModelType.ASSIST,
                system_prompt="你是一名表达改写助手。将输入文本改写为口语化、适合群聊发送的消息。",
                user_prompt=prompt,
                temperature=cfg.temperature,
            )
        rewritten = content.strip()
        if not rewritten or len(rewritten) > len(original_text) * 2:
            # 空结果或长度异常膨胀: 返回原文
            logger.debug("Expressor 输出异常, 返回原文: %d → %d", len(original_text), len(rewritten))
            return original_text
        return rewritten
    except Exception as e:
        logger.warning("Expressor 改写失败, 返回原文: %s", e)
        return original_text


async def run_memory_analysis(
    forwarder: MultiForwarder,
    source_user: str,
    conversation: str,
    tools: list,
    max_iterations: int = 4,
    *,
    persona_name: str,
    persona_addressing: str,
    user_addressing: str,
    relation_context: str,
    emotion_analysis: str = "",
    current_speaker: str = "未知参与者",
    channel_type: str | None = None,
) -> MemoryAnalysisOutput:
    """记忆分析 Agent: ReAct 循环, 提取候选记忆.

    衰减评估已从此 Agent 移除 —— 由 MemoryLifecycle.run_deterministic_decay() 用确定性公式处理。
    emotion_analysis 由 graph 层预计算, 供 Agent 直接使用。

    persona_name / persona_addressing / user_addressing / relation_context: v0.2.9 起
    透传给 prompt, 让 Agent 用 "哥哥 X" 而不是 "用户 X" 提取记忆.
    """
    user_prompt = build_memory_analysis_prompt(
        source_user=source_user, conversation=conversation,
        persona_name=persona_name,
        persona_addressing=persona_addressing,
        user_addressing=user_addressing,
        relation_context=relation_context,
        emotion_analysis=emotion_analysis,
        current_speaker=current_speaker,
        channel_type=channel_type,
    )
    with use_agent("memory_analysis"):
        result = await run_react_loop(
            forwarder=forwarder, role=ModelType.ASSIST,
            system_prompt="你是记忆分析 Agent。按指令调用工具后输出 JSON。",
            user_prompt=user_prompt, tools=tools, max_iterations=max_iterations,
            temperature=0.2,
        )
    if not result.succeeded:
        logger.warning("记忆分析 ReAct 失败: %s", result.error)
        return MemoryAnalysisOutput(new_memories=[], decay_evaluations=[], raw_output="", steps=result.steps)
    parsed = _extract_json(result.output)
    if parsed is None:
        logger.warning("记忆分析 JSON 解析失败: %s", result.output[:200])
        return MemoryAnalysisOutput(new_memories=[], decay_evaluations=[], raw_output=result.output, steps=result.steps)
    new_memories = [_parse_candidate(d) for d in parsed.get("new_memories", []) or []]
    decay_evals = [_parse_decay_eval(d) for d in parsed.get("decay_evaluations", []) or []]
    new_memories = [m for m in new_memories if m.content.strip()]
    return MemoryAnalysisOutput(new_memories=new_memories, decay_evaluations=decay_evals, raw_output=result.output, steps=result.steps)


@dataclass
class RelationshipAnalysisOutput:
    intimacy_delta: float
    trust_delta: float
    new_relationship_type: str | None
    notes: str
    reasoning: str
    raw_output: str


async def run_relationship_analysis(
    forwarder: MultiForwarder,
    current_relationship: str,
    conversation: str,
    tools: list,
    max_iterations: int = 2,
    *,
    persona_name: str,
    persona_addressing: str,
    user_addressing: str,
    relation_context: str,
    emotion_analysis: str = "",
    current_speaker: str = "未知参与者",
    channel_type: str | None = None,
) -> RelationshipAnalysisOutput:
    """关系分析 Agent: CoT, 调用 emotion_analyzer 后输出亲密度增量.

    persona_name / persona_addressing / user_addressing / relation_context: v0.2.9 起
    透传给 prompt, 让 Agent 用兄妹/主仆等关系基线判断信号, 不再默认助手-用户.
    """
    user_prompt = build_relationship_analysis_prompt(
        current_relationship=current_relationship, conversation=conversation,
        persona_name=persona_name,
        persona_addressing=persona_addressing,
        user_addressing=user_addressing,
        relation_context=relation_context,
        emotion_analysis=emotion_analysis,
        current_speaker=current_speaker,
        channel_type=channel_type,
    )
    try:
        with use_agent("relationship_analysis"):
            result = await run_react_loop(
                forwarder=forwarder, role=ModelType.ASSIST,
                system_prompt="你是关系分析 Agent。读取情绪数据后分析关系信号并输出 JSON。",
                user_prompt=user_prompt, tools=tools, max_iterations=max_iterations,
                temperature=0.2,
            )
        if not result.succeeded:
            logger.warning("关系分析失败: %s", result.error)
            return RelationshipAnalysisOutput(intimacy_delta=0.0, trust_delta=0.0, new_relationship_type=None, notes="", reasoning=result.error or "", raw_output="")
        parsed = _extract_json(result.output) or {}
        return RelationshipAnalysisOutput(
            intimacy_delta=float(parsed.get("intimacy_delta", 0.0)),
            trust_delta=float(parsed.get("trust_delta", 0.0)),
            new_relationship_type=parsed.get("new_relationship_type"),
            notes=parsed.get("notes", ""),
            reasoning=parsed.get("reasoning", ""),
            raw_output=result.output,
        )
    except Exception as e:
        logger.warning("关系分析异常: %s", e)
        return RelationshipAnalysisOutput(intimacy_delta=0.0, trust_delta=0.0, new_relationship_type=None, notes="", reasoning=str(e), raw_output="")


@dataclass
class PromptCleaningOutput:
    """提示词清洗 Agent 的解析输出."""

    clean_prompt: str
    reasoning: str
    raw_output: str
    steps: list


async def run_prompt_cleaning(
    forwarder: MultiForwarder,
    system_message: str,
) -> PromptCleaningOutput:
    """提示词清洗 Agent: 单次调用, 重写系统消息.

    从逐句 ReAct + classify_sentence_type 改为单次 LLM 调用 ——
    直接重写整个 system 消息, 剥离人格描述, 保留功能性指令.

    Args:
        forwarder: 多候选转发器
        system_message: 客户端发来的 system 消息

    Returns:
        PromptCleaningOutput: clean_prompt(重写后的系统消息), reasoning, raw_output, steps
    """
    user_prompt = build_prompt_cleaning_user_prompt(system_message)

    logger.debug("=" * 60)
    logger.debug("🧹 [prompt_cleaning] 开始, 输入长度: %d", len(system_message))

    try:
        with use_agent("prompt_cleaning"):
            content = await run_simple_completion(
                forwarder=forwarder,
                role=ModelType.ASSIST,
                system_prompt=load_prompt_cleaning_system(),
                user_prompt=user_prompt,
                temperature=0.2,
            )
        parsed = _extract_json(content) or {}
        clean_prompt = parsed.get("clean_prompt", "") or ""
        reasoning = parsed.get("reasoning", "")

        logger.debug("  ✅ 清洗完成: 输出长度 %d", len(clean_prompt))
        return PromptCleaningOutput(
            clean_prompt=clean_prompt, reasoning=reasoning,
            raw_output=content, steps=[],
        )
    except Exception as e:
        logger.warning("提示词清洗异常: %s, 降级为全部丢弃", e)
        return PromptCleaningOutput(
            clean_prompt="", reasoning=str(e), raw_output="", steps=[],
        )


async def run_proxy_thinking(
    forwarder: MultiForwarder,
    user_name: str,
    relationship: str,
    memories: str,
    user_message: str,
    tools: list | None = None,
    max_iterations: int = 3,
    channel_type: str | None = None,
) -> str:
    """代理思考 Agent: CoT, 输出推理过程供主对话参考."""
    user_prompt = build_proxy_thinking_prompt(
        user_name=user_name,
        relationship=relationship,
        memories=memories or "（无）",
        user_message=user_message,
        channel_type=channel_type,
    )
    if tools:
        with use_agent("proxy_thinking"):
            result = await run_react_loop(
                forwarder=forwarder, role=ModelType.ASSIST,
                system_prompt="你是代理思考助手。可调用工具检索记忆，然后输出分析。",
                user_prompt=user_prompt, tools=tools, max_iterations=max_iterations,
                temperature=0.3,
            )
        return result.output
    with use_agent("proxy_thinking"):
        return await run_simple_completion(
            forwarder=forwarder, role=ModelType.ASSIST,
            system_prompt="你是代理思考助手。输出供主 AI 参考的推理分析。",
            user_prompt=user_prompt, temperature=0.3,
            extra_body={"enable_thinking": False},
        )
