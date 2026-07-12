"""Mnemosync CLI - 统一入口.

支持两种运行模式:
  - 本地模式: 直接运行 Python 进程
  - Docker 模式: 通过 Docker 容器运行
"""

import argparse
import os
import subprocess
import sys


# ============================================================================
# 工具函数
# ============================================================================

def get_project_root() -> str:
    """获取项目根目录."""
    # 优先使用环境变量
    if os.getenv("MNEMOSYNC_DIR"):
        return os.getenv("MNEMOSYNC_DIR")

    # 使用脚本所在位置向上查找
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    return project_root


def is_docker_installed() -> bool:
    """检查 Docker 是否已安装."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def is_container_running() -> bool:
    """检查容器是否正在运行."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        return "running" in result.stdout.lower()
    except Exception:
        return False


def run_docker_command(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """运行 Docker Compose 命令."""
    project_root = get_project_root()
    cmd = ["docker", "compose", "-f", f"{project_root}/docker-compose.yml"] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        return subprocess.run(cmd)


# ============================================================================
# 本地模式命令
# ============================================================================

def cmd_serve(args: argparse.Namespace) -> int:
    """启动服务（本地模式）."""
    project_root = get_project_root()

    # 设置环境变量
    os.chdir(project_root)
    os.environ.setdefault("PYTHONPATH", project_root)

    # 导入并运行服务器
    try:
        import uvicorn
        from fastapi import FastAPI
        from src.api import api_router, forward_router
    except ImportError as e:
        print(f"❌ 依赖未安装: {e}")
        print("请运行: uv sync")
        return 1

    app = FastAPI(
        title="Mnemosync API",
        description="智能代理中间件 - LLM 上下文编排与人格记忆管理",
        version="0.2.0",
    )

    app.include_router(api_router)
    app.include_router(forward_router)

    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "16125"))

    print(f"🚀 Mnemosync 启动中... http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)
    return 0


def cmd_init_local(args: argparse.Namespace) -> int:
    """初始化数据库（本地模式）."""
    project_root = get_project_root()
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)

    print("🔧 初始化数据库...")

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "init-internal"],
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": project_root}
    )

    if result.returncode != 0:
        print("❌ 初始化失败")
        return 1

    print("\n✅ 初始化完成")
    print("\n下一步:")
    print("  1. 编辑 config.local.toml 填入你的 LLM API Key")
    print("  2. 运行 'mnemosync serve' 启动服务")
    print()
    return 0


def cmd_login_local(args: argparse.Namespace) -> int:
    """进入交互式 CLI（本地模式）."""
    project_root = get_project_root()

    print("🔗 连接 Mnemosync...")
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "cli-internal"],
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": project_root}
    )
    return result.returncode


# ============================================================================
# Docker 模式命令
# ============================================================================

def cmd_init_docker(args: argparse.Namespace) -> int:
    """初始化（Docker 模式）."""
    print("🔧 Docker 模式初始化...")

    if not is_docker_installed():
        print("❌ Docker 未安装。请先安装 Docker: https://docs.docker.com/get-docker/")
        return 1

    # 构建
    print("📦 构建 Docker 镜像...")
    result = run_docker_command(["build"])
    if result.returncode != 0:
        print("❌ 构建失败")
        return 1

    # 初始化数据库
    print("🔧 初始化数据库...")
    result = subprocess.run([
        "docker", "compose", "run", "--rm", "--entrypoint", "",
        "mnemosync", "uv", "run", "python", "-m", "src.main", "init-internal"
    ])
    if result.returncode != 0:
        print("❌ 初始化失败")
        return 1

    print("\n✅ 初始化完成")
    print("\n下一步: 运行 'mnemosync login' 进入 CLI")
    print()
    return 0


def cmd_login_docker(args: argparse.Namespace) -> int:
    """登录 CLI（Docker 模式）."""
    if not is_docker_installed():
        print("❌ Docker 未安装")
        return 1

    # 确保容器运行
    if not is_container_running():
        print("🚀 启动 Mnemosync 服务...")
        result = run_docker_command(["up", "-d", "--quiet-pull"])
        if result.returncode != 0:
            print("❌ 启动失败")
            return 1

    # 进入交互式 CLI
    subprocess.run([
        "docker", "compose", "exec", "mnemosync",
        "uv", "run", "python", "-m", "src.main", "cli-internal"
    ])
    return 0


# ============================================================================
# 通用命令
# ============================================================================

def cmd_stop(args: argparse.Namespace) -> int:
    """停止服务."""
    if is_container_running():
        print("⏹  停止服务...")
        result = run_docker_command(["down"])
        if result.returncode == 0:
            print("✅ 服务已停止")
        else:
            print("❌ 停止失败")
            return 1
    else:
        print("ℹ️  服务未运行")
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    """显示帮助."""
    print("""
Mnemosync CLI

用法: mnemosync <command> [options]

服务管理:
  serve               启动服务（本地模式）
  serve --port 16125  指定端口
  serve --host 0.0.0.0  指定监听地址

初始化:
  init                初始化数据库（本地模式）
  init --docker       初始化数据库（Docker 模式）

交互式 CLI:
  login               进入交互式 CLI（本地模式）
  login --docker      进入交互式 CLI（Docker 模式）

其他:
  stop                停止服务（Docker 模式）
  help                显示此帮助信息

示例:
  mnemosync init      # 首次使用，初始化数据库
  mnemosync serve     # 启动服务
  mnemosync login     # 进入交互式 CLI 管理 API Key 等

配置:
  配置文件: config.local.toml（项目根目录）
  数据目录: data/
""")
    return 0


# ============================================================================
# 主入口
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    """主入口."""
    parser = argparse.ArgumentParser(
        prog="mnemosync",
        description="Mnemosync - 人格记忆管理系统",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="启动服务")
    serve_parser.add_argument("--host", default=None, help="监听地址 (默认: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=None, help="监听端口 (默认: 16125)")
    serve_parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    serve_parser.set_defaults(func=cmd_serve)

    # ── init ──
    init_parser = subparsers.add_parser("init", help="初始化数据库")
    init_parser.add_argument("--docker", action="store_true", help="使用 Docker 模式")
    init_parser.set_defaults(func=lambda args: cmd_init_docker(args) if args.docker else cmd_init_local(args))

    # ── login ──
    login_parser = subparsers.add_parser("login", help="进入交互式 CLI")
    login_parser.add_argument("--docker", action="store_true", help="使用 Docker 模式")
    login_parser.set_defaults(func=lambda args: cmd_login_docker(args) if args.docker else cmd_login_local(args))

    # ── stop ──
    stop_parser = subparsers.add_parser("stop", help="停止服务 (Docker 模式)")
    stop_parser.set_defaults(func=cmd_stop)

    # ── help ──
    help_parser = subparsers.add_parser("help", help="显示帮助")
    help_parser.set_defaults(func=cmd_help)

    args = parser.parse_args(argv)

    if not args.command:
        cmd_help(args)
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
