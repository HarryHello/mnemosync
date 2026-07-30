# Mnemosync Docker 镜像
# 使用方法:
#   docker compose up -d
#   docker compose exec mnemosync mnemosync init
#   docker compose exec mnemosync mnemosync login

# ============================================================================
# Stage 1: 构建管理面板 (Vue 3)
# ============================================================================
FROM node:22-slim AS ui-builder

WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY ui/ ./
RUN npm run build

# ============================================================================
# Stage 2: Python 运行时
# ============================================================================
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

# 从 ui-builder 阶段拷贝编译后的前端资源
COPY --from=ui-builder /ui/dist ./ui/dist

# 复制配置模板供参考 (运行时需通过 volume 挂载真实 config.local.toml)
COPY config.example.toml ./config.example.toml

# 创建数据目录
RUN mkdir -p /app/data

# 设置环境变量
ENV PYTHONPATH=/app
ENV PORT=16125
ENV HOST=0.0.0.0

# 暴露端口
EXPOSE 16125

# 入口点: 使用 mnemosync CLI
ENTRYPOINT ["uv", "run", "mnemosync"]
CMD ["serve"]
