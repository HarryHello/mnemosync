"""HTTP 连接池管理."""

from __future__ import annotations

import httpx


class ConnectionPool:
    """上游模型连接池，复用 HTTP 连接."""

    def __init__(
        self,
        max_connections: int = 50,
        max_keepalive_connections: int = 10,
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
    ):
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                limits=self._limits,
                timeout=self._timeout,
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> ConnectionPool:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()
