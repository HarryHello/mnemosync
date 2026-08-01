"""Memory maintenance 命令 (v0.2.4, via panel HTTP)."""

from __future__ import annotations

import argparse as _argparse
import asyncio
import os


class MemoryCommandsMixin:
    """cmd_memory 子命令族 (reindex / prune)."""

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

    async def cmd_memory_reindex(self, argv: list[str]) -> None:
        """memory reindex [--prune] [--threshold F]. 阻塞到完成."""
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
