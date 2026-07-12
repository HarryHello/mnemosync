"""Mnemosync CLI 交互环境."""

import asyncio
import getpass
import os
import signal
import sys
import termios
import tty

from src.core.config_writer import update_chat_model, update_model, get_current_config
from src.infra.forwarder.forwarder import Forwarder, ForwarderConfig
from src.infra.llm_service.models import LLMServiceProvider, ModelConfiguration, ModelType
from src.infra.llm_service.store import LLMServiceStore
from src.persistence.api_key_store import ApiKey, SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore

# 全局退出标志
_exit_requested = False


def secure_input(prompt: str = "") -> str:
    """安全输入，显示星号代替字符."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars = []

    try:
        tty.setraw(fd)
        sys.stdout.write(prompt)
        sys.stdout.flush()

        while True:
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                break
            elif ch == '\x7f' or ch == '\x08':  # Backspace
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif ch.isprintable():
                chars.append(ch)
                sys.stdout.write('*')
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ''.join(chars)


def mask_api_key(key: str) -> str:
    """遮蔽 API Key，只显示前4位和后4位."""
    if len(key) <= 8:
        return key
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def setup_exit_handler():
    """设置全局退出处理器."""
    global _exit_requested

    def signal_handler(sig, frame):
        _exit_requested = True
        raise KeyboardInterrupt("Ctrl+C pressed")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def check_exit_requested():
    """检查是否请求退出."""
    if _exit_requested:
        print("\n\n👋 Exiting CLI (Mnemosync service keeps running in background).\n")
        return True
    return False


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
│                         v0.2.0                                │
│                                                               │
╰───────────────────────────────────────────────────────────────╯
"""

    def __init__(self):
        self.auth_db = SqliteAuthStore(os.getenv("AUTH_DB_PATH", "data/auth.db"))
        self.api_key_db = SqliteApiKeyStore(os.getenv("MNEMOSYNC_DB_PATH", "data/api_keys.db"))
        llm_db = os.getenv("LLM_SERVICE_DB_PATH", "data/llm_service.db")
        self.llm_service_store = LLMServiceStore(llm_db)
        self.current_user = None
        self.current_password = None
        self.running = True

    async def init_db(self):
        """初始化数据库."""
        await self.auth_db.init_db()
        await self.api_key_db.init_db()
        await self.llm_service_store.init_db()

    def print_help(self):
        """打印帮助信息."""
        help_text = """
Usage: COMMAND [OPTIONS]

Common Commands:
  help        Show this page
  logout      Exit this CLI environment
  stop        Stop the Mnemosync server
  show-config Show current config.local.toml settings

API-Key Commands:
  ls-keys                  List existing api-keys
  show-key [key_id]        Show the specific key
  generate-key             Generate a new api-key

LLM Service Commands:
  ls-service               List existing llm service provider
  ad-service               Add a new llm service provider
  rm-service [srv_id]      Remove a llm service provider
  show-service             Show the information
  ls-models [srv_id]       List available models from provider API

Models Commands (writes to config.local.toml):
  set-main-model [srv_id] [model]      Set the main chat model
  set-assist-model [srv_id] [model]    Set the assist chat model
  set-embedding-model [srv_id] [model] Set the embedding model
  set-rerank-model [srv_id] [model]    Set the rerank model (optional)
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
            if check_exit_requested():
                return False

            try:
                username = input("Account: ").strip()
                if check_exit_requested():
                    return False
                password = getpass.getpass("Password: ")
                if check_exit_requested():
                    return False
            except KeyboardInterrupt:
                print(
                    "\n\n👋 Ctrl+C detected. Exiting CLI "
                    "(Mnemosync service keeps running in background).\n"
                )
                return False

            try:
                user = await self.auth_db.authenticate(username, password)
                self.current_user = user
                self.current_password = password
                print("\n✅ Login Successfully!\n")
                print("Use `help` to get commands information.\n")

                if user.must_change_password:
                    print("⚠️  First login detected. Please change your account and password.\n")
                    await self.change_credentials()
                    return False

                return True

            except ValueError:
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
            if check_exit_requested():
                self.running = False
                return

            try:
                new_username = input("New account: ").strip()
                if check_exit_requested():
                    self.running = False
                    return
                if not new_username:
                    new_username = self.current_user.username

                new_password = getpass.getpass("New Password: ")
                if check_exit_requested():
                    self.running = False
                    return
                confirm = getpass.getpass("Confirm Password: ")
                if check_exit_requested():
                    self.running = False
                    return
            except KeyboardInterrupt:
                print(
                    "\n\n👋 Ctrl+C detected. Exiting CLI "
                    "(Mnemosync service keeps running in background).\n"
                )
                self.running = False
                return

            if new_password != confirm:
                print("❌ Passwords do not match. Please try again.\n")
                continue

            if len(new_password) < 6:
                print("❌ Password must be at least 6 characters.\n")
                continue

            try:
                await self.auth_db.change_username_and_password(
                    self.current_user.id,
                    self.current_password,
                    new_username,
                    new_password,
                )
                print("\n✅ Credentials changed successfully!\n")
                self.current_password = new_password
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
            prefix = key.key_prefix
            if len(prefix) > 10:
                masked_key = f"{prefix[:6]}****{prefix[-4:]}"
            else:
                masked_key = prefix + "****"
            print(f"{masked_key:<20} {key.id:<10} {key.note:<20}")

    async def cmd_show_key(self, key_id: str):
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

    async def cmd_generate_key(self):
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

    async def cmd_logout(self):
        """登出."""
        print("\n👋 Logout Mnemosync CLI.\n")
        self.running = False

    async def cmd_stop(self):
        """停止服务（预留）."""
        print("\n🛑 Stopping Mnemosync server...\n")
        self.running = False

    async def cmd_help(self):
        """显示帮助."""
        self.print_help()

    async def cmd_ls_service(self):
        """列出服务."""
        services = await self.llm_service_store.list_services()

        if not services:
            print("No LLM service providers found.")
            return

        print(f"{'service-id':<20} {'base-url':<30} {'api-key':<20}")
        print("-" * 70)
        for svc in services:
            print(f"{svc.id:<20} {svc.base_url:<30} {svc.api_key_masked:<20}")

    async def cmd_show_service(self, service_id: str):
        """显示特定服务信息."""
        service = await self.llm_service_store.get_service(service_id)

        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        print(f"\nService ID: {service.id}")
        print(f"Base URL: {service.base_url}")
        print(f"API Key: {mask_api_key(service.api_key)}")
        print(f"Created: {service.created_at.isoformat()}")
        print(f"Updated: {service.updated_at.isoformat()}")
        print("\n⚠️  Do not let others get your keys!\n")

        main_model = await self.llm_service_store.get_model(service_id, ModelType.MAIN)
        assist_model = await self.llm_service_store.get_model(service_id, ModelType.ASSIST)

        if main_model:
            print(f"Main Model: {main_model.model}")
        if assist_model:
            print(f"Assist Model: {assist_model.model}")
        print()

    async def cmd_ad_service(self):
        """添加服务."""
        print("Add new llm service provider:")

        if check_exit_requested():
            return

        try:
            service_id = input("Custom service id: ").strip()
            if check_exit_requested():
                return
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        if await self.llm_service_store.get_service(service_id) is not None:
            print(f"❌ Service '{service_id}' already exists!\n")
            return

        if check_exit_requested():
            return

        try:
            base_url = input("base URL: ").strip()
            api_key = secure_input("API key: ")
            if check_exit_requested():
                return
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        service = LLMServiceProvider.create(
            service_id=service_id,
            base_url=base_url,
            api_key=api_key,
        )

        try:
            await self.llm_service_store.save_service(service)
            print(f"\n✅ LLM service provider '{service_id}' has been added!\n")
        except Exception as e:
            print(f"\n❌ Failed to add service: {e}\n")

    async def cmd_rm_service(self, service_id: str):
        """移除服务."""
        confirm = input(
            f"Are you sure you want to delete service "
            f"'{service_id}'? (yes/no): "
        ).strip().lower()
        if confirm != "yes":
            print("Cancelled.\n")
            return

        success = await self.llm_service_store.delete_service(service_id)
        if success:
            print(f"✅ LLM service provider '{service_id}' has been removed!\n")
        else:
            print(f"❌ Service '{service_id}' not found.\n")

    async def cmd_ls_models(self, service_id: str):
        """列出服务商可用模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            config = ForwarderConfig(
                base_url=service.base_url,
                api_key=service.api_key,
            )
            async with Forwarder(config) as forwarder:
                models = await forwarder.list_models()
                if not models:
                    print(f"No models available for '{service_id}'.")
                    return
                print(f"Models for {service_id}:")
                print("-" * 40)
                for model in models:
                    print(f"  {model}")
                print()
        except Exception as e:
            print(f"❌ Failed to fetch models: {e}\n")

    async def cmd_set_main_model(self, service_id: str, model: str):
        """设置主模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            update_chat_model(main_model=model, base_url=service.base_url, api_key=service.api_key)
            print(f"✅ Main model set to '{model}'.")
            print(f"   Updated config.local.toml [chat] section.\n")
        except Exception as e:
            print(f"❌ Failed to update config: {e}\n")

    async def cmd_set_assist_model(self, service_id: str, model: str):
        """设置辅助模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            update_chat_model(assist_model=model, base_url=service.base_url, api_key=service.api_key)
            print(f"✅ Assist model set to '{model}'.")
            print(f"   Updated config.local.toml [chat] section.\n")
        except Exception as e:
            print(f"❌ Failed to update config: {e}\n")

    async def cmd_set_embedding_model(self, service_id: str, model: str):
        """设置嵌入模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            update_model("embedding", "model", model, base_url=service.base_url, api_key=service.api_key)
            print(f"✅ Embedding model set to '{model}'.")
            print(f"   Updated config.local.toml [embedding] section.\n")
        except Exception as e:
            print(f"❌ Failed to update config: {e}\n")

    async def cmd_set_rerank_model(self, service_id: str, model: str):
        """设置重排序模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            update_model("rerank", "model", model, base_url=service.base_url, api_key=service.api_key)
            print(f"✅ Rerank model set to '{model}'.")
            print(f"   Updated config.local.toml [rerank] section.\n")
        except Exception as e:
            print(f"❌ Failed to update config: {e}\n")

    async def cmd_show_config(self):
        """显示当前配置."""
        try:
            config = get_current_config()
            print("\nCurrent config.local.toml settings:")
            print("-" * 50)

            for section in ["chat", "embedding", "rerank"]:
                if section in config:
                    print(f"\n[{section}]")
                    for key, value in config[section].items():
                        if key == "api_key" and value:
                            # 遮蔽 API key
                            if len(value) > 8:
                                value = f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
                        print(f"  {key} = {value}")
                else:
                    print(f"\n[{section}] (not configured)")

            print()
        except Exception as e:
            print(f"❌ Failed to read config: {e}\n")

    async def cmd_test_model(self, service_id: str, model: str):
        """测试模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        print(f"Testing connection to {model} from {service_id}...")

        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{service.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {service.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )

                if response.status_code == 200:
                    print("✅ Connection successful!\n")
                else:
                    print(f"❌ Connection failed: {response.status_code} - {response.text[:200]}\n")
        except httpx.RequestError as e:
            print(f"❌ Connection error: {e}\n")
        except Exception as e:
            print(f"❌ Unexpected error: {e}\n")

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
            elif cmd == "set-embedding-model":
                if len(args) >= 2:
                    await self.cmd_set_embedding_model(args[0], args[1])
                else:
                    print("❌ Usage: set-embedding-model [srv_id] [model]\n")
            elif cmd == "set-rerank-model":
                if len(args) >= 2:
                    await self.cmd_set_rerank_model(args[0], args[1])
                else:
                    print("❌ Usage: set-rerank-model [srv_id] [model]\n")
            elif cmd == "show-config":
                await self.cmd_show_config()
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
        setup_exit_handler()

        if not await self.login():
            if self.current_user:
                await self.login()
            else:
                return

        while self.running:
            if check_exit_requested():
                print("\n\n👋 Exiting CLI (Mnemosync service keeps running in background).\n")
                break

            try:
                line = input("Mnemosync > ").strip()
                if line:
                    await self.process_command(line)
            except EOFError:
                print("\n\n👋 Goodbye!\n")
                break
            except KeyboardInterrupt:
                pass


async def main():
    """主入口."""
    cli = MnemosyncCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())
