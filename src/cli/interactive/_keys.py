"""API Key 管理命令."""

from __future__ import annotations

from src.persistence.api_key_store import ApiKey

from ._input import check_exit_requested


class KeyCommandsMixin:
    """cmd_ls_keys / cmd_show_key / cmd_generate_key."""

    async def cmd_ls_keys(self) -> None:
        """列出 API Keys (只展示用户手动创建的, 调试面板自动 key 不显示)."""
        keys = await self.api_key_db.list_all(source="user")

        if not keys:
            print("No API keys found.")
            return

        print(f"{'key':<20} {'key-id':<10} {'annotation':<20}")
        print("-" * 50)
        for key in keys:
            prefix = key.key_prefix
            if len(prefix) > 10:
                masked_key = f"{prefix[:6]}****{prefix[-4:]}"
            else:
                masked_key = prefix + "****"
            print(f"{masked_key:<20} {key.id:<10} {key.note:<20}")

    async def cmd_show_key(self, key_id: str) -> None:
        """显示特定 API Key."""
        key = await self.api_key_db.get_by_id(key_id)

        if not key:
            print(f"❌ Key with id '{key_id}' not found.")
            return

        if key.key_full:
            print(f"\n{key.key_full}\n")
        else:
            print(f"\nsk-{'*' * 30}\n")
            print("(Key was generated before full key storage was implemented)\n")

        print(f"Annotation: {key.note}\n")
        print("⚠️  Do not let others get your keys!\n")

    async def cmd_generate_key(self) -> None:
        """生成新的 API Key."""
        print("It is recommended to map one key to one platform.")

        if check_exit_requested():
            return

        try:
            annotation = input("Please enter the annotation for the new key:\n> ").strip()
            if check_exit_requested():
                return
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        if not annotation:
            annotation = "Unnamed"

        api_key = ApiKey.generate(note=annotation)
        await self.api_key_db.save(api_key)

        print("\nYour new api-key is:")
        print(f"\n{api_key.key_full}\n")
        print("⚠️  Do not let others get your keys!\n")
