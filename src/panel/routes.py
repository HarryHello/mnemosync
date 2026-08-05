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
from fastapi import APIRouter, Depends

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


@router.post("/start")
async def backend_start() -> dict[str, Any]:
    """启动后端进程."""
    status = await _get_backend_status()
    if status["running"]:
        return {"success": True, "message": "后端已在运行", "running": True}
    proc = _spawn_backend()
    logger.info("后端启动已触发 (PID: %s)", proc.pid)
    return {"success": True, "message": f"后端启动中 (PID: {proc.pid})...", "running": False}


@router.post("/stop")
async def backend_stop() -> dict[str, Any]:
    """停止后端进程."""
    from src.cli.cli import _stop_pid_file

    pid_file = _pid_file()
    stopped = _stop_pid_file(pid_file, "后端")
    return {"success": True, "message": "后端已停止" if stopped else "后端未运行", "running": False}


@router.post("/restart")
async def backend_restart() -> dict[str, Any]:
    """重启后端进程 (先停后启)."""
    from src.cli.cli import _stop_pid_file

    _stop_pid_file(_pid_file(), "后端")
    proc = _spawn_backend()
    logger.info("后端重启已触发 (PID: %s)", proc.pid)
    return {"success": True, "message": f"后端重启中 (PID: {proc.pid})...", "running": False}
