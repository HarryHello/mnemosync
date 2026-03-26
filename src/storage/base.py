"""存储层抽象基类."""

from abc import ABC, abstractmethod
from typing import Protocol

from .models import ApiKey


class ApiKeyStore(Protocol):
    """API Key 存储协议."""

    @abstractmethod
    async def save(self, api_key: ApiKey) -> None:
        """保存 API Key."""
        ...

    @abstractmethod
    async def get_by_id(self, key_id: str) -> ApiKey | None:
        """根据 ID 获取 API Key."""
        ...

    @abstractmethod
    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None:
        """根据密钥哈希获取 API Key."""
        ...

    @abstractmethod
    async def list_all(self) -> list[ApiKey]:
        """列出所有 API Key."""
        ...

    @abstractmethod
    async def delete(self, key_id: str) -> bool:
        """删除 API Key."""
        ...

    @abstractmethod
    async def update_last_used(self, key_id: str) -> None:
        """更新最后使用时间."""
        ...
