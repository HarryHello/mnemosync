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

    # 设置调试模式环境变量
    if args.debug:
        os.environ["MNEMOSYNC_DEBUG"] = "1"
        print("🔍 Debug mode enabled - logging all HTTP requests/responses")

    # 导入并运行服务器
    try:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
        from starlette.responses import FileResponse

        from src.api import api_router, forward_router
        from src.api.lifespan import app_lifespan
        from src.api.middleware import HttpLogMiddleware
    except ImportError as e:
        print(f"❌ 依赖未安装: {e}")
        print("请运行: uv sync")
        return 1

    app = FastAPI(
        title="Mnemosync API",
        description="智能代理中间件 - LLM 上下文编排与人格记忆管理",
        version="0.3.0",
        lifespan=app_lifespan,
    )

    # 添加 CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 添加 HTTP 日志中间件
    app.add_middleware(HttpLogMiddleware, debug=args.debug)

    # API 路由
    app.include_router(api_router)
    app.include_router(forward_router)

    # 前端静态文件
    ui_dist = os.path.join(project_root, "ui", "dist")
    if os.path.exists(ui_dist):
        app.mount("/assets", StaticFiles(directory=os.path.join(ui_dist, "assets")), name="assets")

        from fastapi import HTTPException as _HTTPException

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """SPA 路由: 未注册的非 API 路径返回 index.html.

            /panel/* 与 /v1/* 属于 API 命名空间, 若未命中已注册路由必须直接 404,
            否则前端 fetch 拿到 HTML, response.json() 会抛 "Unexpected token '<'".
            """
            if full_path.startswith(("panel/", "v1/")) or full_path in ("panel", "v1"):
                raise _HTTPException(status_code=404, detail="Not Found")
            file_path = os.path.join(ui_dist, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(ui_dist, "index.html"))
    else:
        print("⚠️  前端未构建, 仅提供 API 服务")

    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "16125"))
    pid_file = os.path.join(project_root, "data", "mnemosync.pid")

    if args.daemon:
        # 后台模式
        pid = os.fork()
        if pid > 0:
            # 父进程
            print(f"🚀 Mnemosync 已启动 (PID: {pid})")
            print(f"   日志: {os.path.join(project_root, 'data', 'mnemosync.log')}")
            print("   停止: mnemosync stop")
            return 0

        # 子进程
        os.setsid()

        # 写入 PID 文件
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        # 重定向标准输出到日志文件
        log_file = os.path.join(project_root, "data", "mnemosync.log")
        sys.stdout = open(log_file, "a")
        sys.stderr = sys.stdout

        print(f"🚀 Mnemosync 后台启动中... http://{host}:{port}")

        uvicorn.run(app, host=host, port=port, log_level=args.log_level)
    else:
        # 前台模式
        # 保存 PID 以便 stop 命令使用
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        print(f"🚀 Mnemosync 启动中... http://{host}:{port}")
        try:
            uvicorn.run(app, host=host, port=port, log_level=args.log_level)
        finally:
            # 清理 PID 文件
            if os.path.exists(pid_file):
                os.remove(pid_file)

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
    project_root = get_project_root()
    pid_file = os.path.join(project_root, "data", "mnemosync.pid")

    # 检查本地进程
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())

            # 检查进程是否存在
            os.kill(pid, 0)
            # 进程存在，发送终止信号
            print(f"⏹  停止服务 (PID: {pid})...")
            os.kill(pid, 15)  # SIGTERM

            # 等待进程退出
            import time
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break

            # 检查是否还在运行
            try:
                os.kill(pid, 0)
                print("⚠️  进程未响应，强制终止...")
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass

            # 清理 PID 文件
            os.remove(pid_file)
            print("✅ 服务已停止")
            return 0
        except (ValueError, OSError):
            # PID 文件无效或进程不存在
            os.remove(pid_file)

    # 检查 Docker 容器
    if is_container_running():
        print("⏹  停止 Docker 服务...")
        result = run_docker_command(["down"])
        if result.returncode == 0:
            print("✅ 服务已停止")
        else:
            print("❌ 停止失败")
            return 1
    else:
        print("ℹ️  服务未运行")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    """升级 Mnemosync."""
    project_root = get_project_root()
    branch = args.branch or os.getenv("MNEMOSYNC_BRANCH", "main")

    print(f"🔄 Upgrading Mnemosync (branch: {branch})...")
    print()

    # 检查是否是 git 仓库
    if not os.path.exists(os.path.join(project_root, ".git")):
        print("❌ Not a git repository. Please reinstall using install.sh")
        return 1

    # 拉取最新代码
    print("📥 Pulling latest code...")
    result = subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=project_root,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"❌ Failed to fetch: {result.stderr}")
        return 1

    result = subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=project_root,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"❌ Failed to reset: {result.stderr}")
        return 1

    print("✅ Code updated")

    # 更新依赖
    print("📦 Updating dependencies...")
    result = subprocess.run(
        ["uv", "sync"],
        cwd=project_root,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"❌ Failed to update dependencies: {result.stderr}")
        return 1

    print("✅ Dependencies updated")

    # 重新注册命令
    bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    venv_bin = os.path.join(project_root, ".venv", "bin", "mnemosync")
    link_path = os.path.join(bin_dir, "mnemosync")

    if os.path.exists(venv_bin):
        if os.path.exists(link_path) or os.path.islink(link_path):
            os.remove(link_path)
        os.symlink(venv_bin, link_path)
        print("✅ Command registered")

    print()
    print("✅ Upgrade complete!")
    print()
    print("If service is running, restart it:")
    print("  mnemosync stop && mnemosync serve")
    print()
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    """显示帮助."""
    print("""
Mnemosync CLI

用法: mnemosync <command> [options]

服务管理:
  serve               前台启动服务
  serve -d            后台启动服务
  serve --port 16125  指定端口
  serve --host 0.0.0.0  指定监听地址
  stop                停止服务

初始化:
  init                初始化数据库（本地模式）
  init --docker       初始化数据库（Docker 模式）

交互式 CLI:
  login               进入交互式 CLI（本地模式）
  login --docker      进入交互式 CLI（Docker 模式）

调试:
  ask "你好"                 in-process 跑一次主对话
  ask --stream "..."         流式模式 (与生产 SSE 路径一致)
  ask --debug "..."          打印上游 HTTP 请求/响应 (等价 serve --debug)
  ask --user harry ...       指定 source_user
  ask --persona-file p.txt ... 用文件里的人格 prompt
  ask --via-http --api-key sk-... "..."  走本地 HTTP 服务

提示词覆盖:
  prompt list                          列出所有 Agent 提示词及覆盖状态
  prompt show <name>                   打印当前生效的提示词
  prompt show <name> --from-default    打印默认版本
  prompt set <name> --file p.md        从文件写入覆盖
  cat p.md | prompt set <name>         从管道写入
  prompt set <name> --edit             用 $EDITOR 编辑当前版本
  prompt set <name> --edit --from-default  从默认版本开始编辑
  prompt reset <name>                  删除覆盖 (自动备份)
  prompt reset --all                   全部回默认
  prompt validate <name> / --all       校验占位符齐全性

身份管理 (v0.3.0 多用户):
  identity strategy list               列出身份识别策略
  identity strategy create --name "AstrBot QQ" --type regex \\
      --frontend astrbot --actor-pattern 'QQ号[:：]\\s*(\\d+)' \\
      --name-pattern '用户名[:：]\\s*(\\S+)' --space-pattern '群号[:：]\\s*(\\d+)'
                                       创建策略 (便捷参数或 --config JSON)
  identity strategy show <id>          查看策略详情 (含 config)
  identity strategy update <id> [--name X] [--config JSON] [--active/--inactive]
  identity strategy delete <id>        删除策略
  identity actor list [--frontend X] [--search 关键词]
                                       列出参与者 (请求到达时自动创建)
  identity actor show <actor_id>       参与者详情 (组归属 + effective_user_id)
  identity group list                  列出用户组 (含成员数)
  identity group create --name 张三    创建用户组 (一个真实人)
  identity group show <group_id>       查看组成员
  identity bind <actor_id> <group_id>  跨平台身份归一 (共享记忆与关系)
  identity unbind <actor_id> <group_id>

升级:
  upgrade             拉取最新代码并更新依赖
  upgrade --branch dev  指定分支（默认 main）

其他:
  help                显示此帮助信息

示例:
  mnemosync init          # 首次使用，初始化数据库
  mnemosync serve         # 前台启动（开发用）
  mnemosync serve -d      # 后台启动（生产用）
  mnemosync stop          # 停止服务
  mnemosync login         # 进入交互式 CLI 管理 API Key 等
  mnemosync ask "你好"    # 直接跑一次主对话 (调试)
  mnemosync upgrade       # 更新到最新版本

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
    serve_parser.add_argument("--daemon", "-d", action="store_true", help="后台运行")
    serve_parser.add_argument("--debug", action="store_true", help="调试模式: 打印所有 HTTP 请求/响应")
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

    # ── ask ──
    ask_parser = subparsers.add_parser("ask", help="命令行直连主对话 (调试用)")
    ask_parser.add_argument("question", nargs="?", default=None, help="问题内容 (不填则从 stdin 读入)")
    ask_parser.add_argument("--user", required=True, help="source_user 标识 (必填)")
    ask_parser.add_argument("--persona-file", default=None, help="人格 prompt 文件路径")
    ask_parser.add_argument("--stream", action="store_true", help="流式输出 (与生产 SSE 路径一致)")
    ask_parser.add_argument("--debug", action="store_true", help="打印上游 HTTP 请求/响应 (等价 serve --debug)")
    ask_parser.add_argument("--verbose", "-v", action="store_true", help="打印调试日志")
    ask_parser.add_argument("--via-http", action="store_true", help="改走本地 HTTP 服务, 需先 mnemosync serve")
    ask_parser.add_argument("--api-key", default=None, help="--via-http 时使用, 默认读 MNEMOSYNC_API_KEY 环境变量")
    ask_parser.add_argument("--base-url", default="http://127.0.0.1:16125", help="--via-http 目标地址")
    from src.cli.ask import cmd_ask
    ask_parser.set_defaults(func=cmd_ask)

    # ── prompt ──
    from src.cli.prompt_cmd import build_parser as _build_prompt_parser
    _build_prompt_parser(subparsers)

    # ── identity ──
    from src.cli.identity_cmd import build_parser as _build_identity_parser
    _build_identity_parser(subparsers)

    # ── upgrade ──
    upgrade_parser = subparsers.add_parser("upgrade", help="升级 Mnemosync")
    upgrade_parser.add_argument("--branch", default=None, help="指定分支 (默认: main)")
    upgrade_parser.set_defaults(func=cmd_upgrade)

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
