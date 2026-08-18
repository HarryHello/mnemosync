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
from dataclasses import dataclass, field
from functools import lru_cache
from types import TracebackType
from typing import Any, cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .connection_pool import ConnectionPool
from .debug_hook import get_debug_bus
from .debug_utils import emit_upstream_debug as _emit_debug  # noqa: F401
from .model_capabilities import known_capability_for

logger = logging.getLogger(__name__)


@dataclass
class ModelDetail:
    """上游 /v1/models 单条模型: id + 尽力解析的能力声明.

    能力字段来自服务商在模型列表里携带的扩展字段 (非 OpenAI 标准),
    常见键名见 _extract_model_capability; 缺失时回落默认 (text / None).
    """

    id: str
    context_length: int | None = None
    output_limit: int | None = None
    supports_tools: bool = False   # 工具调用 (function calling)
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])


def _parse_tool_support(raw: dict[str, Any]) -> bool:
    """尽力解析上游是否声明支持工具调用.

    支持 bool/int/str, 或 list (如 OpenRouter supported_parameters 含 "tools").
    """
    for key in ("supports_tools", "tool_calling", "server_side_tool_use", "tool_call"):
        v = raw.get(key)
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        if isinstance(v, list):
            return any(str(x).strip().lower() in ("tools", "tool_call") for x in v)
        return False
    # OpenRouter: supported_parameters 是 list, 含 "tools" 即支持
    params = raw.get("supported_parameters")
    if isinstance(params, list):
        return any(str(x).strip().lower() in ("tools", "tool_call") for x in params)
    return False


def _extract_model_capability(
    raw: dict[str, Any],
    *names: str,
) -> Any | None:
    """从模型条目 dict 里按候选键名尽力取值 (int 或 list).

    支持点路径 (如 architecture.input_modalities, 兼容 OpenRouter).
    """
    for name in names:
        # 点路径逐段下钻
        node: Any = raw
        ok = True
        for part in name.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        v = node if ok else None
        if v is None:
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return max(0, v)
        if isinstance(v, (float, str)):
            try:
                return max(0, int(float(str(v).replace(",", ""))))
            except ValueError:
                continue
        if isinstance(v, list):
            return v
    return None


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


# 429 错误的配额类关键词: 命中表示余额/配额问题, 等待无用, 应 fallback 换候选
_QUOTA_KEYWORDS = (
    "insufficient_quota", "quota_exceeded", "quota", "billing", "payment",
    "insufficient_credits", "insufficient_balance", "insufficient balance",
    "purchase a plan", "api key expired", "forbidden",
)
# 429 错误的限流类关键词: 命中表示并发/速率限制, 等待有效
_RATE_KEYWORDS = (
    "rate_limit", "rate limit", "too_many_requests", "too many requests",
    "overloaded", "throttl", "temporarily unavailable",
)


@lru_cache(maxsize=256)
def classify_429(message: str) -> str:
    """分类 429 错误.

    结构化提取优先 (error.code / error.type, 递归找), 找不到则全文关键词兜底.
    相同错误文本只解析一次 (lru_cache), 服务商固定错误模板下零重复开销.
    Returns:
        "quota" (余额不足, 应 fallback) | "rate" (限流, 应重试) | "unknown"
    """
    # 结构化提取 error.code / error.type
    try:
        import json
        parsed = json.loads(message)
        for value in _walk_error_codes(parsed):
            lowered = str(value).lower()
            if any(k in lowered for k in _RATE_KEYWORDS):
                return "rate"
            if any(k in lowered for k in _QUOTA_KEYWORDS):
                return "quota"
    except (json.JSONDecodeError, ValueError):
        pass

    # 全文关键词兜底: 先查限流 (宁可重试不误换候选), 再查配额
    lowered = message.lower()
    if any(k in lowered for k in _RATE_KEYWORDS):
        return "rate"
    if any(k in lowered for k in _QUOTA_KEYWORDS):
        return "quota"
    return "unknown"


def _walk_error_codes(obj: Any) -> list[Any]:
    """递归遍历 dict 提取 error.code / error.type 类字段的值."""
    results: list[Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key in ("code", "type") and isinstance(value, str):
                results.append(value)
            else:
                results.extend(_walk_error_codes(value))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_walk_error_codes(item))
    return results


class UpstreamError(Exception):
    """上游服务错误."""

    def __init__(self, status_code: int | None = None, message: str = ""):
        self.status_code = status_code
        self.message = message
        # 429 分类: "quota" / "rate" / "unknown"
        self.category: str | None = classify_429(message) if status_code == 429 else None
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
        """列出服务商可用模型 id. 使用 openai SDK."""
        details = await self.list_model_details()
        return [d.id for d in details]

    async def list_model_details(self) -> list[ModelDetail]:
        """列出服务商可用模型并尽力解析能力声明.

        /v1/models 每条 model 对象除 id 外可能携带服务商扩展字段
        (context_length / max_output_tokens / input_modalities 等), 这里把它们
        映射到 ModelDetail; 缺失回落默认 (text / None). 未识别字段忽略.
        """
        client = self._get_openai_client()
        try:
            models = await client.models.list()
        except APIStatusError as e:
            raise UpstreamError(e.status_code, e.response.text) from e

        out: list[ModelDetail] = []
        for m in models.data:
            raw: dict[str, Any] = {}
            for key in ("id", "object", "created", "owned_by"):
                v = getattr(m, key, None)
                if v is not None:
                    raw[key] = v
            extra = getattr(m, "model_extra", None) or {}
            raw.update(extra)  # SDK 未声明但上游返回的扩展字段
            mid = str(raw.get("id") or "")
            if not mid:
                continue
            cl = _extract_model_capability(
                raw, "context_length", "max_context_length",
                "max_context_window", "context_window", "model_max_length",
            )
            ol = _extract_model_capability(
                raw, "max_output_tokens", "output_limit", "output_token_limit",
                "max_tokens",
            )
            st = _parse_tool_support(raw)
            im = _extract_model_capability(raw, "input_modalities", "modalities", "architecture.input_modalities")
            om = _extract_model_capability(raw, "output_modalities", "architecture.output_modalities")
            input_mods = [str(x) for x in im] if isinstance(im, list) else ["text"]
            output_mods = [str(x) for x in om] if isinstance(om, list) else ["text"]
            # 上游未声明能力时, 用内置知名模型表兜底 (如 DeepSeek 的 /v1/models 只有 id)
            known = known_capability_for(mid)
            if known is not None:
                if not isinstance(cl, (int, float)) and known.context_length is not None:
                    cl = known.context_length
                if not isinstance(ol, (int, float)) and known.output_limit is not None:
                    ol = known.output_limit
                if not st and known.supports_tools:
                    st = True
                if input_mods == ["text"] and known.input_modalities != ("text",):
                    input_mods = list(known.input_modalities)
            out.append(ModelDetail(
                id=mid,
                context_length=int(cl) if isinstance(cl, (int, float)) else None,
                output_limit=int(ol) if isinstance(ol, (int, float)) else None,
                supports_tools=st,
                input_modalities=input_mods,
                output_modalities=output_mods,
            ))
        return out

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
