"""程序入口 - 兼容旧的调用方式.

推荐使用 mnemosync CLI 命令:
  mnemosync serve
  mnemosync init
  mnemosync login
"""

import os
import sys


def serve() -> None:
    """运行 FastAPI 服务器."""
    import uvicorn
    from fastapi import FastAPI

    from .api import api_router, forward_router

    app = FastAPI(
        title="Mnemosync API",
        description="智能代理中间件 - LLM 上下文编排与人格记忆管理",
        version="0.3.0",
    )

    app.include_router(api_router)
    app.include_router(forward_router)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "16125"))
    uvicorn.run(app, host=host, port=port)


def main() -> int:
    """主入口 - 处理内部命令."""
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "serve":
            serve()
            return 0
        elif cmd == "cli-internal":
            import asyncio
            from src.cli.cli_interactive import main as cli_main
            asyncio.run(cli_main())
            return 0
        elif cmd == "init-internal":
            from .persistence.api_key_store import SqliteApiKeyStore
            from .persistence.auth_store import SqliteAuthStore

            async def _init():
                os.makedirs("data", exist_ok=True)

                auth_db = SqliteAuthStore(os.getenv("AUTH_DB_PATH", "data/auth.db"))
                api_key_db = SqliteApiKeyStore(os.getenv("MNEMOSYNC_DB_PATH", "data/api_keys.db"))

                await auth_db.init_db()
                await api_key_db.init_db()

                try:
                    await auth_db.create_default_user("mnemosync")
                except Exception:
                    pass

                print("Success!")

            import asyncio
            asyncio.run(_init())
            return 0
        else:
            print(f"Unknown command: {cmd}")
            return 1

    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
