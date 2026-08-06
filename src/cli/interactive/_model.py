"""Model binding 命令 (role_bindings 表)."""

from __future__ import annotations

import argparse as _argparse

from src.infra.llm_service.models import ModelType
from src.infra.llm_service.store import LLMServiceStore


class ModelCommandsMixin:
    """cmd_model 子命令族."""

    _VALID_ROLES = {mt.value for mt in ModelType}
    llm_service_store: LLMServiceStore

    def _parse_role(self, role: str) -> ModelType | None:
        if role not in self._VALID_ROLES:
            print(f"❌ role 必须是 {sorted(self._VALID_ROLES)} 之一\n")
            return None
        return ModelType(role)

    async def cmd_model(self, argv: list[str]) -> None:
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

    async def cmd_model_ls(self, role: str | None = None) -> None:
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

    async def cmd_model_add(self, argv: list[str]) -> None:
        """model add <role> <service_id> <model> [--priority N] [--context N] [--dim N] [--send-dim]."""
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

    async def cmd_model_rm(self, role: str, priority_str: str) -> None:
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

    async def cmd_model_reorder(self, role: str, spec: str) -> None:
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

    async def cmd_model_test(self, role: str) -> None:
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

    async def cmd_test_model(self, service_id: str, model: str) -> None:
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
