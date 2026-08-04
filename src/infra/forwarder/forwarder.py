"""Forwarder: 所有模型调用的唯一 HTTP 出口.

负责:
- 对话调用 (chat/completions) — 流式 + 非流式
- 嵌入调用 (embeddings)
- 重排序调用 (rerank)

不负责任何智能决策，只做 HTTP 转发 + 错误处理.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from src.infra.debug_context import get_agent_name, get_correlation_id

from .connection_pool import ConnectionPool
from .debug_hook import get_debug_bus

logger = logging.getLogger(__name__)


def _emit_debug(direction: str, url: str, **fields) -> str | None:
    bus = get_debug_bus()
    if bus is None or not bus.should_emit():
        return None
    cid = get_correlation_id() or "no-cid"
    agent = get_agent_name()
    parsed = urlparse(url)
    port = parsed.port
    return bus.emit(
        direction=direction,
        correlation_id=cid,
        url=url,
        port=port,
        agent=agent,
        **fields,
    )


def _log_upstream(direction: str, base_url: str, data: Any, status: int | None = None):
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
    """

    def __init__(self, config: ForwarderConfig, pool: ConnectionPool | None = None):
        self.config = config
        self._pool = pool
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
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
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        extra_body: dict | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """非流式对话. 支持 function_call.

        注意: DashScope 等服务商 tools 与 stream=True 互斥, 本方法非流式可安全用 tools.

        Args:
            extra_body: 额外请求体字段（如 {"enable_thinking": False} 关闭 Qwen3 思考）
        """
        payload: dict[str, Any] = {
            "model": model or self.config.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if extra_body:
            payload.update(extra_body)
        payload.update(kwargs)

        client = await self._get_client()
        chat_url = f"{self.config.base_url}/chat/completions"

        # Debug: 记录上游请求
        _log_upstream("REQUEST", self.config.base_url, payload)
        _emit_debug("upstream_request", chat_url, method="POST", body=payload)

        started = time.time()
        try:
            resp = await client.post(
                chat_url,
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            result = resp.json()

            # Debug: 记录上游响应
            _log_upstream("RESPONSE", self.config.base_url, result, status=resp.status_code)
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=resp.status_code,
                duration_ms=(time.time() - started) * 1000,
                body=result,
            )

            return result
        except httpx.HTTPStatusError as e:
            _log_upstream("ERROR", self.config.base_url, {"error": e.response.text}, status=e.response.status_code)
            _emit_debug(
                "upstream_response",
                chat_url,
                method="POST",
                status=e.response.status_code,
                duration_ms=(time.time() - started) * 1000,
                body={"error": e.response.text},
            )
            raise UpstreamError(e.response.status_code, e.response.text) from e
        except httpx.TimeoutException as e:
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

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncIterator[bytes]:
        """流式对话, yield SSE 原始字节.

        `**kwargs` 会原样合入 payload (tools / tool_choice / response_format 等),
        由调用方决定透传哪些字段. 服务商侧限制 (如 DashScope 兼容端点 stream+tools
        互斥) 交由上游报错 → 通过 UpstreamError 走 SSE error 帧回客户端, 不在此层
        静默丢字段。
        """
        payload: dict[str, Any] = {
            "model": model or self.config.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        client = await self._get_client()
        stream_url = f"{self.config.base_url}/chat/completions"

        # Debug: 记录上游请求
        _log_upstream("REQUEST (STREAM)", self.config.base_url, payload)
        event_id = _emit_debug(
            "upstream_request", stream_url, method="POST", body=payload
        )

        bus = get_debug_bus()
        started = time.time()
        collected_text_parts: list[str] = []
        try:
            async with client.stream(
                "POST",
                stream_url,
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if event_id and bus is not None and bus.should_emit():
                        bus.append_stream_chunk(event_id, chunk)
                        # 也顺手累计 assembled 文本
                        try:
                            collected_text_parts.append(
                                chunk.decode("utf-8", errors="ignore")
                            )
                        except Exception:
                            pass
                    yield chunk
                if event_id and bus is not None:
                    assembled = parse_sse_stream([c.encode("utf-8") for c in collected_text_parts]) if collected_text_parts else ""
                    bus.finalize_stream(
                        event_id,
                        assembled=assembled,
                        status=resp.status_code,
                        duration_ms=(time.time() - started) * 1000,
                    )
        except httpx.HTTPStatusError as e:
            body = await e.response.aread()
            _log_upstream("ERROR", self.config.base_url, {"error": body.decode()}, status=e.response.status_code)
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id,
                    assembled=body.decode("utf-8", errors="replace"),
                    status=e.response.status_code,
                    duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamError(e.response.status_code, body.decode()) from e
        except httpx.TimeoutException as e:
            _log_upstream("TIMEOUT", self.config.base_url, {"error": str(e)})
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id,
                    assembled=f"timeout: {e}",
                    status=None,
                    duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamTimeout(f"chat_stream timeout after {self.config.timeout}s") from e

    # ============ 嵌入 ============

    async def embed(
        self,
        input: str | list[str],
        model: str,
        dimensions: int | None = None,
    ) -> list[list[float]]:
        """调用 embedding API, 返回向量列表."""
        payload: dict[str, Any] = {"model": model, "input": input}
        if dimensions is not None:
            payload["dimensions"] = dimensions

        client = await self._get_client()
        embed_url = f"{self.config.base_url}/embeddings"
        _emit_debug("upstream_request", embed_url, method="POST", body=payload)
        started = time.time()
        try:
            resp = await client.post(
                embed_url,
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            # OpenAI 兼容: data[].embedding, 按 index 排序
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            vectors = [item["embedding"] for item in items]
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=resp.status_code,
                duration_ms=(time.time() - started) * 1000,
                body={"vectors_count": len(vectors), "dim": len(vectors[0]) if vectors else 0},
            )
            return vectors
        except httpx.HTTPStatusError as e:
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=e.response.status_code,
                duration_ms=(time.time() - started) * 1000,
                body={"error": e.response.text},
            )
            raise UpstreamError(e.response.status_code, e.response.text) from e
        except httpx.TimeoutException as e:
            _emit_debug(
                "upstream_response",
                embed_url,
                method="POST",
                status=None,
                duration_ms=(time.time() - started) * 1000,
                body={"error": f"timeout: {e}"},
            )
            raise UpstreamTimeout("embed timeout") from e

    # ============ 重排序 ============

    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str,
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """调用 rerank API.

        Returns:
            list of {index, relevance_score, document?}, 按 relevance_score 降序.
        """
        payload: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        client = await self._get_client()
        # 尝试 /rerank, 失败则试 /reranks（部分服务商用复数）
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
                # 补 document 文本（若服务商未返回）
                for r in results:
                    if "document" not in r or r.get("document") is None:
                        r["document"] = documents[r["index"]]
                return results
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404 and endpoint == "/rerank":
                    continue
                raise UpstreamError(e.response.status_code, e.response.text) from e
            except httpx.TimeoutException as e:
                raise UpstreamTimeout("rerank timeout") from e
        raise UpstreamError(404, "rerank endpoint not found (/rerank and /reranks both failed)")

    # ============ 模型列表 ============

    async def list_models(self) -> list[str]:
        """列出服务商可用模型."""
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.config.base_url}/models",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except httpx.HTTPStatusError as e:
            raise UpstreamError(e.response.status_code, e.response.text) from e

    # ============ 生命周期 ============

    async def close(self) -> None:
        if self._client and not self._pool:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Forwarder:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


@dataclass
class StreamResult:
    """流式 SSE 累积结果.

    v0.3.0 起支持 tool_calls 累积, 用于纯工具调用响应的检测和持久化.
    """

    text: str
    tool_calls: list[dict] | None  # 累积合并后的完整 tool_calls; None = 无工具调用
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
    tool_calls: dict[int, dict] = {}
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

    result_tool_calls: list[dict] | None = None
    if tool_calls:
        result_tool_calls = [tool_calls[i] for i in sorted(tool_calls)]

    return StreamResult(
        text="".join(content_parts),
        tool_calls=result_tool_calls,
        finish_reason=finish_reason,
    )
