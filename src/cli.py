"""Mnemosync CLI - 统一入口."""

import argparse
import subprocess
import sys
import os


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
    cmd = ["docker", "compose"] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        return subprocess.run(cmd)


def cmd_init(args: argparse.Namespace) -> int:
    """初始化."""
    print("Mnemosync initializing...\n")
    
    if not is_docker_installed():
        print("❌ Docker not found. Please install Docker first.\n")
        return 1
    
    # 构建
    print("Building Docker image...")
    result = run_docker_command(["build", "--quiet"])
    if result.returncode != 0:
        print("❌ Build failed.\n")
        return 1
    
    print("Success!\n")
    print("Use `mnemosync login` to start the cli environment,")
    print("or use `mnemosync help` to get more information.\n")
    
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """登录 CLI."""
    if not is_docker_installed():
        print("❌ Docker not found.\n")
        return 1
    
    # 确保服务已启动
    if not is_container_running():
        print("Starting Mnemosync service...")
        result = run_docker_command(["up", "-d", "--quiet-pull"])
        if result.returncode != 0:
            print("❌ Failed to start service.\n")
            return 1
    
    # 进入交互式 CLI
    print("Starting Mnemosync CLI...\n")
    subprocess.run([
        "docker", "compose", "exec", "-T", "mnemosync",
        "uv", "run", "mnemosync", "cli-internal"
    ])
    
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    """显示帮助."""
    print("""
Mnemosync CLI - Usage:

  mnemosync init          Initialize Mnemosync (build Docker image)
  mnemosync login         Login to Mnemosync CLI environment
  mnemosync help          Show this help message

Once logged in with `mnemosync login`, you can use:
  
  help                    Show available commands
  logout                  Exit CLI environment (service keeps running)
  stop                    Stop Mnemosync server
  ls-keys                 List API keys
  generate-key            Generate new API key
  show-key [id]           Show specific API key
  ls-service              List LLM service providers
  ad-service              Add new LLM service provider
  rm-service [id]         Remove LLM service provider
  ls-models [srv_id]      List available models
  set-main-model          Set main model
  set-assist-model        Set assist model
  test-model              Test model connection
""")
    return 0


def main(argv: list[str] | None = None) -> int:
    """主入口."""
    parser = argparse.ArgumentParser(
        prog="mnemosync",
        description="Mnemosync CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize Mnemosync")
    init_parser.set_defaults(func=cmd_init)

    # login
    login_parser = subparsers.add_parser("login", help="Login to CLI")
    login_parser.set_defaults(func=cmd_login)

    # help
    help_parser = subparsers.add_parser("help", help="Show help")
    help_parser.set_defaults(func=cmd_help)

    args = parser.parse_args(argv)

    if not args.command:
        print("Mnemosync CLI v0.1.0")
        print("\nUse 'mnemosync init' to initialize, then 'mnemosync login' to start.\n")
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
