"""Ask (in-process 主对话调试) 命令."""

from __future__ import annotations

import argparse as _argparse
import logging
import os


class AskCommandsMixin:
    """cmd_ask."""

    async def cmd_ask(self, argv: list[str]) -> None:
        """在登入 CLI 内直连主对话 (调试用).

        用法: ask --user <name> [--persona-file <path>] [--stream] [--debug] [-v] "<question>"

        复用 src.cli.ask.run_ask, 与 `mnemosync ask` 走同一条 in-process 路径.
        """
        from src.cli.ask import run_ask

        parser = _argparse.ArgumentParser(prog="ask", add_help=False, description="in-process 主对话调试")
        parser.add_argument("--user", required=True, help="source_user 标识 (必填)")
        parser.add_argument("--persona-file", default=None)
        parser.add_argument("--stream", action="store_true")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--verbose", "-v", action="store_true")
        parser.add_argument("question", nargs="*")
        try:
            a = parser.parse_args(argv)
        except SystemExit:
            print(
                '❌ Usage: ask --user <name> [--persona-file <path>] [--stream] [--debug] "<question>"\n'
            )
            return

        question = " ".join(a.question).strip()
        if not question:
            print('❌ 请提供问题, 如: ask "你好"\n')
            return

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
