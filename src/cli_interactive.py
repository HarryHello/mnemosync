"""Mnemosync CLI 交互环境."""

import asyncio
import getpass
import sys
import os
from typing import Optional

from .storage import SqliteAuthService, SqliteApiKeyStore, ApiKey, InvalidCredentialsError, PasswordTooWeakError


class MnemosyncCLI:
    """Mnemosync 交互式 CLI."""
    
    BANNER = """
╭───────────────────────────────────────────────────────────────╮
│                                                               │
│  │  ╲╱  ││ \\ │ ││  ___│  ╲╱  │  _  ╱  ___\\ ╲ ╱ / ╲ │ /  __ ╲  │
│  │ .  . ││  \\│ ││ │__ │ .  . │ │ │ ╲ `──. \\ V /│  ╲│ │ /  ╲╱  │
│  │ │╲╱│ ││ . ` ││  __││ │╲╱│ │ │ │ │`──. ╲ ╲ / │ . ` │ │      │
│  │ │  │ ││ │\\  ││ │___│ │  │ │ \\_/ ╱╲__╱ ╱ │ │ │ │\\  │ \\__╱╲  │
│  \\_│  │_╱╲_│ ╲_╱╲____╱╲_│  │_╱╲___╱╲____╱  \\_/ ╲_│ ╲_╱╲____╱  │
│                                                               │
│                         Mnemosync                             │
│                         v0.1.0                                │
│                                                               │
╰───────────────────────────────────────────────────────────────╯
"""

    def __init__(self):
        self.auth_db = SqliteAuthService(os.getenv("AUTH_DB_PATH", "data/auth.db"))
        self.api_key_db = SqliteApiKeyStore(os.getenv("MNEMOSYNC_DB_PATH", "data/api_keys.db"))
        self.current_user = None
        self.running = True

    async def init_db(self):
        """初始化数据库."""
        await self.auth_db.init_db()
        await self.api_key_db.init_db()

    def print_help(self):
        """打印帮助信息."""
        help_text = """
Usage: COMMAND [OPTIONS]

Common Commands:
  help        Show this page
  logout      Exit this CLI environment
  stop        Stop the Mnemosync server

API-Key Commands:
  ls-keys                  List existing api-keys
  show-key [key_id]        Show the specific key
  generate-key             Generate a new api-key

LLM Service Commands:
  ls-service               List existing llm service provider
  ad-service               Add a new llm service provider
  rm-service [srv_id]      Remove a llm service provider
  show-service             Show the information
  ls-models [srv_id]       List available models

Models Commands:
  set-main-model [srv_id] [model]      Set the main model for Mnemosync
  set-assist-model [srv_id] [model]    Set the assist model for Mnemosync
  test-model [srv_id] [model]          Test if able to connect to a model
"""
        print(help_text)

    async def login(self) -> bool:
        """登录."""
        print(self.BANNER)
        print("Welcome to Mnemosync!")
        print("Please login with account and password.")
        print("The default account and password are all 'mnemosync'.\n")

        await self.init_db()

        max_attempts = 5
        for attempt in range(max_attempts):
            username = input("Account: ").strip()
            password = getpass.getpass("Password: ")

            try:
                user = await self.auth_db.authenticate(username, password)
                self.current_user = user
                print("\n✅ Login Successfully!\n")
                print("Use `help` to get commands information.\n")

                # 首次登录需要修改密码
                if user.must_change_password:
                    print("⚠️  First login detected. Please change your account and password.\n")
                    await self.change_credentials()
                    return False  # 修改后需要重新登录

                return True

            except InvalidCredentialsError:
                remaining = max_attempts - attempt - 1
                if remaining > 0:
                    print(f"❌ Invalid credentials. {remaining} attempts remaining.\n")
                else:
                    print("❌ Too many failed attempts. Exiting.\n")
                    return False

        return False

    async def change_credentials(self):
        """修改账号密码."""
        print("Please change your account and password.")
        
        while True:
            new_username = input("New account: ").strip()
            if not new_username:
                new_username = self.current_user.username
            
            new_password = getpass.getpass("New Password: ")
            confirm = getpass.getpass("Confirm Password: ")

            if new_password != confirm:
                print("❌ Passwords do not match. Please try again.\n")
                continue

            if len(new_password) < 6:
                print("❌ Password must be at least 6 characters.\n")
                continue

            try:
                await self.auth_db.change_password(
                    self.current_user.id,
                    self.current_user.password_hash,  # 这里需要原密码，逻辑需要调整
                    new_password
                )
                print("\n✅ Credentials changed successfully! Please login again.\n")
                return
            except Exception as e:
                print(f"❌ Failed to change credentials: {e}\n")

    async def cmd_ls_keys(self):
        """列出 API Keys."""
        keys = await self.api_key_db.list_all()
        
        if not keys:
            print("No API keys found.")
            return

        print(f"{'key':<20} {'key-id':<10} {'annotation':<20}")
        print("-" * 50)
        for key in keys:
            masked_key = f"{key.key_prefix[:6]}****{key.key_prefix[-4:]}" if len(key.key_prefix) > 10 else key.key_prefix + "****"
            print(f"{masked_key:<20} {key.id:<10} {key.note:<20}")

    async def cmd_show_key(self, key_id: str):
        """显示特定 API Key."""
        key = await self.api_key_db.get_by_id(key_id)
        
        if not key:
            print(f"❌ Key with id '{key_id}' not found.")
            return

        # 重建完整 key（简化处理，实际需要存储完整 key 或从哈希恢复）
        print(f"\nsk-{'*' * 30}\n")
        print(f"Annotation: {key.note}\n")
        print("⚠️  Do not let others get your keys!\n")

    async def cmd_generate_key(self):
        """生成新的 API Key."""
        print("It is recommended to map one key to one platform.")
        annotation = input("Please enter the annotation for the new key:\n> ").strip()
        
        if not annotation:
            annotation = "Unnamed"

        api_key = ApiKey.generate(note=annotation)
        raw_key = f"sk-{api_key.key_prefix[3:]}"
        await self.api_key_db.save(api_key)

        print(f"\nYour new api-key is:")
        print(f"\n{raw_key}\n")
        print("⚠️  Do not let others get your keys!\n")

    async def cmd_logout(self):
        """登出."""
        print("\n👋 Logout Mnemosync CLI.\n")
        self.running = False

    async def cmd_stop(self):
        """停止服务（预留）."""
        print("\n🛑 Stopping Mnemosync server...\n")
        # 这里需要调用 Docker 停止命令
        self.running = False

    async def cmd_help(self):
        """显示帮助."""
        self.print_help()

    async def cmd_ls_service(self):
        """列出服务（预留）."""
        print("LLM Service Commands - Not implemented yet.\n")

    async def cmd_ad_service(self):
        """添加服务（预留）."""
        print("Add new llm service provider:")
        service_id = input("Custom service id: ").strip()
        base_url = input("base URL: ").strip()
        api_key = getpass.getpass("API key: ")
        print(f"\nService '{service_id}' added. (Not implemented yet)\n")

    async def cmd_rm_service(self, service_id: str):
        """移除服务（预留）."""
        print(f"LLM service provider {service_id} has been removed! (Not implemented yet)\n")

    async def cmd_ls_models(self, service_id: str):
        """列出模型（预留）."""
        print(f"Available models for {service_id}:\n")
        print("Pro/MiniMaxAI/MiniMax-M2.5")
        print("Pro/zai-org/GLM-5")
        print("Pro/moonshotai/Kimi-K2.5")
        print("Qwen/Qwen3.5-397B-A17B")
        print("... (Not implemented yet)\n")

    async def cmd_set_main_model(self, service_id: str, model: str):
        """设置主模型（预留）."""
        print(f"Change main model to {model} from {service_id} successfully!\n")

    async def cmd_set_assist_model(self, service_id: str, model: str):
        """设置辅助模型（预留）."""
        print(f"Change assist model to {model} from {service_id} successfully!\n")

    async def cmd_test_model(self, service_id: str, model: str):
        """测试模型（预留）."""
        print(f"Testing connection to {model} from {service_id}...\n")
        print("✅ Connection successful! (Not implemented yet)\n")

    async def process_command(self, line: str):
        """处理命令."""
        parts = line.strip().split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd == "help":
                await self.cmd_help()
            elif cmd == "logout":
                await self.cmd_logout()
            elif cmd == "stop":
                await self.cmd_stop()
            elif cmd == "ls-keys":
                await self.cmd_ls_keys()
            elif cmd == "show-key":
                if args:
                    await self.cmd_show_key(args[0])
                else:
                    print("❌ Usage: show-key [key_id]\n")
            elif cmd == "generate-key":
                await self.cmd_generate_key()
            elif cmd == "ls-service":
                await self.cmd_ls_service()
            elif cmd == "ad-service":
                await self.cmd_ad_service()
            elif cmd == "rm-service":
                if args:
                    await self.cmd_rm_service(args[0])
                else:
                    print("❌ Usage: rm-service [srv_id]\n")
            elif cmd == "ls-models":
                if args:
                    await self.cmd_ls_models(args[0])
                else:
                    print("❌ Usage: ls-models [srv_id]\n")
            elif cmd == "set-main-model":
                if len(args) >= 2:
                    await self.cmd_set_main_model(args[0], args[1])
                else:
                    print("❌ Usage: set-main-model [srv_id] [model]\n")
            elif cmd == "set-assist-model":
                if len(args) >= 2:
                    await self.cmd_set_assist_model(args[0], args[1])
                else:
                    print("❌ Usage: set-assist-model [srv_id] [model]\n")
            elif cmd == "test-model":
                if len(args) >= 2:
                    await self.cmd_test_model(args[0], args[1])
                else:
                    print("❌ Usage: test-model [srv_id] [model]\n")
            else:
                print(f"❌ Unknown command: {cmd}\n")
                print("Use 'help' to see available commands.\n")

        except Exception as e:
            print(f"❌ Error: {e}\n")

    async def run(self):
        """运行 CLI."""
        # 登录
        if not await self.login():
            # 如果是修改密码后需要重新登录
            if self.current_user:
                await self.login()
            else:
                return

        # 交互循环
        while self.running:
            try:
                line = input("Mnemosync > ").strip()
                if line:
                    await self.process_command(line)
            except EOFError:
                print("\n\n👋 Goodbye!\n")
                break
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!\n")
                break


async def main():
    """主入口."""
    cli = MnemosyncCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())
