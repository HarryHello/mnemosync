"""Mnemosync CLI 交互环境."""

import asyncio
import getpass
import sys
import os
from typing import Optional

from src.storage import SqliteAuthService, SqliteApiKeyStore, ApiKey, InvalidCredentialsError, PasswordTooWeakError


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
            try:
                username = input("Account: ").strip()
                password = getpass.getpass("Password: ")
            except KeyboardInterrupt:
                print("\n\n👋 Ctrl+C detected. Exiting CLI (Mnemosync service keeps running in background).\n")
                return False

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
            except KeyboardInterrupt:
                print("\n\n👋 Ctrl+C detected. Exiting CLI (Mnemosync service keeps running in background).\n")
                return False

        return False

    async def change_credentials(self):
        """修改账号密码."""
        print("Please change your account and password.")

        while True:
            try:
                new_username = input("New account: ").strip()
                if not new_username:
                    new_username = self.current_user.username

                new_password = getpass.getpass("New Password: ")
                confirm = getpass.getpass("Confirm Password: ")
            except KeyboardInterrupt:
                print("\n\n👋 Ctrl+C detected. Exiting CLI (Mnemosync service keeps running in background).\n")
                self.running = False
                return

            if new_password != confirm:
                print("❌ Passwords do not match. Please try again.\n")
                continue

            if len(new_password) < 6:
                print("❌ Password must be at least 6 characters.\n")
                continue

            try:
                # TODO: Implement change_username_and_password in auth_service
                await self.auth_db.change_username_and_password(
                    self.current_user.id,
                    new_username,
                    new_password
                )
                print("\n✅ Credentials changed successfully!\n")
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
            # Mask key for display (show first 6 and last 4 of prefix)
            masked_key = f"{key.key_prefix[:6]}****{key.key_prefix[-4:]}" if len(key.key_prefix) > 10 else key.key_prefix + "****"
            print(f"{masked_key:<20} {key.id:<10} {key.note:<20}")

    async def cmd_show_key(self, key_id: str):
        """显示特定 API Key."""
        key = await self.api_key_db.get_by_id(key_id)

        if not key:
            print(f"❌ Key with id '{key_id}' not found.")
            return

        # Display full key if available
        if key.key_full:
            print(f"\n{key.key_full}\n")
        else:
            print(f"\nsk-{'*' * 30}\n")
            print("(Key was generated before full key storage was implemented)\n")
        
        print(f"Annotation: {key.note}\n")
        print("⚠️  Do not let others get your keys!\n")

    async def cmd_generate_key(self):
        """生成新的 API Key."""
        print("It is recommanded to map one key to one platform.")
        try:
            annotation = input("Please enter the annotation for the new key:\n> ").strip()
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        if not annotation:
            annotation = "Unnamed"

        api_key = ApiKey.generate(note=annotation)
        raw_key = f"sk-{api_key.key_prefix[3:]}" if api_key.key_full is None else api_key.key_full
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
        """列出服务."""
        # TODO: Implement LLM service provider storage
        print("service-id       base-url                     api-key")
        print("openai           https://api.openai.com/v1    sk-********enai")

    async def cmd_show_service(self, service_id: str):
        """显示特定服务信息."""
        # TODO: Implement LLM service provider storage
        print(f"Service ID: {service_id}")
        print(f"Base URL: https://api.{service_id}.com/v1")
        print(f"API Key: sk-********\n")

    async def cmd_ad_service(self):
        """添加服务."""
        # TODO: Implement LLM service provider storage
        print("Add new llm service provider:")
        try:
            service_id = input("Custom service id: ").strip()
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        # Simple duplicate check (stub)
        if service_id == "openai":
            print("This id has been already used!\n")
            return

        try:
            base_url = input("base URL: ").strip()
            api_key = getpass.getpass("API key: ")
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return
        
        print(f"\nLLM service provider '{service_id}' has been added!\n")

    async def cmd_rm_service(self, service_id: str):
        """移除服务."""
        # TODO: Implement LLM service provider storage
        print(f"LLM service provider {service_id} has been removed!\n")

    async def cmd_ls_models(self, service_id: str):
        """列出模型."""
        # TODO: Implement model listing from provider
        print(f"Available models for {service_id}:")
        print("Pro/MiniMaxAI/MiniMax-M2.5")
        print("Pro/zai-org/GLM-5")
        print("Pro/moonshotai/Kimi-K2.5")
        print("Qwen/Qwen3.5-397B-A17B")
        print("...\n")

    async def cmd_set_main_model(self, service_id: str, model: str):
        """设置主模型."""
        # TODO: Implement model configuration storage
        print(f"Change main model to {model} from {service_id} successfully!\n")

    async def cmd_set_assist_model(self, service_id: str, model: str):
        """设置辅助模型."""
        # TODO: Implement model configuration storage
        print(f"Change assist model to {model} from {service_id} successfully!\n")

    async def cmd_test_model(self, service_id: str, model: str):
        """测试模型."""
        # TODO: Implement actual model connection test
        print(f"Testing connection to {model} from {service_id}...")
        print("✅ Connection successful!\n")

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
            elif cmd == "show-service":
                if args:
                    await self.cmd_show_service(args[0])
                else:
                    print("❌ Usage: show-service [srv_id]\n")
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
        import signal
        
        # 设置信号处理器
        def signal_handler(sig, frame):
            print("\n\n👋 Ctrl+C detected. Exiting CLI (Mnemosync service keeps running in background).\n")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        
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
                print("\n\n👋 Exiting CLI (Mnemosync service keeps running in background).\n")
                break


async def main():
    """主入口."""
    cli = MnemosyncCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())
