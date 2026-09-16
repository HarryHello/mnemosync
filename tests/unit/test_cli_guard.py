"""CLI 重复启动守卫测试 (beta.20).

回归: `mnemosync panel -d` 连按两次会得到「假成功 + PID 文件被覆盖 +
失联孤儿」— uvicorn 绑端口失败的秒死晚于 _run_daemon 的 1.5s 观察窗,
必须在派发前用 PID 文件/端口探测拦截.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

from src.cli.cli import (
    _guard_duplicate_start,
    _pid_file_alive,
    _stop_pid_file,
)


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_pid_file_alive_returns_pid_for_live_process(tmp_path) -> None:
    proc = _spawn_sleeper()
    try:
        pid_file = tmp_path / "p.pid"
        pid_file.write_text(str(proc.pid))
        assert _pid_file_alive(str(pid_file)) == proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_pid_file_alive_none_for_dead_process(tmp_path) -> None:
    proc = _spawn_sleeper()
    pid = proc.pid
    proc.kill()
    proc.wait()
    pid_file = tmp_path / "p.pid"
    pid_file.write_text(str(pid))
    assert _pid_file_alive(str(pid_file)) is None


def test_pid_file_alive_none_for_missing_or_garbage(tmp_path) -> None:
    assert _pid_file_alive(str(tmp_path / "missing.pid")) is None
    bad = tmp_path / "garbage.pid"
    bad.write_text("not-a-pid")
    assert _pid_file_alive(str(bad)) is None


def test_guard_blocks_when_pid_alive(tmp_path) -> None:
    proc = _spawn_sleeper()
    try:
        pid_file = tmp_path / "p.pid"
        pid_file.write_text(str(proc.pid))
        # 端口 1 (拒绝连接) — 只验证 PID 分支生效
        assert _guard_duplicate_start("面板", str(pid_file), 1) is True
    finally:
        proc.kill()
        proc.wait()


def test_guard_passes_when_pid_is_self(tmp_path) -> None:
    """回归 (beta.21): daemon 父进程写的是子进程自己的 PID.

    子进程读到自己的 PID 时不得把自己当成"已在运行"而拒绝启动 —
    否则面板永远起不来 (beta.21 服务器实测: panel -d 假成功后子进程
    自我拒绝退出, 日志里留下「面板已在运行 (PID: 自身)」).
    """
    import os

    pid_file = tmp_path / "p.pid"
    pid_file.write_text(str(os.getpid()))

    # 端口空闲 → 不拦截
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    free_port = srv.getsockname()[1]
    srv.close()
    assert _guard_duplicate_start("面板", str(pid_file), free_port) is False

    # 端口被占 → 仍要拦截 (真重复)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        occupied = srv.getsockname()[1]
        assert _guard_duplicate_start("面板", str(pid_file), occupied) is True
    finally:
        srv.close()


def test_stop_pid_file_refuses_foreign_process(tmp_path) -> None:
    """回归 (beta.22): PID 复用防护 — pid 文件指向无关进程时不得杀.

    服务器重启后 PID 段重排, 陈旧 pid 文件可能被无关进程接手;
    stop 必须核对 /proc cmdline 含 mnemosync 标识才允许 SIGTERM.
    macOS 无 /proc (防护不可用, 保持旧行为), 仅 Linux 验证.
    """
    if not os.path.isdir("/proc"):
        import pytest

        pytest.skip("PID 复用防护依赖 /proc, 仅 Linux")
    proc = _spawn_sleeper()
    try:
        pid_file = tmp_path / "foreign.pid"
        pid_file.write_text(str(proc.pid))
        stopped = _stop_pid_file(str(pid_file), "面板")
        assert stopped is False  # 拒绝
        assert proc.poll() is None  # 进程还活着 (未被 SIGTERM)
        assert not pid_file.exists()  # 陈旧文件已清理
    finally:
        proc.kill()
        proc.wait()


def test_stop_pid_file_kills_mnemosync_like_process(tmp_path) -> None:
    """cmdline 含 mnemosync 标识的进程照常停止."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "mnemosync"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid_file = tmp_path / "ours.pid"
        pid_file.write_text(str(proc.pid))
        assert _stop_pid_file(str(pid_file), "服务") is True
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_guard_blocks_when_port_occupied(tmp_path) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        port = srv.getsockname()[1]
        assert _guard_duplicate_start("面板", str(tmp_path / "p.pid"), port) is True
    finally:
        srv.close()


def test_guard_passes_when_clean(tmp_path) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    free_port = srv.getsockname()[1]
    srv.close()
    assert _guard_duplicate_start("面板", str(tmp_path / "p.pid"), free_port) is False
