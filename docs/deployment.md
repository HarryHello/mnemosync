# 部署指南 | Deployment

> **系统版本**: v0.2.1
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-25
> **最后更新**: 2026-07-15
> **作者**: HarryHelloo

---

## 1. 前置准备

### 1.1 编辑配置

Mnemosync 只读 `config.local.toml`, 缺失即启动失败。首次部署:

```bash
cp config.example.toml config.local.toml
# 编辑 [chat] / [embedding] / [rerank] 段, 填入真实服务商凭证
```

配置字段清单见 [configuration.md](configuration.md)。

### 1.2 目录结构

```
Mnemosync/
├── config.local.toml       # 唯一配置源
├── config.example.toml
├── docker-compose.yml
├── Dockerfile
├── install.sh
├── pyproject.toml / uv.lock
├── src/
│   ├── api/                # FastAPI 路由 + 中间件
│   ├── cli/                # 顶层命令 + 交互式 shell + ask
│   ├── core/               # config / graph / memory / agents
│   ├── infra/              # forwarder / llm_service / vector_store
│   ├── persistence/        # SQLite stores (memory / auth / api_key)
│   ├── tools/              # make_*_tool 工厂
│   └── main.py
├── ui/                     # 管理面前端
├── data/                   # 运行时数据 (SQLite + ChromaDB), 需持久化
├── scripts/
└── docs/
```

`data/` 是**唯一需要持久化**的目录, 包含 `memory.db`, `auth.db`, `api_keys.db`, `llm_service.db`, `chroma/`。

---

## 2. Docker Compose (推荐)

### 2.1 启动

```bash
git clone https://github.com/Mnemosync/Mnemosync.git
cd Mnemosync
cp config.example.toml config.local.toml   # 编辑填入凭证
docker compose up -d
```

`docker-compose.yml` 已挂载 `./data`, `./config.local.toml`, `./ui` 到容器内。

### 2.2 初始化数据库

```bash
docker compose exec mnemosync uv run mnemosync init --docker
```

### 2.3 进入交互式 CLI

```bash
docker compose exec mnemosync uv run mnemosync login --docker
```

在 shell 内使用 `generate-key`, `ad-service`, `set-main-model` 等, 详见 [cli.md](modules/cli.md)。

### 2.4 停止

```bash
docker compose down          # 保留 data/
docker compose down -v       # 同时清除挂载卷
# 或在 CLI 内
mnemosync stop
```

---

## 3. 源码部署 (开发/测试)

### 3.1 安装

```bash
# Python 3.12+
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/Mnemosync/Mnemosync.git
cd Mnemosync
uv sync
cp config.example.toml config.local.toml   # 编辑
```

### 3.2 初始化 + 启动

```bash
uv run mnemosync init
uv run mnemosync serve                    # 前台
uv run mnemosync serve --daemon           # 后台
uv run mnemosync serve --debug            # 打印所有上游 HTTP 请求/响应
uv run mnemosync serve --host 127.0.0.1 --port 16126 --log-level debug
```

### 3.3 systemd (可选生产)

```ini
# /etc/systemd/system/mnemosync.service
[Unit]
Description=Mnemosync Service
After=network.target

[Service]
Type=simple
User=mnemosync
WorkingDirectory=/opt/Mnemosync
Environment=MNEMOSYNC_DEBUG=0
ExecStart=/home/mnemosync/.local/bin/uv run mnemosync serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mnemosync
```

---

## 4. 运行时开关

Mnemosync 主要配置只来自 `config.local.toml`。运行时可用的开关只有:

| 入口 | 覆盖 |
|------|------|
| `mnemosync serve --host / --port / --log-level` | `[runtime]` 对应字段 |
| `MNEMOSYNC_DEBUG=1` 环境变量 | 打开 Forwarder 请求/响应日志 (等价 `--debug`) |
| `mnemosync serve --daemon` | 后台运行 |

**不存在**的历史文档遗留: `MNEMOSYNC_DB_PATH` / `AUTH_DB_PATH` / `LOG_LEVEL` / `.env`——数据库路径由 `[storage]` 段控制 (API Key DB 除外, 硬编码 `data/api_keys.db`), 详见 [configuration.md](configuration.md) §4。

---

## 5. 反向代理 / HTTPS

生产必走 HTTPS。示例 Nginx:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # SSE 流式端点需要禁用响应缓冲
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:16125;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}
```

Caddy:

```caddyfile
your-domain.com {
    reverse_proxy 127.0.0.1:16125 {
        flush_interval -1   # SSE 立即刷缓冲
    }
}
```

---

## 6. 健康检查

```bash
curl http://127.0.0.1:16125/health
```

返回 `{"status": "ok", ...}` (见 [src/api/routes/admin.py:85](../src/api/routes/admin.py#L85))。可挂到容器/编排的 liveness / readiness。

---

## 7. 备份

```bash
# 打包 data/ 即可
tar -czf mnemosync-$(date +%Y%m%d).tar.gz data/ config.local.toml
```

Docker:
```bash
docker compose exec mnemosync tar -czf /tmp/backup.tar.gz /app/data /app/config.local.toml
docker cp $(docker compose ps -q mnemosync):/tmp/backup.tar.gz ./
```

**注意**: `config.local.toml` 包含明文 API Key, 备份时按敏感文件处理。

---

## 8. 升级

顶层 CLI 提供便捷升级 (拉分支 + 重装依赖):

```bash
mnemosync upgrade                # 默认 dev 分支
mnemosync upgrade --branch main
```

手动:
```bash
git pull
uv sync
# Docker
docker compose build && docker compose up -d
# systemd
sudo systemctl restart mnemosync
```

---

## 9. 故障排查

**端口占用**:
```bash
sudo lsof -i :16125
mnemosync serve --port 16126
```

**权限问题**:
```bash
chown -R $USER:$USER data/
chmod 755 data/
```

**上游 API 排查**: 用 `mnemosync serve --debug` 或 `mnemosync ask --debug "..."` 打印所有请求/响应 JSON。

**记忆检索为空**: 
1. 是否配置 `[embedding]`?
2. 切换嵌入模型后是否清空 `data/chroma/` 并重建? 详见 [configuration.md](configuration.md) §7
3. `[rerank]` 端点是否 `/compatible-api/v1` (DashScope)?

---

## 10. 卸载

Docker:
```bash
docker compose down -v
```

源码:
```bash
sudo systemctl disable --now mnemosync
sudo rm /etc/systemd/system/mnemosync.service
sudo systemctl daemon-reload
rm -rf /opt/Mnemosync
```

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-25 | 初始部署文档: Docker Compose + 源码 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 移除虚构环境变量表 (`MNEMOSYNC_DB_PATH` 等), 补 `mnemosync upgrade` / `--debug` / `--daemon`, 补 SSE 反代要点, 移除未实现的 Redis 缓存/metrics 章节 |
