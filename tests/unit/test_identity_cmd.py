"""`mnemosync identity` CLI 测试 (v0.3.0).

走 main(argv) 完整解析路径, MNEMOSYNC_DIR 隔离到临时目录 (不碰真实 data/).
覆盖: strategy 便捷参数创建 / group + bind 归一 / effective_user_id 收敛 /
错误路径退出码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cli.cli import main


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLI 数据隔离到临时目录, cwd 自动恢复."""
    monkeypatch.setenv("MNEMOSYNC_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_strategy_create_with_regex_shortcut_flags(
    isolated: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([
        "identity", "strategy", "create",
        "--name", "AstrBot QQ",
        "--type", "regex",
        "--frontend", "astrbot",
        "--actor-pattern", r"QQ号[:：]\s*(\d+)",
        "--name-pattern", r"用户名[:：]\s*(\S+)",
        "--space-pattern", r"群号[:：]\s*(\d+)",
        "--event-id-pattern", r"消息ID[:：]\s*(\S+)",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "策略已创建" in out

    # 落库的 config 与便捷参数一致
    import sqlite3
    con = sqlite3.connect(isolated / "data" / "identity.db")
    row = con.execute(
        "SELECT name, strategy_type, config FROM identity_strategies"
    ).fetchone()
    con.close()
    assert row[0] == "AstrBot QQ"
    assert row[1] == "regex"
    cfg = json.loads(row[2])
    assert cfg["frontend"] == "astrbot"
    assert cfg["actor_pattern"] == r"QQ号[:：]\s*(\d+)"
    assert cfg["space_pattern"] == r"群号[:：]\s*(\d+)"
    assert cfg["event_id_pattern"] == r"消息ID[:：]\s*(\S+)"


def test_strategy_create_with_raw_config(isolated: Path) -> None:
    rc = main([
        "identity", "strategy", "create",
        "--name", "ChatBox 本地",
        "--type", "api_key_bound",
        "--config", '{"external_key": "local", "frontend": "chatbox"}',
    ])
    assert rc == 0
    import sqlite3
    con = sqlite3.connect(isolated / "data" / "identity.db")
    cfg = json.loads(con.execute(
        "SELECT config FROM identity_strategies"
    ).fetchone()[0])
    con.close()
    assert cfg == {"external_key": "local", "frontend": "chatbox"}


def test_strategy_create_invalid_config_rejected(isolated: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main([
            "identity", "strategy", "create",
            "--name", "bad", "--type", "direct", "--config", "{invalid",
        ])
    assert exc.value.code == 2


def test_group_bind_collapses_effective_user_id(
    isolated: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """bind 后 actor show 的 effective_user_id 收敛到组 ID."""
    # 模拟请求自动建档
    import asyncio
    from src.persistence.identity_store import SqliteIdentityStore

    async def seed() -> str:
        s = SqliteIdentityStore(str(isolated / "data" / "identity.db"))
        await s.connect()
        a = await s.find_or_create_actor("12345", "astrbot", "小明")
        await s.close()
        return a.id

    actor_id = asyncio.run(seed())

    assert main(["identity", "group", "create", "--name", "张三"]) == 0
    out = capsys.readouterr().out
    group_id = out.split("用户组已创建: ")[1].splitlines()[0].strip()

    assert main(["identity", "bind", actor_id, group_id]) == 0
    capsys.readouterr()

    assert main(["identity", "actor", "show", actor_id]) == 0
    out = capsys.readouterr().out
    assert f"effective_user_id: {group_id}" in out
    assert "已归组" in out

    # 组成员列表含该 actor
    assert main(["identity", "group", "show", group_id]) == 0
    out = capsys.readouterr().out
    assert "12345" in out

    # unbind 后回到独立身份
    assert main(["identity", "unbind", actor_id, group_id]) == 0
    capsys.readouterr()
    assert main(["identity", "actor", "show", actor_id]) == 0
    out = capsys.readouterr().out
    assert f"effective_user_id: {actor_id}" in out


def test_bind_unknown_actor_returns_1(isolated: Path) -> None:
    assert main(["identity", "bind", "actor_nope", "group_nope"]) == 1


def test_bind_idempotent(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio
    from src.persistence.identity_store import SqliteIdentityStore

    async def seed() -> tuple[str, str]:
        s = SqliteIdentityStore(str(isolated / "data" / "identity.db"))
        await s.connect()
        a = await s.find_or_create_actor("1", "web")
        g = await s.create_group("g")
        await s.close()
        return a.id, g.id

    actor_id, group_id = asyncio.run(seed())
    assert main(["identity", "bind", actor_id, group_id]) == 0
    capsys.readouterr()
    # 重复绑定 → 提示已在组中, 退出码 0
    assert main(["identity", "bind", actor_id, group_id]) == 0
    assert "已在" in capsys.readouterr().out


def test_strategy_show_unknown_returns_1(isolated: Path) -> None:
    assert main(["identity", "strategy", "show", "strategy_nope"]) == 1


def test_identity_without_subcmd_returns_2(isolated: Path) -> None:
    assert main(["identity"]) == 2
