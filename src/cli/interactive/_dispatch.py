"""命令派发."""

from __future__ import annotations

import shlex


class DispatchMixin:
    """process_command — 解析输入并路由到具体 cmd_* 方法."""

    async def process_command(self, line: str) -> None:
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
