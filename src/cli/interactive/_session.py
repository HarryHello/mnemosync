"""Mnemosync CLI 核心会话."""

from __future__ import annotations

import getpass
import os

from src.infra.llm_service.store import LLMServiceStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore

from ._ask import AskCommandsMixin
from ._dispatch import DispatchMixin
from ._input import check_exit_requested, setup_exit_handler
from ._keys import KeyCommandsMixin
from ._memory import MemoryCommandsMixin
from ._model import ModelCommandsMixin
from ._persona import PersonaCommandsMixin
from ._services import ServiceCommandsMixin


class MnemosyncCLI(
    KeyCommandsMixin,
    ServiceCommandsMixin,
    ModelCommandsMixin,
    MemoryCommandsMixin,
    PersonaCommandsMixin,
    AskCommandsMixin,
    DispatchMixin,
):
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

Debug:
  ask [flags] "<question>"   In-process 跑一次主对话 (加载记忆 + 图流程)
                             flags: --user <name>  --persona-file <path>
                                    --stream       --debug   -v/--verbose

API-Key Commands:
  ls-keys                  List existing api-keys
  show-key [key_id]        Show the specific key
  generate-key             Generate a new api-key

LLM Service Commands:
  ls-service               List existing llm service provider
  ad-service               Add a new llm service provider
  rm-service [srv_id]      Remove a llm service provider
  show-service [srv_id]    Show the information
  ls-models [srv_id]       List available models from provider API

Model Binding Commands (role_bindings table, hot-reloaded):
  model ls [role]                       List bindings (optionally filter by role)
  model add <role> <srv_id> <model> [--priority N] [--context N] [--dim N] [--send-dim]
                                        Append (or insert at N) a candidate for a role.
                                        --context: 上下文窗口 (token, 面板展示用)
                                        --dim: 嵌入维度 (向量库维度锁)
                                        --send-dim: 把 --dim 作为 dimensions 参数
                                                    透传给上游 (仅可变维模型需要)
  model rm <role> <priority>            Remove a candidate at priority
  model reorder <role> <srv_id:model,...>
                                        Reorder candidates for a role
  model test <role>                     Test top candidate for the role
  test-model [srv_id] [model]           Test a specific provider/model pair

Memory Maintenance (v0.2.4, via panel HTTP):
  memory reindex [--prune] [--threshold F]
                                        Rebuild all vectors (换嵌入模型后必跑).
                                        --prune 顺便清理低价值记忆.
  memory prune [--threshold F] [--dry-run]
                                        本地规则清理 (forgotten / expired / low priority).
                                        --dry-run 只预览不删.

Persona State (v0.2.7, via panel HTTP):
  persona reset [--dry-run] [--yes]     清空长期记忆(含 PERMANENT) / 关系 / 短期流水 / 向量库.
                                        保留服务商 / API Key / 提示词 / 绑定等运维配置.
                                        --dry-run 只统计; --yes 跳过交互确认.
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
