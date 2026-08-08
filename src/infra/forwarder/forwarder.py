"""Forwarder: 所有模型调用的唯一 HTTP 出口.

负责:
- 对话调用 (chat/completions) — 流式 + 非流式 (via openai SDK)
- 嵌入调用 (embeddings) (via openai SDK)
- 重排序调用 (rerank) (via httpx, 无 SDK 支持)

不负责任何智能决策, 只做 HTTP 转发 + 错误处理.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .connection_pool import ConnectionPool
from .debug_hook import get_debug_bus
from .debug_utils import emit_upstream_debug as _emit_debug  # noqa: F401

logger = logging.getLogger(__name__)


def _log_upstream(direction: str, base_url: str, data: Any, status: int | None = None) -> None:
    """记录上游请求/响应到日志 (DEBUG 级别).

    仅当 MNEMOSYNC_DEBUG=1 时输出, 与 serve --debug 共用同一开关.
    """
    if os.getenv("MNEMOSYNC_DEBUG") != "1":
        return
    if isinstance(data, dict) or isinstance(data, list):
        data_str = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        data_str = str(data)
    status_str = str(status) if status is not None else "-"
    logger.debug(
        "[UPSTREAM %s] %s\nStatus: %s\nData: %s",
        direction,
        base_url,
        status_str,
        data_str[:2000],
    )


class UpstreamError(Exception):
    """上游服务错误."""

    def __init__(self, status_code: int | None = None, message: str = ""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Upstream error {status_code}: {message}")


class UpstreamTimeout(Exception):
    """上游服务超时."""


@dataclass
class ForwarderConfig:
    """转发器配置."""

    base_url: str
    api_key: str
    default_model: str = ""
    timeout: float = 60.0
    connect_timeout: float = 10.0


class Forwarder:
    """上游模型转发器.

    所有 Agent 通过本类调用模型服务商. 一个 Forwarder 实例对应一个服务商.
    使用 openai SDK 调用 Chat Completions 和 Embeddings; rerank 仍用 httpx.
    """

    def __init__(self, config: ForwarderConfig, pool: ConnectionPool | None = None):
        self.config = config
        self._pool = pool
        self._client: httpx.AsyncClient | None = None
        self._openai_client: AsyncOpenAI | None = None

    def _get_openai_client(self) -> AsyncOpenAI:
        if self._openai_client is None:
            self._openai_client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=httpx.Timeout(
                    self.config.timeout, connect=self.config.connect_timeout
                ),
                max_retries=0,
            )
        return self._openai_client

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取 httpx 客户端 (仅用于 rerank 等无 SDK 支持的调用)."""
        if self._client is None:
            if self._pool:
                self._client = await self._pool.get_client()
            else:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self.config.timeout, connect=self.config.connect_timeout
                    ),
                    follow_redirects=False,
                )
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    # ============ 对话 ============

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """非流式对话. 使用 openai SDK."""
        client = self._get_openai_client()
        resolved_model = model or self.config.default_model
        chat_url = f"{self.config.base_url}/chat/completions"

        # 构建 SDK 参数
        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            sdk_kwargs["max_tokens"] = max_tokens
        if tools:
            sdk_kwargs["tools"] = tools
        if tool_choice is not None:
            sdk_kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            sdk_kwargs["response_format"] = response_format
        # extra_body 中的字段通过 extra_body 参数传递给 SDK
        if extra_body:
            sdk_kwargs["extra_body"] = extra_body
        # 透传其他 kwargs (如 reasoning_effort, thinking 等)
        for k, v in kwargs.items():
            if k not in sdk_kwargs:
                sdk_kwargs[k] = v

        # Debug: 记录上游请求
        _log_upstream("REQUEST", self.config.base_url, sdk_kwargs)
        _emit_debug("upstream_request", chat_url, method="POST", body=sdk_kwargs)

        started = time.time()
        try:
            response = await client.chat.completions.create(**sdk_kwargs)
            result = response.model_dump()

            # Debug: 记录上游响应
            _log_upstream("RESPONSE", self.config.base_url, result, status=200)
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=200,
                duration_ms=(time.time() - started) * 1000,
                body=result,
            )

            return cast(dict[str, Any], result)
        except APITimeoutError as e:
            _log_upstream("TIMEOUT", self.config.base_url, {"error": str(e)})
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=None,
                duration_ms=(time.time() - started) * 1000,
                body={"error": f"timeout: {e}"},
            )
            raise UpstreamTimeout(f"chat timeout after {self.config.timeout}s") from e
        except APIConnectionError as e:
            _log_upstream("ERROR", self.config.base_url, {"error": str(e)})
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=None,
                duration_ms=(time.time() - started) * 1000,
                body={"error": str(e)},
            )
            raise UpstreamError(None, str(e)) from e
        except APIStatusError as e:
            _log_upstream("ERROR", self.config.base_url, {"error": e.response.text}, status=e.status_code)
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=e.status_code,
                duration_ms=(time.time() - started) * 1000,
                body={"error": e.response.text},
            )
            raise UpstreamError(e.status_code, e.response.text) from e

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """流式对话, yield SSE 原始字节.

        使用 openai SDK 流式接口, 将每个 chunk 转回 OpenAI SSE 格式的原始字节,
        以兼容下游的 ``parse_sse_stream_full`` 解析链.
        """
        client = self._get_openai_client()
        resolved_model = model or self.config.default_model
        stream_url = f"{self.config.base_url}/chat/completions"

        # 构建 SDK 参数
        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            sdk_kwargs["max_tokens"] = max_tokens
        # 透传 kwargs
        for k, v in kwargs.items():
            if k not in sdk_kwargs:
                sdk_kwargs[k] = v

        # Debug: 记录上游请求
        _log_upstream("REQUEST (STREAM)", self.config.base_url, sdk_kwargs)
        event_id = _emit_debug(
            "upstream_request", stream_url, method="POST", body=sdk_kwargs
        )

        bus = get_debug_bus()
        started = time.time()
        collected_text_parts: list[str] = []
        try:
            stream = await client.chat.completions.create(**sdk_kwargs)
            async for chunk in stream:
                # 将 SDK chunk 转回 OpenAI SSE 格式的原始字节
                chunk_dict = chunk.model_dump()
                sse_line = f"data: {json.dumps(chunk_dict, ensure_ascii=False)}\n\n"
                sse_bytes = sse_line.encode("utf-8")

                if event_id and bus is not None and bus.should_emit():
                    bus.append_stream_chunk(event_id, sse_bytes)
                    try:
                        collected_text_parts.append(sse_line)
                    except Exception:
                        pass
                yield sse_bytes

            # 发送 [DONE] 标记
            yield b"data: [DONE]\n\n"

            if event_id and bus is not None:
                assembled = parse_sse_stream([c.encode("utf-8") for c in collected_text_parts]) if collected_text_parts else ""
                bus.finalize_stream(
                    event_id,
                    assembled=assembled,
                    status=200,
                    duration_ms=(time.time() - started) * 1000,
                )
        except APITimeoutError as e:
            _log_upstream("TIMEOUT", self.config.base_url, {"error": str(e)})
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id,
                    assembled=f"timeout: {e}",
                    status=None,
                    duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamTimeout(f"chat_stream timeout after {self.config.timeout}s") from e
        except APIConnectionError as e:
            _log_upstream("ERROR", self.config.base_url, {"error": str(e)})
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id,
                    assembled=str(e),
                    status=None,
                    duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamError(None, str(e)) from e
        except APIStatusError as e:
            body = e.response.text
            _log_upstream("ERROR", self.config.base_url, {"error": body}, status=e.status_code)
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id,
                    assembled=body,
                    status=e.status_code,
                    duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamError(e.status_code, body) from e

    # ============ 嵌入 ============

    async def embed(
        self,
        input: str | list[str],
        model: str,
        dimensions: int | None = None,
    ) -> list[list[float]]:
        """调用 embedding API, 返回向量列表. 使用 openai SDK."""
        client = self._get_openai_client()
        embed_url = f"{self.config.base_url}/embeddings"

        sdk_kwargs: dict[str, Any] = {"model": model, "input": input}
        if dimensions is not None:
            sdk_kwargs["dimensions"] = dimensions

        _emit_debug("upstream_request", embed_url, method="POST", body=sdk_kwargs)
        started = time.time()
        try:
            response = await client.embeddings.create(**sdk_kwargs)
            # 按 index 排序
            items = sorted(response.data, key=lambda x: x.index)
            vectors = [item.embedding for item in items]
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=200,
                duration_ms=(time.time() - started) * 1000,
                body={"vectors_count": len(vectors), "dim": len(vectors[0]) if vectors else 0},
            )
            return vectors
        except APITimeoutError as e:
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=None,
                duration_ms=(time.time() - started) * 1000,
                body={"error": f"timeout: {e}"},
            )
            raise UpstreamTimeout("embed timeout") from e
        except APIStatusError as e:
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=e.status_code,
                duration_ms=(time.time() - started) * 1000,
                body={"error": e.response.text},
            )
            raise UpstreamError(e.status_code, e.response.text) from e

    # ============ 重排序 ============

    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str,
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """调用 rerank API (httpx, 无 SDK 支持)."""
        payload: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        client = await self._get_http_client()
        for endpoint in ["/rerank", "/reranks"]:
            try:
                resp = await client.post(
                    f"{self.config.base_url}{endpoint}",
                    json=payload,
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                for r in results:
                    if "document" not in r or r.get("document") is None:
                        r["document"] = documents[r["index"]]
                return cast(list[dict[str, Any]], results)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404 and endpoint == "/rerank":
                    continue
                raise UpstreamError(e.response.status_code, e.response.text) from e
            except httpx.TimeoutException as e:
                raise UpstreamTimeout("rerank timeout") from e
        raise UpstreamError(404, "rerank endpoint not found (/rerank and /reranks both failed)")

    # ============ 模型列表 ============

    async def list_models(self) -> list[str]:
        """列出服务商可用模型. 使用 openai SDK."""
        client = self._get_openai_client()
        try:
            models = await client.models.list()
            return [m.id for m in models.data]
        except APIStatusError as e:
            raise UpstreamError(e.status_code, e.response.text) from e

    # ============ 生命周期 ============

    async def close(self) -> None:
        """关闭 HTTP 客户端."""
        if self._openai_client:
            await self._openai_client.close()
            self._openai_client = None
        if self._client and not self._pool:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Forwarder:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()


@dataclass
class StreamResult:
    """流式 SSE 累积结果.

    v0.3.0 起支持 tool_calls 累积, 用于纯工具调用响应的检测和持久化.
    """

    text: str
    tool_calls: list[dict[str, Any]] | None  # 累积合并后的完整 tool_calls; None = 无工具调用
    finish_reason: str | None


def parse_sse_stream(chunks: list[bytes]) -> str:
    """从 SSE 字节块列表中拼接出完整 assistant 文本内容.

    用于流式响应的异步存储. 只返回文本, 不返回 tool_calls.
    如需完整累积结果 (含工具调用), 使用 ``parse_sse_stream_full``.
    """
    return parse_sse_stream_full(chunks).text


def parse_sse_stream_full(chunks: list[bytes]) -> StreamResult:
    """从 SSE 字节块列表中累积完整 assistant 内容与工具调用.

    同一 index 的 function.arguments 按帧顺序拼接, 处理跨帧分片.
    """
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None

    # HTTP 分块边界可能切在 JSON 或 UTF-8 字符中; 先合并原始字节再解码.
    text = b"".join(chunks).decode("utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
            choices = parsed.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])
            # 累积 tool_calls
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    }
                existing = tool_calls[idx]
                func = tc.get("function", {})
                if func.get("name"):
                    existing["function"]["name"] = (
                        existing["function"]["name"] + func["name"]
                    )
                if func.get("arguments"):
                    existing["function"]["arguments"] = (
                        existing["function"]["arguments"] + func["arguments"]
                    )
                if tc.get("id"):
                    existing["id"] = tc["id"]
            # finish_reason 只在最后一条 choice 出现
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

    result_tool_calls: list[dict[str, Any]] | None = None
    if tool_calls:
        result_tool_calls = [tool_calls[i] for i in sorted(tool_calls)]

    return StreamResult(
        text="".join(content_parts),
        tool_calls=result_tool_calls,
        finish_reason=finish_reason,
    )
