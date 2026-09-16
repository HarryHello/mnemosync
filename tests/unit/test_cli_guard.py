"""CLI 重复启动守卫测试 (beta.20).

回归: `mnemosync panel -d` 连按两次会得到「假成功 + PID 文件被覆盖 +
失联孤儿」— uvicorn 绑端口失败的秒死晚于 _run_daemon 的 1.5s 观察窗,
必须在派发前用 PID 文件/端口探测拦截.
"""

from __future__ import annotations

import socket
import subprocess
import sys

from src.cli.cli import _guard_duplicate_start, _pid_file_alive


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
