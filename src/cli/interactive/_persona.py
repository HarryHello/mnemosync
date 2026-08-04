"""Persona 管理命令 (v0.2.7, via panel HTTP)."""

from __future__ import annotations

import argparse as _argparse


class PersonaCommandsMixin:
    """cmd_persona 子命令族 (reset)."""

    def _panel_base(self) -> str:
        raise NotImplementedError  # 由组合类提供

    async def _panel_token(self) -> str | None:
        raise NotImplementedError  # 由组合类提供

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

    async def cmd_persona_reset(self, argv: list[str]) -> None:
        """persona reset [--dry-run] [--yes]. 回到"新装"状态: 清所有长期记忆
        (含 PERMANENT) / 关系 / 短期流水 / 向量库. 不动 API Key / 服务商 / 提示词.
        """
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
                    print("⚠️  部分失败:")
                    for err in res["errors"]:
                        print(f"    - {err}")
                print()
        except httpx.RequestError as e:
            print(f"❌ 网络错误: {e}\n")
