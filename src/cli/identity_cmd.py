"""`mnemosync identity` — 多用户身份管理 (v0.3.0).

子命令:
  identity strategy list / create / show / update / delete   身份识别策略
  identity actor list / show                                 参与者 (只读, 系统自动创建)
  identity group list / create / show                        用户组 (一个真实人)
  identity bind / unbind <actor_id> <group_id>               绑定 / 解绑

设计约束 (与 prompt_cmd 一致):
- CLI 直连本地 data/identity.db, 不走 HTTP。
- 输出为对齐表格, 机器可解析。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from src.cli.cli import get_project_root
from src.persistence.identity_store import SqliteIdentityStore

_IDENTITY_DB_PATH = "data/identity.db"

VALID_STRATEGY_TYPES = ("direct", "api_key_bound", "regex", "llm")


# ── 工具 ──────────────────────────────────────────────────────


def _store() -> SqliteIdentityStore:
    os.chdir(get_project_root())
    return SqliteIdentityStore(_IDENTITY_DB_PATH)


def _run(coro):
    """运行协程并确保 store 连接关闭."""
    return asyncio.run(coro)


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    if not rows:
        print("(空)")
        return
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _pretty_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except Exception:
        return raw


def _build_config(args: argparse.Namespace) -> str:
    """从 --config / --config-file / 类型便捷参数构建策略 config JSON.

    优先级: --config > --config-file > 类型便捷参数。
    """
    if args.config:
        raw = args.config
    elif args.config_file:
        from pathlib import Path

        p = Path(args.config_file)
        if not p.is_file():
            print(f"❌ 配置文件不存在: {p}", file=sys.stderr)
            sys.exit(2)
        raw = p.read_text(encoding="utf-8")
    else:
        cfg: dict[str, Any] = {}
        stype = getattr(args, "type", None)
        if stype == "direct":
            if args.frontend:
                cfg["frontend"] = args.frontend
        elif stype == "api_key_bound":
            if args.external_key:
                cfg["external_key"] = args.external_key
            if args.frontend:
                cfg["frontend"] = args.frontend
            if args.display_name:
                cfg["display_name"] = args.display_name
            if args.channel_type:
                cfg["channel_type"] = args.channel_type
        elif stype == "regex":
            if args.frontend:
                cfg["frontend"] = args.frontend
            if args.actor_pattern:
                cfg["actor_pattern"] = args.actor_pattern
            if args.name_pattern:
                cfg["name_pattern"] = args.name_pattern
            if args.space_pattern:
                cfg["space_pattern"] = args.space_pattern
            if args.event_id_pattern:
                cfg["event_id_pattern"] = args.event_id_pattern
            if args.search_in:
                cfg["search_in"] = args.search_in
        raw = json.dumps(cfg, ensure_ascii=False)
    # 校验 JSON 合法性
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("config 必须是 JSON 对象")
    except ValueError as e:
        print(f"❌ config 不合法: {e}", file=sys.stderr)
        sys.exit(2)
    return raw


# ── strategy 子命令 ───────────────────────────────────────────


def _cmd_strategy_list(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            strategies, total = await store.list_strategies(limit=500)
        finally:
            await store.close()
        rows = [
            (s.id, s.name, s.strategy_type, "yes" if s.is_active else "no")
            for s in strategies
        ]
        _print_table(("id", "name", "type", "active"), rows)
        print(f"\n共 {total} 个策略")
        return 0

    return _run(run())


def _cmd_strategy_create(args: argparse.Namespace) -> int:
    config = _build_config(args)

    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            s = await store.create_strategy(
                name=args.name, strategy_type=args.type, config=config,
            )
        finally:
            await store.close()
        print(f"✅ 策略已创建: {s.id}")
        print(f"   名称: {s.name}  类型: {s.strategy_type}")
        print("   配置:")
        for line in _pretty_json(s.config).splitlines():
            print(f"     {line}")
        print("\n下一步: 创建 API Key 时绑定该策略 (面板 API Key 页, 或 key 的 strategy_id 字段)")
        return 0

    return _run(run())


def _cmd_strategy_show(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            s = await store.get_strategy(args.strategy_id)
        finally:
            await store.close()
        if s is None:
            print(f"❌ 策略不存在: {args.strategy_id}", file=sys.stderr)
            return 1
        print(f"id:           {s.id}")
        print(f"name:         {s.name}")
        print(f"strategy_type: {s.strategy_type}")
        print(f"is_active:    {s.is_active}")
        print(f"created_at:   {s.created_at}")
        print(f"updated_at:   {s.updated_at}")
        print("config:")
        for line in _pretty_json(s.config).splitlines():
            print(f"  {line}")
        return 0

    return _run(run())


def _cmd_strategy_update(args: argparse.Namespace) -> int:
    if not args.name and not args.config and not args.config_file \
            and args.active is None:
        print("❌ 至少指定一项: --name / --config / --config-file / --active / --inactive",
              file=sys.stderr)
        return 2

    new_config: str | None = None
    if args.config or args.config_file:
        new_config = _build_config(args)

    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            existing = await store.get_strategy(args.strategy_id)
            if existing is None:
                print(f"❌ 策略不存在: {args.strategy_id}", file=sys.stderr)
                return 1
            name = args.name if args.name else existing.name
            config = new_config if new_config is not None else existing.config
            is_active = bool(args.active) if args.active is not None else existing.is_active
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            async with store._conn() as db:  # noqa: SLF001 — CLI 与 store 同仓维护
                await db.execute(
                    "UPDATE identity_strategies SET name = ?, config = ?, is_active = ?, updated_at = ? "
                    "WHERE id = ?",
                    (name, config, 1 if is_active else 0, now.isoformat(), args.strategy_id),
                )
                await db.commit()
        finally:
            await store.close()
        print(f"✅ 策略已更新: {args.strategy_id}")
        return 0

    return _run(run())


def _cmd_strategy_delete(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            s = await store.get_strategy(args.strategy_id)
            if s is None:
                print(f"❌ 策略不存在: {args.strategy_id}", file=sys.stderr)
                return 1
            async with store._conn() as db:  # noqa: SLF001
                await db.execute(
                    "DELETE FROM identity_strategies WHERE id = ?", (args.strategy_id,),
                )
                await db.commit()
        finally:
            await store.close()
        print(f"✅ 策略已删除: {args.strategy_id} ({s.name})")
        print("   注意: 引用该策略的 API Key 将失去身份解析能力 (请求进入非归属模式)")
        return 0

    return _run(run())


# ── actor 子命令 ──────────────────────────────────────────────


def _cmd_actor_list(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            actors, total = await store.list_actors(limit=500)
        finally:
            await store.close()
        q = (args.search or "").lower()
        rows = []
        for a in actors:
            if args.frontend and a.frontend != args.frontend:
                continue
            if q and not (
                q in a.external_key.lower()
                or q in (a.display_name or "").lower()
                or q in a.id.lower()
            ):
                continue
            rows.append((a.id, a.frontend, a.external_key, a.display_name or "-"))
        _print_table(("id", "frontend", "external_key", "display_name"), rows)
        print(f"\n共 {total} 个参与者" + (" (已按条件过滤)" if (args.frontend or q) else ""))
        return 0

    return _run(run())


def _cmd_actor_show(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            a = await store.get_actor(args.actor_id)
            if a is None:
                print(f"❌ 参与者不存在: {args.actor_id}", file=sys.stderr)
                print("   用 `mnemosync identity actor list` 查看可用 ID", file=sys.stderr)
                return 1
            groups = await store.list_actor_groups(args.actor_id)
            effective = await store.get_effective_user_id(args.actor_id)
        finally:
            await store.close()
        print(f"id:            {a.id}")
        print(f"frontend:      {a.frontend}")
        print(f"external_key:  {a.external_key}")
        print(f"display_name:  {a.display_name or '-'}")
        print(f"created_at:    {a.created_at}")
        print(f"effective_user_id: {effective}"
              + ("  (独立身份)" if effective == a.id else "  (已归组, 共享组记忆)"))
        if groups:
            print("所属用户组:")
            for g in groups:
                print(f"  - {g.id}  {g.name or '(未命名)'}")
        else:
            print("所属用户组: (无)")
        return 0

    return _run(run())


# ── group 子命令 ──────────────────────────────────────────────


def _cmd_group_list(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            groups, total = await store.list_groups(limit=500)
            rows = []
            for g in groups:
                members = await store.list_group_members(g.id)
                rows.append((g.id, g.name or "-", str(len(members))))
        finally:
            await store.close()
        _print_table(("id", "name", "members"), rows)
        print(f"\n共 {total} 个用户组")
        return 0

    return _run(run())


def _cmd_group_create(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            g = await store.create_group(name=args.name)
        finally:
            await store.close()
        print(f"✅ 用户组已创建: {g.id}")
        if g.name:
            print(f"   名称: {g.name}")
        print("\n下一步: mnemosync identity bind <actor_id> " + g.id)
        return 0

    return _run(run())


def _cmd_group_show(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            g = await store.get_group(args.group_id)
            if g is None:
                print(f"❌ 用户组不存在: {args.group_id}", file=sys.stderr)
                return 1
            members = await store.list_group_members(args.group_id)
        finally:
            await store.close()
        print(f"id:         {g.id}")
        print(f"name:       {g.name or '-'}")
        print(f"created_at: {g.created_at}")
        print(f"成员 ({len(members)}):")
        rows = [
            (m.id, m.frontend, m.external_key, m.display_name or "-")
            for m in members
        ]
        _print_table(("actor_id", "frontend", "external_key", "display_name"), rows)
        return 0

    return _run(run())


# ── bind / unbind ─────────────────────────────────────────────


def _cmd_bind(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            actor = await store.get_actor(args.actor_id)
            if actor is None:
                print(f"❌ 参与者不存在: {args.actor_id}", file=sys.stderr)
                return 1
            group = await store.get_group(args.group_id)
            if group is None:
                print(f"❌ 用户组不存在: {args.group_id}", file=sys.stderr)
                return 1
            ok = await store.bind_actor_to_group(args.actor_id, args.group_id)
        finally:
            await store.close()
        if not ok:
            print("ℹ️  该参与者已在此用户组中")
            return 0
        print(f"✅ 已绑定: {actor.external_key} ({actor.frontend}) → 组 {args.group_id} ({group.name or '未命名'})")
        print("   从现在起, 该身份的记忆与关系与组内其他身份共享 (effective_user_id = 组 ID)")
        return 0

    return _run(run())


def _cmd_unbind(args: argparse.Namespace) -> int:
    async def run() -> int:
        store = _store()
        try:
            await store.connect()
            ok = await store.unbind_actor_from_group(args.actor_id, args.group_id)
        finally:
            await store.close()
        if not ok:
            print("ℹ️  该参与者不在此用户组中")
            return 0
        print(f"✅ 已解绑: {args.actor_id} ← 组 {args.group_id}")
        print("   该身份回到独立身份; 已有组记忆不迁移")
        return 0

    return _run(run())


# ── 分派与注册 ────────────────────────────────────────────────


def cmd_identity(args: argparse.Namespace) -> int:
    """`mnemosync identity` 分派器."""
    handler = getattr(args, "_identity_subcmd", None)
    if handler is None:
        print("❌ 请指定子命令, 例如: mnemosync identity strategy list", file=sys.stderr)
        return 2
    return handler(args)


def build_parser(sub: argparse._SubParsersAction) -> None:
    """在 mnemosync 主 parser 上注册 `identity` 子命令树."""
    p = sub.add_parser(
        "identity",
        help="多用户身份管理 (策略 / 参与者 / 用户组 / 绑定)",
    )
    sp = p.add_subparsers(dest="subcmd", help="子命令")

    # ── strategy ──
    sp_strategy = sp.add_parser("strategy", help="身份识别策略管理")
    strategy_sp = sp_strategy.add_subparsers(dest="strategy_cmd")

    st_list = strategy_sp.add_parser("list", help="列出所有策略")
    st_list.set_defaults(_identity_subcmd=_cmd_strategy_list)

    st_create = strategy_sp.add_parser("create", help="创建策略")
    st_create.add_argument("--name", required=True, help="策略名称")
    st_create.add_argument("--type", required=True, choices=VALID_STRATEGY_TYPES,
                           help="策略类型")
    st_create.add_argument("--config", default=None, help="config JSON 字符串 (优先)")
    st_create.add_argument("--config-file", default=None, help="从文件读取 config JSON")
    # 便捷参数 (与 --config 二选一; 按类型生效)
    st_create.add_argument("--frontend", default=None,
                           help="前台应用名 (direct/api_key_bound/regex)")
    st_create.add_argument("--external-key", default=None,
                           help="固定平台标识 (api_key_bound)")
    st_create.add_argument("--display-name", default=None,
                           help="显示名称 (api_key_bound)")
    st_create.add_argument("--channel-type", default=None, choices=["direct", "group"],
                           help="渠道类型 (api_key_bound)")
    st_create.add_argument("--actor-pattern", default=None,
                           help="参与者提取正则, 第 1 个捕获组为 external_key (regex)")
    st_create.add_argument("--name-pattern", default=None,
                           help="昵称提取正则 (regex)")
    st_create.add_argument("--space-pattern", default=None,
                           help="空间/群号提取正则 (regex)")
    st_create.add_argument("--event-id-pattern", default=None,
                           help="事件 ID 提取正则, 幂等用 (regex)")
    st_create.add_argument("--search-in", default=None,
                           choices=["system_or_first_user", "system", "all"],
                           help="正则搜索范围 (regex, 默认 system_or_first_user)")
    st_create.set_defaults(_identity_subcmd=_cmd_strategy_create)

    st_show = strategy_sp.add_parser("show", help="查看策略详情 (含 config)")
    st_show.add_argument("strategy_id", help="策略 ID")
    st_show.set_defaults(_identity_subcmd=_cmd_strategy_show)

    st_update = strategy_sp.add_parser("update", help="更新策略 (名称 / 配置 / 启停)")
    st_update.add_argument("strategy_id", help="策略 ID")
    st_update.add_argument("--name", default=None, help="新名称")
    st_update.add_argument("--config", default=None, help="新 config JSON 字符串")
    st_update.add_argument("--config-file", default=None, help="从文件读取新 config")
    active_group = st_update.add_mutually_exclusive_group()
    active_group.add_argument("--active", dest="active", action="store_const",
                              const=1, default=None, help="启用")
    active_group.add_argument("--inactive", dest="active", action="store_const",
                              const=0, help="停用")
    # _build_config 用到的占位 (update 仅用 --config/--config-file)
    st_update.set_defaults(_identity_subcmd=_cmd_strategy_update,
                           type=None, frontend=None, external_key=None,
                           display_name=None, channel_type=None, actor_pattern=None,
                           name_pattern=None, space_pattern=None,
                           event_id_pattern=None, search_in=None)

    st_delete = strategy_sp.add_parser("delete", help="删除策略")
    st_delete.add_argument("strategy_id", help="策略 ID")
    st_delete.set_defaults(_identity_subcmd=_cmd_strategy_delete)

    # ── actor ──
    sp_actor = sp.add_parser("actor", help="参与者 (只读, 系统自动创建)")
    actor_sp = sp_actor.add_subparsers(dest="actor_cmd")

    ac_list = actor_sp.add_parser("list", help="列出参与者")
    ac_list.add_argument("--frontend", default=None, help="按前台应用过滤")
    ac_list.add_argument("--search", default=None, help="按 平台标识/昵称/ID 模糊过滤")
    ac_list.set_defaults(_identity_subcmd=_cmd_actor_list)

    ac_show = actor_sp.add_parser("show", help="查看参与者详情 (含组归属与 effective_user_id)")
    ac_show.add_argument("actor_id", help="参与者 ID")
    ac_show.set_defaults(_identity_subcmd=_cmd_actor_show)

    # ── group ──
    sp_group = sp.add_parser("group", help="用户组 (一个真实人)")
    group_sp = sp_group.add_subparsers(dest="group_cmd")

    gr_list = group_sp.add_parser("list", help="列出用户组 (含成员数)")
    gr_list.set_defaults(_identity_subcmd=_cmd_group_list)

    gr_create = group_sp.add_parser("create", help="创建用户组")
    gr_create.add_argument("--name", default=None, help="组名称 (可选, 便于辨认)")
    gr_create.set_defaults(_identity_subcmd=_cmd_group_create)

    gr_show = group_sp.add_parser("show", help="查看用户组成员")
    gr_show.add_argument("group_id", help="用户组 ID")
    gr_show.set_defaults(_identity_subcmd=_cmd_group_show)

    # ── bind / unbind ──
    sp_bind = sp.add_parser("bind", help="把参与者绑定到用户组 (跨平台身份归一)")
    sp_bind.add_argument("actor_id", help="参与者 ID")
    sp_bind.add_argument("group_id", help="用户组 ID")
    sp_bind.set_defaults(_identity_subcmd=_cmd_bind)

    sp_unbind = sp.add_parser("unbind", help="解除参与者的用户组绑定")
    sp_unbind.add_argument("actor_id", help="参与者 ID")
    sp_unbind.add_argument("group_id", help="用户组 ID")
    sp_unbind.set_defaults(_identity_subcmd=_cmd_unbind)

    p.set_defaults(func=cmd_identity)


__all__ = ["build_parser", "cmd_identity"]
