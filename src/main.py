"""程序入口."""

import os
import sys


def serve() -> None:
    """运行 FastAPI 服务器."""
    import uvicorn
    from fastapi import FastAPI
    from .api import api_router

    app = FastAPI(
        title="Mnemosync API",
        description="智能代理中间件 - LLM 上下文编排与社交记忆管理",
        version="0.1.0",
    )
    app.include_router(api_router)

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
            # 容器内运行交互式 CLI
            from .cli_interactive import main as cli_main
            import asyncio
            asyncio.run(cli_main())
            return 0
        elif cmd == "init-internal":
            # 容器内初始化
            from .storage import SqliteAuthService, SqliteApiKeyStore, ApiKey
            
            async def _init():
                os.makedirs("data", exist_ok=True)
                
                auth_db = SqliteAuthService(os.getenv("AUTH_DB_PATH", "data/auth.db"))
                api_key_db = SqliteApiKeyStore(os.getenv("MNEMOSYNC_DB_PATH", "data/api_keys.db"))
                
                await auth_db.init_db()
                await api_key_db.init_db()
                
                # 创建默认用户
                try:
                    await auth_db.create_default_user("mnemosync")
                except Exception:
                    pass  # 用户已存在
                
                print("Success!")
            
            import asyncio
            asyncio.run(_init())
            return 0
        else:
            print(f"Unknown command: {cmd}")
            return 1
    
    # 默认启动服务
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
