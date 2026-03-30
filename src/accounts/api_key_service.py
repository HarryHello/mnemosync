"""API Key 业务逻辑服务."""

from .api_key_models import ApiKey
from .api_key_store import ApiKeyStore


class ApiKeyService:
    """API Key 业务逻辑服务."""

    def __init__(self, store: ApiKeyStore):
        self.store = store

    async def create_key(self, note: str) -> ApiKey:
        """创建新的 API Key.

        Args:
            note: API Key 备注信息

        Returns:
            生成的 API Key 对象 (包含完整 key)
        """
        api_key = ApiKey.generate(note)
        await self.store.save(api_key)
        return api_key

    async def get_key(self, key_id: str) -> ApiKey | None:
        """根据 ID 获取 API Key."""
        return await self.store.get_by_id(key_id)

    async def list_keys(self) -> list[ApiKey]:
        """列出所有 API Key."""
        return await self.store.list_all()

    async def revoke_key(self, key_id: str) -> bool:
        """撤销 (删除) API Key.

        Args:
            key_id: API Key ID

        Returns:
            是否删除成功
        """
        return await self.store.delete(key_id)

    async def validate_key(self, key_value: str) -> ApiKey | None:
        """验证 API Key 并返回详情.

        Args:
            key_value: 完整的 API Key 值 (如 sk-xxx)

        Returns:
            验证通过返回 ApiKey 对象，否则返回 None
        """
        keys = await self.store.list_all()
        for key in keys:
            if key.key_full == key_value and key.is_active:
                return key
        return None
