# Mnemosync Docker 镜像
# 使用方法:
#   docker compose up -d
#   docker compose exec mnemosync mnemosync init
#   docker compose exec mnemosync mnemosync login

FROM python:3.12-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制项目文件
COPY pyproject.toml uv.lock README.md ./

# 安装依赖
RUN uv sync --frozen --no-dev

# 复制源代码
COPY src/ ./src/
COPY install.sh ./

# 复制配置模板
COPY config.example.toml config.local.toml

# 创建数据目录
RUN mkdir -p /app/data

# 设置环境变量
ENV PYTHONPATH=/app
ENV MNEMOSYNC_DB_PATH=/app/data/api_keys.db
ENV AUTH_DB_PATH=/app/data/auth.db
ENV PORT=16125
ENV HOST=0.0.0.0

# 暴露端口
EXPOSE 16125

# 入口点: 使用 mnemosync CLI
ENTRYPOINT ["uv", "run", "mnemosync"]
CMD ["serve"]
