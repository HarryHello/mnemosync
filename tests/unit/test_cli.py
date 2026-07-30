"""CLI 模块单元测试."""

from __future__ import annotations

import pytest
from src.cli.cli import get_project_root, is_docker_installed, main


def test_get_project_root_returns_string() -> None:
    """get_project_root 应返回项目根路径字符串."""
    root = get_project_root()
    assert isinstance(root, str)
    assert len(root) > 0


def test_is_docker_installed_returns_bool() -> None:
    """is_docker_installed 应返回布尔值 (不抛异常)."""
    result = is_docker_installed()
    assert isinstance(result, bool)


def test_main_help_runs(capsys) -> None:
    """`mnemosync help` 应正常退出并打印帮助."""
    rc = main(["help"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Mnemosync" in captured.out or "mnemosync" in captured.out.lower()


def test_main_unknown_command_exits_nonzero() -> None:
    """未知命令应通过 SystemExit 返回非零退出码."""
    with pytest.raises(SystemExit) as exc_info:
        main(["__nonexistent_command_xyz__"])
    assert exc_info.value.code != 0


def test_main_no_args_shows_help(capsys) -> None:
    """无参数调用应打印帮助/用法."""
    rc = main([])
    # 无参数默认打印帮助并返回 0
    assert rc == 0
    captured = capsys.readouterr()
    assert len(captured.out) > 0
