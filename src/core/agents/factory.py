"""Agent 执行函数: prompt + 工具 + ReAct 循环组装."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.core.agents.base import (
    ReActResult,
    run_react_loop,
    run_simple_completion,
)
from src.core.agents.prompts import (
    DECAY_TARGETS_HEADER,
    PROMPT_CLEANING_SYSTEM,
    PROXY_THINKING_PROMPT,
    build_memory_analysis_prompt,
    build_prompt_cleaning_user_prompt,
    build_relationship_analysis_prompt,
)
from src.core.config import get_settings
from src.core.memory.models import CandidateMemory, DecayEvaluation, DecayState, MemoryType
from src.infra import Forwarder

logger = logging.getLogger(__name__)


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
    """从模型输出中提取 JSON, 支持修复常见格式问题."""
    TRIPLE = "```"
    if TRIPLE in content:
        parts = content.split(TRIPLE)
        if len(parts) >= 2:
            content = parts[-2]
    content = content.strip()
    
    # 移除可能的 JSON 前缀/后缀文本
    lines = content.split("\n")
    json_lines = []
    in_json = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("{") or in_json:
            in_json = True
            json_lines.append(line)
            if stripped.endswith("}"):
                break
    
    if json_lines:
        content = "\n".join(json_lines)
    
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    
    json_str = content[start : end + 1]
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # 尝试修复常见问题: 缺少引号的键
        import re
        # 给没有引号的键加上引号
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
    forwarder: Forwarder,
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 0.7,
) -> tuple[str, dict[str, Any] | None]:
    """主对话 Agent: 直接调用主模型生成回复.

    Returns:
        (content, usage) — content 为回复文本, usage 为上游原样返回的 token 计数字典
        (可能为 None, 例如上游未返回 usage 段).
    """
    settings = get_settings()
    model = model or settings.chat.main_model
    resp = await forwarder.chat(
        messages=messages, model=model, temperature=temperature,
    )
    content = resp["choices"][0]["message"]["content"] or ""
    usage = resp.get("usage")
    return content, usage


async def run_memory_analysis(
    forwarder: Forwarder,
    source_user: str,
    conversation: str,
    tools: list,
    decay_targets: list[dict] | None = None,
    max_iterations: int = 6,
) -> MemoryAnalysisOutput:
    """记忆分析 Agent: ReAct 循环, 提取候选 + 衰减评估."""
    settings = get_settings()
    decay_section = ""
    if decay_targets:
        lines = []
        for t in decay_targets:
            lines.append(
                f"- memory_id: {t['memory_id']}, content: {t['content']}, "
                f"importance: {t.get('importance', 0.5)}, decay_rate: {t.get('decay_rate', 0.3)}, "
                f"memory_type: {t.get('memory_type', 'normal')}"
            )
        decay_section = DECAY_TARGETS_HEADER + "\n".join(lines) + "\n"
    user_prompt = build_memory_analysis_prompt(
        source_user=source_user, conversation=conversation,
        decay_targets_section=decay_section,
    )
    result = await run_react_loop(
        forwarder=forwarder, model=settings.chat.assist_model,
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
    forwarder: Forwarder,
    current_relationship: str,
    conversation: str,
    tools: list,
    max_iterations: int = 3,
) -> RelationshipAnalysisOutput:
    """关系分析 Agent: CoT, 调用 emotion_analyzer 后输出亲密度增量."""
    settings = get_settings()
    user_prompt = build_relationship_analysis_prompt(
        current_relationship=current_relationship, conversation=conversation,
    )
    try:
        result = await run_react_loop(
            forwarder=forwarder, model=settings.chat.assist_model,
            system_prompt="你是关系分析 Agent。调用 emotion_analyzer 后输出 JSON。",
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

    retained: list[str]   # 保留的功能性指令
    discarded: list[str]  # 丢弃的人格描述
    reasoning: str        # 分类理由
    raw_output: str       # 原始输出
    steps: list           # ReAct 步骤


async def run_prompt_cleaning(
    forwarder: Forwarder,
    system_message: str,
    tools: list,
    max_iterations: int = 3,
) -> PromptCleaningOutput:
    """提示词清洗 Agent: ReAct 循环, 分离人格描述与功能性指令.

    Args:
        forwarder: 上游转发器
        system_message: 客户端发来的 system 消息
        tools: 工具列表 (应包含 classify_sentence_type)
        max_iterations: ReAct 最大迭代轮数

    Returns:
        PromptCleaningOutput: retained(保留的指令), discarded(丢弃的人格), reasoning, raw_output, steps
    """
    settings = get_settings()
    user_prompt = build_prompt_cleaning_user_prompt(system_message)

    logger.debug("=" * 60)
    logger.debug("🧹 [prompt_cleaning] 开始, 输入长度: %d", len(system_message))

    try:
        result = await run_react_loop(
            forwarder=forwarder,
            model=settings.chat.assist_model,
            system_prompt=PROMPT_CLEANING_SYSTEM,
            user_prompt=user_prompt,
            tools=tools,
            max_iterations=max_iterations,
            temperature=0.2,
        )
        if not result.succeeded:
            logger.warning("提示词清洗 ReAct 失败: %s, 降级为全部丢弃", result.error)
            return PromptCleaningOutput(
                retained=[], discarded=[system_message] if system_message else [],
                reasoning=f"清洗失败: {result.error}", raw_output="", steps=result.steps,
            )
        parsed = _extract_json(result.output) or {}
        retained = parsed.get("retained", []) or []
        discarded = parsed.get("discarded", []) or []
        reasoning = parsed.get("reasoning", "")

        logger.debug("  ✅ 清洗完成: 保留 %d 条指令, 丢弃 %d 条人格描述", len(retained), len(discarded))
        return PromptCleaningOutput(
            retained=retained, discarded=discarded,
            reasoning=reasoning, raw_output=result.output, steps=result.steps,
        )
    except Exception as e:
        logger.warning("提示词清洗异常: %s, 降级为全部丢弃", e)
        return PromptCleaningOutput(
            retained=[], discarded=[system_message] if system_message else [],
            reasoning=str(e), raw_output="", steps=[],
        )


async def run_proxy_thinking(
    forwarder: Forwarder,
    user_name: str,
    relationship: str,
    memories: str,
    user_message: str,
    tools: list | None = None,
    max_iterations: int = 3,
) -> str:
    """代理思考 Agent: CoT, 输出推理过程供主对话参考."""
    settings = get_settings()
    user_prompt = PROXY_THINKING_PROMPT.format(
        user_name=user_name, relationship=relationship,
        memories=memories or "（无）", user_message=user_message,
    )
    if tools:
        result = await run_react_loop(
            forwarder=forwarder, model=settings.chat.assist_model,
            system_prompt="你是代理思考助手。可调用工具检索记忆，然后输出分析。",
            user_prompt=user_prompt, tools=tools, max_iterations=max_iterations,
            temperature=0.3,
        )
        return result.output
    return await run_simple_completion(
        forwarder=forwarder, model=settings.chat.assist_model,
        system_prompt="你是代理思考助手。输出供主 AI 参考的推理分析。",
        user_prompt=user_prompt, temperature=0.3,
        extra_body={"enable_thinking": False},
    )