"""Mnemosync CLI 交互环境."""

import asyncio
import getpass
import os
import shlex
import signal
import sys
import termios
import tty

from src.infra.forwarder.forwarder import Forwarder, ForwarderConfig
from src.infra.llm_service.models import LLMServiceProvider, ModelType
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

    async def cmd_ls_keys(self):
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

        all_bindings = await self.llm_service_store.list_role_bindings()
        related = [b for b in all_bindings if b.service_id == service_id]
        if related:
            print("Role bindings using this service:")
            for b in related:
                print(f"  [{b.role.value}] priority={b.priority} model={b.model}")
        else:
            print("(no role bindings reference this service)")
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

    # ---- Role bindings (v0.2.3): 单一真相源在 llm_service.db.role_bindings ----

    _VALID_ROLES = {mt.value for mt in ModelType}

    def _parse_role(self, role: str) -> ModelType | None:
        if role not in self._VALID_ROLES:
            print(f"❌ role 必须是 {sorted(self._VALID_ROLES)} 之一\n")
            return None
        return ModelType(role)

    async def cmd_model_ls(self, role: str | None = None):
        """列出角色绑定."""
        role_enum = self._parse_role(role) if role else None
        if role and role_enum is None:
            return
        bindings = await self.llm_service_store.list_role_bindings(role_enum)
        if not bindings:
            scope = f"role '{role}'" if role else "any role"
            print(f"No bindings for {scope}.\n")
            return
        print(f"{'role':<10} {'prio':<5} {'service-id':<20} {'model':<30} {'ctx':<8} {'dim':<6} {'send-dim':<8}")
        print("-" * 92)
        for b in bindings:
            ctx = str(b.context_length) if b.context_length else "-"
            dim = str(b.embedding_dim) if b.embedding_dim else "-"
            sd = "yes" if b.send_dimensions else "no"
            print(
                f"{b.role.value:<10} {b.priority:<5} {b.service_id:<20} "
                f"{b.model:<30} {ctx:<8} {dim:<6} {sd:<8}"
            )
        print()

    async def cmd_model_add(self, argv: list[str]):
        """model add <role> <service_id> <model> [--priority N] [--context N] [--dim N] [--send-dim]."""
        import argparse as _argparse

        parser = _argparse.ArgumentParser(prog="model add", add_help=False)
        parser.add_argument("role")
        parser.add_argument("service_id")
        parser.add_argument("model")
        parser.add_argument("--priority", type=int, default=None)
        parser.add_argument("--context", type=int, default=None,
                            help="上下文窗口 (token) - 仅面板展示")
        parser.add_argument("--dim", type=int, default=None,
                            help="嵌入维度 - 用作向量库维度锁 (是否透传上游由 --send-dim 控制)")
        parser.add_argument("--send-dim", dest="send_dim", action="store_true",
                            help="把 --dim 作为 dimensions 参数透传给上游 (仅 text-embedding-3-*, "
                                 "text-embedding-v3/v4, qwen3-embedding-* 等可变维模型需要; "
                                 "bge/bce/jina/mistral/gemini 等固定维模型开启会 400)")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print(
                "❌ Usage: model add <role> <service_id> <model> "
                "[--priority N] [--context N] [--dim N] [--send-dim]\n"
            )
            return

        role_enum = self._parse_role(a.role)
        if role_enum is None:
            return
        if await self.llm_service_store.get_service(a.service_id) is None:
            print(f"❌ Service '{a.service_id}' not found.\n")
            return
        if a.send_dim and a.dim is None:
            print("❌ --send-dim 需要配合 --dim N 使用\n")
            return
        try:
            binding = await self.llm_service_store.add_role_binding(
                role_enum, a.service_id, a.model,
                priority=a.priority,
                context_length=a.context,
                embedding_dim=a.dim,
                send_dimensions=a.send_dim,
            )
            ctx = f" ctx={binding.context_length}" if binding.context_length else ""
            dim = f" dim={binding.embedding_dim}" if binding.embedding_dim else ""
            sd = " send-dim=yes" if binding.send_dimensions else ""
            print(
                f"✅ Added [{binding.role.value}] priority={binding.priority} "
                f"service={binding.service_id} model={binding.model}{ctx}{dim}{sd}\n"
            )
        except ValueError as e:
            print(f"❌ {e}\n")

    async def cmd_model_rm(self, role: str, priority_str: str):
        """删除角色的某个优先级."""
        role_enum = self._parse_role(role)
        if role_enum is None:
            return
        try:
            priority = int(priority_str)
        except ValueError:
            print("❌ priority 必须是整数\n")
            return
        ok = await self.llm_service_store.delete_role_binding(role_enum, priority)
        if ok:
            print(f"✅ Removed [{role}] priority={priority}\n")
        else:
            print(f"❌ Binding not found: [{role}] priority={priority}\n")

    async def cmd_model_reorder(self, role: str, spec: str):
        """model reorder <role> <srv:model,srv:model,...>."""
        role_enum = self._parse_role(role)
        if role_enum is None:
            return
        pairs: list[tuple[str, str]] = []
        for item in spec.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                print(f"❌ 无效项 '{item}', 应为 service_id:model\n")
                return
            sid, model = item.split(":", 1)
            pairs.append((sid.strip(), model.strip()))
        try:
            bindings = await self.llm_service_store.reorder_role_bindings(role_enum, pairs)
            print(f"✅ Reordered [{role}]:")
            for b in bindings:
                print(f"  priority={b.priority} service={b.service_id} model={b.model}")
            print()
        except ValueError as e:
            print(f"❌ {e}\n")

    async def cmd_model_test(self, role: str):
        """测试角色的最高优先级候选."""
        role_enum = self._parse_role(role)
        if role_enum is None:
            return
        candidates = await self.llm_service_store.resolve_role(role_enum)
        if not candidates:
            print(f"❌ role '{role}' 无任何候选\n")
            return
        top = candidates[0]
        print(f"Testing top candidate: service={top.service_id} model={top.model}")
        await self._test_upstream(top.base_url, top.api_key, top.model)

    async def cmd_test_model(self, service_id: str, model: str):
        """测试指定 service_id 的模型直连."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return
        print(f"Testing connection to {model} from {service_id}...")
        await self._test_upstream(service.base_url, service.api_key, model)

    async def _test_upstream(self, base_url: str, api_key: str, model: str) -> None:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
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
                    print(
                        f"❌ Connection failed: {response.status_code} - "
                        f"{response.text[:200]}\n"
                    )
        except httpx.RequestError as e:
            print(f"❌ Connection error: {e}\n")
        except Exception as e:
            print(f"❌ Unexpected error: {e}\n")

    async def cmd_model(self, argv: list[str]):
        """model 子命令派发."""
        if not argv:
            print(
                "❌ Usage: model {ls|add|rm|reorder|test} ...\n"
                "  model ls [role]\n"
                "  model add <role> <service_id> <model> [--priority N]\n"
                "  model rm <role> <priority>\n"
                "  model reorder <role> <srv:model,srv:model,...>\n"
                "  model test <role>\n"
            )
            return
        sub = argv[0]
        rest = argv[1:]
        if sub == "ls":
            await self.cmd_model_ls(rest[0] if rest else None)
        elif sub == "add":
            await self.cmd_model_add(rest)
        elif sub == "rm":
            if len(rest) < 2:
                print("❌ Usage: model rm <role> <priority>\n")
                return
            await self.cmd_model_rm(rest[0], rest[1])
        elif sub == "reorder":
            if len(rest) < 2:
                print("❌ Usage: model reorder <role> <srv:model,srv:model,...>\n")
                return
            await self.cmd_model_reorder(rest[0], " ".join(rest[1:]))
        elif sub == "test":
            if not rest:
                print("❌ Usage: model test <role>\n")
                return
            await self.cmd_model_test(rest[0])
        else:
            print(f"❌ Unknown model subcommand: {sub}\n")

    # ---- Memory maintenance (v0.2.4): reindex + prune via server HTTP ----

    def _panel_base(self) -> str:
        host = os.getenv("MNEMOSYNC_PANEL_HOST", "127.0.0.1")
        port = os.getenv("PORT", "16125")
        return f"http://{host}:{port}"

    async def _panel_token(self) -> str | None:
        """用当前 CLI 会话已知的账号/密码换 panel JWT."""
        import httpx
        if not self.current_user or not self.current_password:
            print("❌ 未登录, 无法调用 panel API\n")
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{self._panel_base()}/panel/auth/login",
                    json={
                        "username": self.current_user.username,
                        "password": self.current_password,
                    },
                )
                if r.status_code != 200:
                    print(f"❌ 登录 panel 失败: {r.status_code} {r.text[:200]}\n")
                    return None
                return r.json().get("access_token")
        except httpx.RequestError as e:
            print(f"❌ 无法连接 panel: {e} (确认服务器在 {self._panel_base()} 运行)\n")
            return None

    async def cmd_memory_reindex(self, argv: list[str]) -> None:
        """memory reindex [--prune] [--threshold F]. 阻塞到完成."""
        import argparse as _argparse
        import httpx

        parser = _argparse.ArgumentParser(prog="memory reindex", add_help=False)
        parser.add_argument("--prune", action="store_true", help="顺便清理低价值记忆")
        parser.add_argument("--threshold", type=float, default=0.05,
                            help="prune 优先级阈值, 默认 0.05")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print("❌ Usage: memory reindex [--prune] [--threshold F]\n")
            return

        token = await self._panel_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        base = self._panel_base()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{base}/panel/admin/memory/reindex",
                    headers=headers,
                    json={"prune": a.prune, "priority_threshold": a.threshold},
                )
                if r.status_code != 200:
                    print(f"❌ 启动失败: {r.status_code} {r.text[:300]}\n")
                    return
                print("▶ Reindex 已启动, 轮询进度中... (Ctrl+C 停止轮询, 后台仍会继续)\n")

                last_processed = -1
                while True:
                    await asyncio.sleep(1.5)
                    sr = await client.get(
                        f"{base}/panel/admin/memory/reindex/status",
                        headers=headers,
                    )
                    if sr.status_code != 200:
                        print(f"❌ 查询失败: {sr.status_code} {sr.text[:200]}\n")
                        return
                    s = sr.json()
                    if s["processed"] != last_processed:
                        pct = int(s["processed"] * 100 / s["total"]) if s["total"] else 0
                        print(f"  {s['state']}: {s['processed']}/{s['total']} "
                              f"({pct}%) pruned={s['pruned']}")
                        last_processed = s["processed"]
                    if s["state"] in ("success", "error", "idle"):
                        if s["state"] == "success":
                            print(f"\n✅ 完成: total={s['total']} processed={s['processed']} "
                                  f"pruned={s['pruned']}\n")
                        elif s["state"] == "error":
                            print(f"\n❌ 失败: {s.get('error', 'unknown')}\n")
                        else:
                            print("\n⚠️  状态回到 idle, 服务器可能重启过\n")
                        return
        except httpx.RequestError as e:
            print(f"❌ 网络错误: {e}\n")
        except KeyboardInterrupt:
            print("\n⚠️  停止轮询 (后台任务继续)\n")

    async def cmd_memory_prune(self, argv: list[str]) -> None:
        """memory prune [--threshold F] [--dry-run]."""
        import argparse as _argparse
        import httpx

        parser = _argparse.ArgumentParser(prog="memory prune", add_help=False)
        parser.add_argument("--threshold", type=float, default=0.05)
        parser.add_argument("--dry-run", action="store_true")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print("❌ Usage: memory prune [--threshold F] [--dry-run]\n")
            return

        token = await self._panel_token()
        if not token:
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{self._panel_base()}/panel/admin/memory/prune",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"priority_threshold": a.threshold, "dry_run": a.dry_run},
                )
                if r.status_code != 200:
                    print(f"❌ 清理失败: {r.status_code} {r.text[:300]}\n")
                    return
                res = r.json()
                mode = "DRY-RUN" if a.dry_run else "DELETED"
                bd = res["breakdown"]
                print(
                    f"[{mode}] total_before={res['total_before']} "
                    f"would_delete={res['would_delete']} deleted={res['deleted']}\n"
                    f"  forgotten={bd['forgotten']} expired={bd['expired']} "
                    f"low_priority={bd['low_priority']}\n"
                )
        except httpx.RequestError as e:
            print(f"❌ 网络错误: {e}\n")

    async def cmd_memory(self, argv: list[str]) -> None:
        """memory 子命令派发."""
        if not argv:
            print(
                "❌ Usage: memory {reindex|prune} ...\n"
                "  memory reindex [--prune] [--threshold F]\n"
                "  memory prune [--threshold F] [--dry-run]\n"
            )
            return
        sub = argv[0]
        rest = argv[1:]
        if sub == "reindex":
            await self.cmd_memory_reindex(rest)
        elif sub == "prune":
            await self.cmd_memory_prune(rest)
        else:
            print(f"❌ Unknown memory subcommand: {sub}\n")

    async def cmd_persona_reset(self, argv: list[str]) -> None:
        """persona reset [--dry-run] [--yes]. 回到"新装"状态: 清所有长期记忆
        (含 PERMANENT) / 关系 / 短期流水 / 向量库. 不动 API Key / 服务商 / 提示词.
        """
        import argparse as _argparse
        import httpx

        parser = _argparse.ArgumentParser(prog="persona reset", add_help=False)
        parser.add_argument("--dry-run", action="store_true", help="只统计不执行")
        parser.add_argument("--yes", action="store_true", help="跳过交互式确认 (脚本用)")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print("❌ Usage: persona reset [--dry-run] [--yes]\n")
            return

        token = await self._panel_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        base = self._panel_base()

        if not a.dry_run and not a.yes:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(
                        f"{base}/panel/admin/persona/reset",
                        headers=headers,
                        json={"dry_run": True},
                    )
                if r.status_code == 200:
                    res = r.json()
                    print(
                        f"将清空: memories={res['deleted_memories']} "
                        f"relationships={res['deleted_relationships']} "
                        f"conversation_turns={res['deleted_conversation_turns']} + Chroma collection\n"
                    )
                elif r.status_code == 409:
                    print(f"❌ 拒绝: {r.json().get('detail', '')}\n")
                    return
                else:
                    print(f"⚠️  预览失败 ({r.status_code}), 仍可继续\n")
            except httpx.RequestError as e:
                print(f"❌ 网络错误: {e}\n")
                return

            try:
                confirm = input("Type 'yes' to confirm reset: ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm != "yes":
                print("已取消\n")
                return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{base}/panel/admin/persona/reset",
                    headers=headers,
                    json={"dry_run": a.dry_run},
                )
                if r.status_code == 409:
                    print(f"❌ 拒绝: {r.json().get('detail', '')}\n")
                    return
                if r.status_code != 200:
                    print(f"❌ 重置失败: {r.status_code} {r.text[:300]}\n")
                    return
                res = r.json()
                mode = "DRY-RUN" if a.dry_run else "RESET"
                print(
                    f"[{mode}] memories={res['deleted_memories']} "
                    f"relationships={res['deleted_relationships']} "
                    f"conversation_turns={res['deleted_conversation_turns']} "
                    f"vector_reset={res['vector_reset']}"
                )
                if res.get("errors"):
                    print(f"⚠️  部分失败:")
                    for err in res["errors"]:
                        print(f"    - {err}")
                print()
        except httpx.RequestError as e:
            print(f"❌ 网络错误: {e}\n")

    async def cmd_persona(self, argv: list[str]) -> None:
        """persona 子命令派发."""
        if not argv:
            print(
                "❌ Usage: persona reset [--dry-run] [--yes]\n"
                "  persona reset          清空长期记忆/关系/短期流水/向量库 (含 PERMANENT), 保留服务商与 API Key\n"
            )
            return
        sub = argv[0]
        rest = argv[1:]
        if sub == "reset":
            await self.cmd_persona_reset(rest)
        else:
            print(f"❌ Unknown persona subcommand: {sub}\n")

    async def cmd_ask(self, argv: list[str]) -> None:
        """在登入 CLI 内直连主对话 (调试用).

        用法: ask [--user <name>] [--persona-file <path>] [--stream] [--debug] [-v] "<question>"

        复用 src.cli.ask.run_ask, 与 `mnemosync ask` 走同一条 in-process 路径.
        """
        import argparse as _argparse
        from src.cli.ask import run_ask

        parser = _argparse.ArgumentParser(prog="ask", add_help=False, description="in-process 主对话调试")
        parser.add_argument("--user", default="cli")
        parser.add_argument("--persona-file", default=None)
        parser.add_argument("--stream", action="store_true")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--verbose", "-v", action="store_true")
        parser.add_argument("question", nargs="*")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print(
                '❌ Usage: ask [--user <name>] [--persona-file <path>] [--stream] [--debug] "<question>"\n'
            )
            return

        question = " ".join(a.question).strip()
        if not question:
            print('❌ 请提供问题, 如: ask "你好"\n')
            return

        import logging
        if a.verbose:
            logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")

        prev_debug = os.environ.get("MNEMOSYNC_DEBUG")
        try:
            await run_ask(
                question,
                source_user=a.user,
                persona_file=a.persona_file,
                stream=a.stream,
                debug=a.debug,
            )
        finally:
            # 恢复 MNEMOSYNC_DEBUG, 避免污染同一 CLI 会话里后续命令
            if prev_debug is None:
                os.environ.pop("MNEMOSYNC_DEBUG", None)
            else:
                os.environ["MNEMOSYNC_DEBUG"] = prev_debug
        print()

    async def process_command(self, line: str):
        """处理命令."""
        try:
            parts = shlex.split(line.strip())
        except ValueError as e:
            print(f"❌ 无法解析命令 (引号未闭合?): {e}\n")
            return
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd == "help":
                await self.cmd_help()
            elif cmd == "ask":
                await self.cmd_ask(args)
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
            elif cmd == "model":
                await self.cmd_model(args)
            elif cmd == "memory":
                await self.cmd_memory(args)
            elif cmd == "persona":
                await self.cmd_persona(args)
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
