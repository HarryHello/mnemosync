"""面板后端管理路由: 后端状态 + 启停.

挂在 /panel/admin/backend 前缀下, 需管理员登录 (Depends(get_current_user)).
面板进程自己处理, 不依赖后端进程.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.routes.auth import get_current_user
from src.cli.cli import get_project_root
from src.panel.proxy import BACKEND_BASE

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/backend",
    tags=["Admin", "Backend"],
    dependencies=[Depends(get_current_user)],
)


def _pid_file() -> str:
    return os.path.join(get_project_root(), "data", "backend.pid")


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _get_backend_status() -> dict[str, Any]:
    """读取后端进程状态: pid 存在 + 探测活跃 + /health 可达."""
    pid_file = _pid_file()
    pid: int | None = None
    running = False
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            running = _is_pid_running(pid)
        except (ValueError, OSError):
            pid = None
            running = False

    health = None
    if running:
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                resp = await client.get(f"{BACKEND_BASE}/health")
                if resp.status_code == 200:
                    health = resp.json()
        except httpx.HTTPError:
            health = None
    else:
        # PID 文件缺失/失效不代表真没跑: 端口仍通说明有孤儿后端 (beta.2 实测)
        port = int(os.getenv("MNEMOSYNC_BACKEND_PORT", "16126"))
        try:
            import asyncio as _asyncio

            _, writer = await _asyncio.wait_for(
                _asyncio.open_connection("127.0.0.1", port), timeout=0.6
            )
            writer.close()
            running = True
            pid = None  # 未知 PID, 仅如实显示运行中
        except OSError:
            running = False

    return {
        "running": running,
        "pid": pid,
        "health": health,
        "port": int(os.getenv("MNEMOSYNC_BACKEND_PORT", "16126")),
    }


def _spawn_backend() -> subprocess.Popen[bytes]:
    """后台启动后端进程 (mnemosync backend --daemon)."""
    project_root = get_project_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = project_root
    env["MNEMOSYNC_DIR"] = project_root
    log_file = os.path.join(project_root, "data", "backend.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    log_fh = open(log_file, "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.cli.cli", "backend", "--daemon"],
        cwd=project_root,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_fh.close()
    return proc


@router.get("/status")
async def backend_status() -> dict[str, Any]:
    """查询后端进程状态."""
    return await _get_backend_status()


def _backend_log_tail(max_chars: int = 600) -> str:
    """读取后端日志尾部 (启动失败时返回给面板展示)."""
    log_file = os.path.join(get_project_root(), "data", "backend.log")
    try:
        with open(log_file, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - max_chars))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


async def _wait_backend_running(timeout_s: float = 6.0) -> bool:
    """轮询等待后端进程存活 + /health 就绪 (启动确认)."""
    import asyncio
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = await _get_backend_status()
        if status["running"] and status["health"] is not None:
            return True
        await asyncio.sleep(0.5)
    return False


@router.post("/start")
async def backend_start() -> dict[str, Any]:
    """启动后端进程 (轮询确认真正就绪, 失败带回日志尾部)."""
    status = await _get_backend_status()
    if status["running"]:
        return {"success": True, "message": "后端已在运行", "running": True}
    proc = _spawn_backend()
    ok = await _wait_backend_running()
    if ok:
        logger.info("后端启动就绪 (PID: %s)", proc.pid)
        return {"success": True, "message": f"后端已启动 (PID: {proc.pid})", "running": True}
    logger.warning("后端启动未就绪 (PID: %s)", proc.pid)
    detail = _backend_log_tail()
    message = f"后端启动失败 (PID: {proc.pid} 未在超时内就绪)" + (f":\n{detail}" if detail else "")
    raise HTTPException(status_code=502, detail=message)


@router.post("/stop")
async def backend_stop() -> dict[str, Any]:
    """停止后端进程."""
    from src.cli.cli import _stop_pid_file

    pid_file = _pid_file()
    stopped = _stop_pid_file(pid_file, "后端")
    if not stopped and await _wait_backend_running(timeout_s=1.0):
        raise HTTPException(
            status_code=409,
            detail="未找到后端 PID 记录, 但端口仍有服务监听 (孤儿进程); 请在服务器上用 ss -tlnp 找到 PID 后手动 kill",
        )
    return {"success": True, "message": "后端已停止" if stopped else "后端未运行", "running": False}


@router.post("/restart")
async def backend_restart() -> dict[str, Any]:
    """重启后端进程 (先停后启, 轮询确认)."""
    from src.cli.cli import _stop_pid_file

    _stop_pid_file(_pid_file(), "后端")
    proc = _spawn_backend()
    ok = await _wait_backend_running()
    if ok:
        logger.info("后端重启就绪 (PID: %s)", proc.pid)
        return {"success": True, "message": f"后端已重启 (PID: {proc.pid})", "running": True}
    logger.warning("后端重启未就绪 (PID: %s)", proc.pid)
    detail = _backend_log_tail()
    message = f"后端重启失败 (PID: {proc.pid} 未在超时内就绪)" + (f":\n{detail}" if detail else "")
    raise HTTPException(status_code=502, detail=message)
