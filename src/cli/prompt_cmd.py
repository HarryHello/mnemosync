"""`mnemosync prompt` — 提示词覆盖管理 (list/show/set/reset/validate).

设计约束:
- CLI 只操作本地文件 (data/prompts + defaults/), 不走 HTTP.
- set 输入源优先级: --file > stdin > --edit.
- --edit 用 $EDITOR (fallback vi), 校验失败提示重编 / --file, 不自动重开.
- reset --all 一次性回默认, 单个 name 才备份到 .history/.
- validate --all 有任何错误 → 退出码 2 (CI 友好).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from src.core.prompts import get_prompt_store
from src.core.prompts.registry import PROMPT_REGISTRY

# ── 展示辅助 ──────────────────────────────────────────────────


def _print_list_table() -> int:
    store = get_prompt_store()
    infos = store.list()
    if not infos:
        print("(无可用提示词)")
        return 0

    headers = ("name", "description", "overridden", "version")
    rows = [
        (info.name, info.description, "yes" if info.overridden else "no", str(info.version))
        for info in infos
    ]
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))
    return 0


def _resolve_name(name: str) -> int | None:
    """校验 name, 未命中打印可用列表并返回退出码; 命中返回 None."""
    if name in PROMPT_REGISTRY:
        return None
    print(f"❌ 未知的提示词: {name!r}", file=sys.stderr)
    print("可用名称:", file=sys.stderr)
    for n in PROMPT_REGISTRY:
        print(f"  - {n}", file=sys.stderr)
    return 2


# ── 子命令实现 ────────────────────────────────────────────────


def _cmd_list(args: argparse.Namespace) -> int:
    return _print_list_table()


def _cmd_show(args: argparse.Namespace) -> int:
    rc = _resolve_name(args.name)
    if rc is not None:
        return rc
    store = get_prompt_store()
    try:
        text = store.load_raw(args.name, default=args.from_default)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _read_input(args: argparse.Namespace) -> str | None:
    """按输入源优先级读入 content. 返回 None 表示无输入源 (调用方判定错误)."""
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            print(f"❌ 文件不存在: {p}", file=sys.stderr)
            return None
        return p.read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def _edit_in_editor(initial_content: str) -> str | None:
    """打开 $EDITOR 编辑 initial_content, 返回编辑后内容; 用户中止返回 None."""
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(
        prefix="mnemosync-prompt-", suffix=".md", delete=False, mode="w", encoding="utf-8"
    ) as tf:
        tf.write(initial_content)
        tmp_path = tf.name
    try:
        result = subprocess.run([editor, tmp_path])
        if result.returncode != 0:
            print(f"⚠️  编辑器退出码 {result.returncode}, 放弃保存", file=sys.stderr)
            return None
        return Path(tmp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _cmd_set(args: argparse.Namespace) -> int:
    rc = _resolve_name(args.name)
    if rc is not None:
        return rc
    store = get_prompt_store()

    if args.edit:
        try:
            initial = store.load_raw(args.name, default=args.from_default)
        except FileNotFoundError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        content = _edit_in_editor(initial)
        if content is None:
            return 1
    else:
        content = _read_input(args)
        if content is None:
            print(
                "❌ 无输入. 请通过 --file, stdin 管道, 或 --edit 提供内容.",
                file=sys.stderr,
            )
            return 2

    result = store.validate(args.name, content)
    if not result.ok:
        print(f"❌ 校验失败: {result.error}", file=sys.stderr)
        if args.edit:
            print(
                "提示: 用 `mnemosync prompt set <name> --edit` 重新编辑, "
                "或 `--file <path>` 传入修正版本.",
                file=sys.stderr,
            )
        return 3

    try:
        store.save(args.name, content)
    except ValueError as e:  # 理论上不会到这里, validate 已挡住
        print(f"❌ 保存失败: {e}", file=sys.stderr)
        return 3

    print(f"✅ 已保存覆盖: {args.name}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    store = get_prompt_store()
    if args.all:
        touched: list[str] = []
        for n in PROMPT_REGISTRY:
            if store.reset(n):
                touched.append(n)
        if touched:
            print(f"✅ 已重置 {len(touched)} 个提示词: {', '.join(touched)}")
        else:
            print("ℹ️  没有需要重置的覆盖")
        return 0

    if not args.name:
        print("❌ 需指定 <name> 或 --all", file=sys.stderr)
        return 2
    rc = _resolve_name(args.name)
    if rc is not None:
        return rc
    if store.reset(args.name):
        print(f"✅ 已重置: {args.name}")
    else:
        print(f"ℹ️  {args.name} 无覆盖, 无需操作")
    return 0


def _validate_one(name: str) -> tuple[bool, str]:
    """返回 (ok, message)."""
    store = get_prompt_store()
    override = store.override_dir / f"{name}.md"
    if not override.is_file():
        return True, f"{name}: (无覆盖, 使用默认)"
    content = override.read_text(encoding="utf-8")
    result = store.validate(name, content)
    if result.ok:
        return True, f"{name}: OK"
    return False, f"{name}: {result.error}"


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.all:
        all_ok = True
        for n in PROMPT_REGISTRY:
            ok, msg = _validate_one(n)
            if not ok:
                all_ok = False
            print(msg)
        return 0 if all_ok else 2

    if not args.name:
        print("❌ 需指定 <name> 或 --all", file=sys.stderr)
        return 2
    rc = _resolve_name(args.name)
    if rc is not None:
        return rc
    ok, msg = _validate_one(args.name)
    print(msg)
    return 0 if ok else 3


# ── 入口 ──────────────────────────────────────────────────────


def build_parser(sub: argparse._SubParsersAction) -> None:
    """在 mnemosync 主 parser 上注册 `prompt` 子命令树."""
    p = sub.add_parser("prompt", help="管理 Agent 提示词覆盖 (list/show/set/reset/validate)")
    sp = p.add_subparsers(dest="subcmd", help="子命令")

    # list
    sp_list = sp.add_parser("list", help="列出所有提示词及覆盖状态")
    sp_list.set_defaults(_prompt_subcmd=_cmd_list)

    # show
    sp_show = sp.add_parser("show", help="打印当前生效的提示词到 stdout")
    sp_show.add_argument("name", help="提示词名 (见 `prompt list`)")
    sp_show.add_argument(
        "--from-default", action="store_true", help="强制打印默认版本 (忽略覆盖)"
    )
    sp_show.set_defaults(_prompt_subcmd=_cmd_show)

    # set
    sp_set = sp.add_parser("set", help="设置覆盖版本 (--file/stdin/--edit)")
    sp_set.add_argument("name", help="提示词名")
    sp_set.add_argument("--file", default=None, help="从文件读取内容")
    sp_set.add_argument("--edit", action="store_true", help="用 $EDITOR 打开当前生效版本编辑")
    sp_set.add_argument(
        "--from-default",
        action="store_true",
        help="配合 --edit 使用: 从默认版本开始编辑 (忽略现有覆盖)",
    )
    sp_set.set_defaults(_prompt_subcmd=_cmd_set)

    # reset
    sp_reset = sp.add_parser("reset", help="删除覆盖, 回到默认")
    sp_reset.add_argument("name", nargs="?", default=None, help="提示词名 (与 --all 二选一)")
    sp_reset.add_argument("--all", action="store_true", help="重置所有覆盖")
    sp_reset.set_defaults(_prompt_subcmd=_cmd_reset)

    # validate
    sp_val = sp.add_parser("validate", help="校验覆盖是否含齐全部占位符")
    sp_val.add_argument("name", nargs="?", default=None)
    sp_val.add_argument("--all", action="store_true", help="校验全部")
    sp_val.set_defaults(_prompt_subcmd=_cmd_validate)

    p.set_defaults(func=cmd_prompt)


def cmd_prompt(args: argparse.Namespace) -> int:
    """`mnemosync prompt` 分派器."""
    handler = getattr(args, "_prompt_subcmd", None)
    if handler is None:
        # 未指定子命令 → 显示 list
        return _cmd_list(args)
    return handler(args)


__all__ = ["build_parser", "cmd_prompt"]
