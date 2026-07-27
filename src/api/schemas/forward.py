"""OpenAI 兼容的 Schema 定义."""

import time
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# ============== Models API ==============

class ModelInfo(BaseModel):
    """模型信息."""
    id: str = Field(..., description="模型 ID")
    object: Literal["model"] = "model"
    created: int = Field(default_factory=lambda: int(time.time()), description="创建时间戳")
    owned_by: str = Field(..., description="模型所有者")


class ModelList(BaseModel):
    """模型列表响应."""
    object: Literal["list"] = "list"
    data: list[ModelInfo] = Field(default_factory=list, description="模型列表")


# ============== Chat Completions API ==============

class ChatMessage(BaseModel):
    """聊天消息."""
    role: Literal["system","developer", "user", "assistant", "tool"] = Field(..., description="消息角色")
    content: Optional[str | list[dict[str, Any]]] = Field(None, description="消息内容 (支持 OpenAI content parts 数组格式)")
    reasoning_content: Optional[str] = Field(None, description="推理过程 (OpenAI 兼容扩展, DeepSeek/Qwen 等)")
    name: Optional[str] = Field(None, description="消息名称")
    tool_calls: Optional[list[dict]] = Field(None, description="工具调用")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID")


class ChatCompletionRequest(BaseModel):
    """聊天补全请求."""
    model: str = Field(..., description="使用的模型 ID")
    messages: list[ChatMessage] = Field(..., description="消息列表")
    
    # 可选参数
    frequency_penalty: Optional[float] = Field(0, ge=-2, le=2, description="频率惩罚")
    logit_bias: Optional[dict[str, float]] = Field(None, description="Logit 偏置")
    logprobs: Optional[bool] = Field(False, description="是否返回 logprobs")
    top_logprobs: Optional[int] = Field(None, ge=0, le=20, description="返回的 logprobs 数量")
    max_tokens: Optional[int] = Field(None, ge=1, description="最大生成 token 数")
    n: Optional[int] = Field(1, ge=1, le=128, description="生成多少个补全")
    presence_penalty: Optional[float] = Field(0, ge=-2, le=2, description="存在性惩罚")
    response_format: Optional[dict] = Field(None, description="响应格式")
    seed: Optional[int] = Field(None, description="随机种子")
    service_tier: Optional[str] = Field(None, description="服务层级")
    stop: Optional[str | list[str]] = Field(None, description="停止序列")
    stream: Optional[bool] = Field(False, description="是否流式输出")
    stream_options: Optional[dict] = Field(None, description="流式选项")
    temperature: Optional[float] = Field(1, ge=0, le=2, description="温度")
    top_p: Optional[float] = Field(1, ge=0, le=1, description="Top-p 采样")
    tools: Optional[list[dict]] = Field(None, description="工具列表")
    tool_choice: Optional[str | dict] = Field(None, description="工具选择")
    parallel_tool_calls: Optional[bool] = Field(True, description="是否并行工具调用")
    user: Optional[str] = Field(None, description="用户 ID")

    # 推理触发字段 (前台显式要求原生推理时提交, 命中即跳过代理推理)
    reasoning_effort: Optional[str] = Field(None, description="OpenAI o-series 推理力度")
    reasoning: Optional[dict] = Field(None, description="OpenAI Responses 风格 reasoning 参数")
    thinking: Optional[dict] = Field(None, description="Anthropic / Qwen thinking 参数")


class ChatCompletionChoice(BaseModel):
    """聊天补全选项."""
    index: int = Field(..., description="选项索引")
    message: ChatMessage = Field(..., description="回复消息")
    logprobs: Optional[dict] = Field(None, description="Logprobs")
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter", "function_call"]] = Field(
        None, description="结束原因"
    )


class UsageInfo(BaseModel):
    """Token 使用信息."""
    prompt_tokens: int = Field(..., description="提示词 token 数")
    completion_tokens: int = Field(..., description="补全 token 数")
    total_tokens: int = Field(..., description="总 token 数")


class ChatCompletionResponse(BaseModel):
    """聊天补全响应."""
    id: str = Field(..., description="响应 ID")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()), description="创建时间戳")
    model: str = Field(..., description="使用的模型")
    choices: list[ChatCompletionChoice] = Field(..., description="补全选项")
    usage: Optional[UsageInfo] = Field(None, description="Token 使用信息")
    system_fingerprint: Optional[str] = Field(None, description="系统指纹")


# ============== Stream Response ==============

class ChatCompletionChunkChoice(BaseModel):
    """聊天补全分块选项."""
    index: int = Field(..., description="选项索引")
    delta: ChatMessage = Field(..., description="消息增量")
    logprobs: Optional[dict] = Field(None, description="Logprobs")
    finish_reason: Optional[str] = Field(None, description="结束原因")


class ChatCompletionChunk(BaseModel):
    """聊天补全分块响应 (流式)."""
    id: str = Field(..., description="响应 ID")
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()), description="创建时间戳")
    model: str = Field(..., description="使用的模型")
    choices: list[ChatCompletionChunkChoice] = Field(..., description="补全选项")
    system_fingerprint: Optional[str] = Field(None, description="系统指纹")
