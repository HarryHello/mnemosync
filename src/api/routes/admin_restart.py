"""管理 API 路由 - 服务重启.

提供一键重启服务端点, 复用 CLI 的 ``mnemosync restart`` 逻辑.

**认证**: 所有路由要求登录 (Depends(get_current_user)).
"""

import logging
import os
import subprocess
import sys

from fastapi import APIRouter, Depends

from src.api.routes.auth import get_current_user
from src.cli.cli import get_project_root

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/restart")
async def restart_service():
    """重启服务.

    通过 subprocess 触发 ``src.cli.cli restart``, 并立即返回响应.
    因为重启会杀掉当前进程, 这里不能 await 重启完成 —— 子进程
    ``start_new_session=True`` 脱离当前会话, 由 CLI 负责停止旧进程并
    后台拉起新进程 (复用 cmd_serve 的 daemon 分支).
    """
    project_root = get_project_root()

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", project_root)
    env["MNEMOSYNC_DIR"] = project_root  # 子进程复用同一安装目录

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.cli.cli", "restart"],
        cwd=project_root,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    logger.info("服务重启已触发 (PID: %s)", proc.pid)
    return {"success": True, "message": f"服务重启中 (PID: {proc.pid})..."}
