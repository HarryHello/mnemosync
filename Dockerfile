# 使用官方 Python 镜像
# 如果网络问题，请先配置 Docker 镜像加速器
FROM python:3.12-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制项目文件（包括 README）
COPY pyproject.toml uv.lock README.md ./

# 安装依赖
RUN uv sync --frozen --no-dev

# 复制源代码
COPY src/ ./src/

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

# 默认命令 - 使用 src.main 作为入口以支持 serve 命令
ENTRYPOINT ["uv", "run", "python", "-m", "src.main"]
CMD []
