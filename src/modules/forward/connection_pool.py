"""HTTP 连接池管理."""

import httpx
import asyncio
from typing import Optional


class ConnectionPool:
    """上游模型连接池.

    复用 HTTP 连接，减少 TCP 握手开销.

    Usage:
        pool = ConnectionPool(max_connections=50)
        async with pool.get_connection() as client:
            response = await client.post(...)
    """

    def __init__(
        self,
        max_connections: int = 50,
        max_keepalive_connections: int = 10,
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
    ):
        """初始化连接池.

        Args:
            max_connections: 最大连接数
            max_keepalive_connections: 最大保持活跃的连接数
            timeout: 请求超时 (秒)
            connect_timeout: 连接超时 (秒)
        """
        self.max_connections = max_connections
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._client: Optional[httpx.AsyncClient] = None

    async def _create_client(self) -> httpx.AsyncClient:
        """创建新的 HTTP 客户端."""
        return httpx.AsyncClient(
            limits=self._limits,
            timeout=self._timeout,
            follow_redirects=False,
        )

    async def get_client(self) -> httpx.AsyncClient:
        """获取客户端实例."""
        if self._client is None:
            self._client = await self._create_client()
        return self._client

    async def close(self) -> None:
        """关闭连接池."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ConnectionPool":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
