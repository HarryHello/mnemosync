"""上游模型转发器."""

import os
import json
from typing import AsyncIterator, Any
from dataclasses import dataclass

import httpx

from .connection_pool import ConnectionPool


@dataclass
class ForwarderConfig:
    """转发器配置."""

    base_url: str  # 上游模型基础 URL
    api_key: str  # 上游模型 API Key
    default_model: str  # 默认模型
    timeout: float = 30.0  # 请求超时 (秒)
    connect_timeout: float = 10.0  # 连接超时 (秒)


class Forwarder:
    """上游模型转发器.

    将处理后的消息转发给上游模型提供商，支持流式和非流式响应.

    Usage:
        config = ForwarderConfig(
            base_url="https://api.openai.com/v1",
            api_key="sk-xxx",
            default_model="gpt-4",
        )
        forwarder = Forwarder(config)

        # 非流式
        response = await forwarder.send(messages=[...])

        # 流式
        async for chunk in forwarder.send_stream(messages=[...]):
            process(chunk)
    """

    def __init__(
        self,
        config: ForwarderConfig,
        pool: ConnectionPool | None = None,
    ):
        """初始化转发器.

        Args:
            config: 转发器配置
            pool: 连接池 (可选，不提供则创建默认连接池)
        """
        self.config = config
        self._pool = pool
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端."""
        if self._client is None:
            if self._pool:
                self._client = await self._pool.get_client()
            else:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self.config.timeout,
                        connect=self.config.connect_timeout,
                    ),
                    follow_redirects=False,
                )
        return self._client

    async def send(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        stream: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        """发送请求到上游模型 (非流式).

        Args:
            messages: 处理后的消息列表 (OpenAI 格式)
            model: 模型名称 (可选，默认使用配置的 default_model)
            temperature: 温度 (0-2)
            max_tokens: 最大生成 token 数
            stream: 是否流式 (为 True 时使用 send_stream)
            **kwargs: 其他 OpenAI 兼容参数

        Returns:
            上游模型响应 (OpenAI 兼容格式)

        Raises:
            UpstreamError: 上游服务错误
            UpstreamTimeout: 上游超时
        """
        client = await self._get_client()

        payload = self._build_payload(
            messages=messages,
            model=model or self.config.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        headers = self._build_headers()

        try:
            response = await client.post(
                url=f"{self.config.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            raise UpstreamError(
                status_code=e.response.status_code,
                message=e.response.text,
            ) from e
        except httpx.TimeoutException as e:
            raise UpstreamTimeout(
                f"Upstream request timeout after {self.config.timeout}s"
            ) from e
        except Exception as e:
            raise UpstreamError(message=str(e)) from e

    async def send_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncIterator[bytes]:
        """发送请求到上游模型 (流式).

        Args:
            messages: 处理后的消息列表 (OpenAI 格式)
            model: 模型名称 (可选)
            temperature: 温度 (0-2)
            max_tokens: 最大生成 token 数
            **kwargs: 其他 OpenAI 兼容参数

        Yields:
            SSE 格式的响应分块

        Raises:
            UpstreamError: 上游服务错误
            UpstreamTimeout: 上游超时
        """
        client = await self._get_client()

        payload = self._build_payload(
            messages=messages,
            model=model or self.config.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

        headers = self._build_headers()

        try:
            async with client.stream(
                method="POST",
                url=f"{self.config.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()

                async for chunk in response.aiter_bytes():
                    yield chunk

        except httpx.HTTPStatusError as e:
            error_body = await e.response.aread()
            raise UpstreamError(
                status_code=e.response.status_code,
                message=error_body.decode(),
            ) from e
        except httpx.TimeoutException as e:
            raise UpstreamTimeout(
                f"Upstream stream timeout after {self.config.timeout}s"
            ) from e
        except Exception as e:
            raise UpstreamError(message=str(e)) from e

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        **kwargs,
    ) -> dict[str, Any]:
        """构建请求体."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # 合并其他参数
        payload.update(kwargs)

        return payload

    def _build_headers(self) -> dict[str, str]:
        """构建请求头."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        """关闭转发器."""
        if self._client and not self._pool:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "Forwarder":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class UpstreamError(Exception):
    """上游服务错误."""

    def __init__(self, status_code: int | None = None, message: str = ""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Upstream error {status_code}: {message}")


class UpstreamTimeout(Exception):
    """上游服务超时."""

    pass
