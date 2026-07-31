# 部署指南 | Deployment

> **系统版本**: v0.3.4
> **文档状态**: 与代码同步
> **创建时间**: 2026-03-25
> **最后更新**: 2026-08-01
> **作者**: HarryHelloo

---

## 1. 前置准备

### 1.1 编辑配置

Mnemosync 只读 `config.local.toml`, 缺失即启动失败。首次部署:

```bash
cp config.example.toml config.local.toml
# 编辑 [persona] / [runtime] / [storage] 等段; 模型绑定 (main/assist/embedding/rerank)
# 由 role_bindings 表管理, 通过 CLI (`mnemosync login` → `ad-service` + `model add`) 或
# 面板 `/panel/admin/model-bindings` 配置, 不再写在 TOML 里 (v0.2.3 起)
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
│   ├── api/                # FastAPI 路由 + 中间件 + reasoning_control + admin_debug
│   ├── cli/                # 顶层命令 + 交互式 shell + ask + identity (v0.3.0)
│   ├── core/               # config / graph / memory / agents / models / prompts / identity (v0.3.0)
│   ├── infra/              # forwarder + MultiForwarder / llm_service / vector_store / debug_bus
│   ├── persistence/        # SQLite stores (memory / auth / api_key / conversation / http_log / identity / idempotency)
│   ├── tools/              # make_*_tool 工厂
│   └── main.py
├── ui/                     # 管理面前端
├── data/                   # 运行时数据 (SQLite + ChromaDB), 需持久化
├── scripts/
└── docs/
```

`data/` 是**唯一需要持久化**的目录, 包含:
- `memory.db` — 长期记忆元数据 (SQLite)
- `auth.db` — 管理员账号 + 会话 Token
- `api_keys.db` — 前端 API Key (含 v0.2.5 `source` 列、v0.3.0 `strategy_id` 列)
- `llm_service.db` — 服务商 + `role_bindings` (v0.2.3)
- `conversation.db` — v0.2.6 跨前端短期记忆 (`conversation_turns` 流水; v0.3.0 含空间事件流列)
- `http_logs.db` — v0.2.5 调试面板 HTTP 日志
- `notifications.db` — v0.2.13 通知中心
- `identity.db` — **v0.3.0** 身份四表 (actors / user_groups / actor_group_memberships / identity_strategies)
- `idempotency.db` — **v0.3.0** 幂等重放缓存 (平台重发消息时原样返回首次响应)
- `persona.db` — **v0.3.3** 结构化人格版本存储 (personas + persona_versions)
- `lorebook.db` — **v0.3.3** Lorebook 关键词知识条目
- `space_policy.db` — **v0.3.3** 空间社交策略
- `chroma/` — ChromaDB 向量库 (含 v0.2.4 embedding lock metadata)
- `prompts/` — v0.2.1 用户提示词覆盖层 (可选; 无覆盖时读默认层)
- `prompts/.history/` — 提示词覆盖备份 (每个 name 保留最近 10 份)

---

## 2. Docker Compose (推荐)

### 2.1 启动

```bash
git clone https://github.com/HarryHello/mnemosync.git
cd mnemosync
cp config.example.toml config.local.toml   # 编辑填入凭证
docker compose up -d
```

`docker-compose.yml` 挂载 `./data` 与 `./config.local.toml` 到容器内。**管理面板由 Dockerfile 多阶段自动构建** — 第一阶段 `node:22-slim` 跑 `npm run build` 生成 `ui/dist`, 第二阶段拷进 Python 镜像, 无需宿主机装 Node.js。

### 2.2 初始化数据库

```bash
docker compose exec mnemosync uv run mnemosync init --docker
```

### 2.3 进入交互式 CLI

```bash
docker compose exec mnemosync uv run mnemosync login --docker
```

在 shell 内使用 `generate-key`, `ad-service`, `model add <role> <service_id> <model>` 等, 详见 [cli.md](modules/cli.md)。

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

git clone https://github.com/HarryHello/mnemosync.git
cd mnemosync
uv sync
cp config.example.toml config.local.toml   # 编辑
```

### 3.2 管理面板 (ui/dist)

后端在启动时挂载 `ui/dist/` 作为静态资源。若该目录缺失, API 仍可用, 但访问 `/` 会 404。生成方式有二选一:

**A. 从 GitHub Release 下载预编译产物** (推荐, 无需 Node.js)

```bash
LATEST=$(curl -fsSL https://api.github.com/repos/HarryHello/mnemosync/releases/latest | jq -r .tag_name)
curl -fsSL "https://github.com/HarryHello/mnemosync/releases/download/${LATEST}/ui-dist.tar.gz" \
  | tar -xz -C ui/
```

`ui-dist.tar.gz` 由 [.github/workflows/release.yml](../.github/workflows/release.yml) 在 tag 推送时由 GitHub Actions 自动构建上传。

**B. 本地构建** (开发者)

```bash
cd ui
npm install     # 首次或依赖变更后
npm run build   # 生成 ui/dist/
cd ..
```

需要 Node.js 22+。构建后 `ui/dist/index.html` 存在即视为就绪。

一键脚本 `curl -fsSL .../install.sh | sh` 会自动尝试 A → B → 跳过并警告。

### 3.3 初始化 + 启动

```bash
uv run mnemosync init
uv run mnemosync serve                    # 前台
uv run mnemosync serve --daemon           # 后台
uv run mnemosync serve --debug            # 打印所有上游 HTTP 请求/响应
uv run mnemosync serve --host 127.0.0.1 --port 16126 --log-level debug
```

### 3.4 systemd (可选生产)

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
mnemosync upgrade                # 默认 main 分支
mnemosync upgrade --branch dev   # 开发者可切 dev
```

手动:
```bash
git pull
uv sync
# 重新构建面板 (若从 Release 拿预编译产物, 见 §3.2)
cd ui && npm install && npm run build && cd ..
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
1. 是否配置了嵌入角色绑定? `mnemosync login` → `model add embedding <service_id> <model> --dim N` (v0.2.3+)
2. 切换嵌入模型: 走 `POST /panel/admin/memory/reindex` 或 CLI `memory reindex --prune` (v0.2.4); Chroma collection 锁 `(service_id, model, dim)`, 手动清空 `data/chroma/` 会失去元数据
3. 重排端点差异见 [forward.md](modules/forward.md) §4 (`/rerank` → 404 fallback `/reranks`)

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

## 11. 发布流程 (维护者)

Release 由 [.github/workflows/release.yml](../.github/workflows/release.yml) 自动化。整体流程:

```bash
# 1. 在 release 分支上做 dev→main 的差异修正 (install.sh BRANCH 默认、cli 描述等)
git checkout -b release/v0.2.X

# 2. 冒烟验证 (可选)
uv run pytest -q --no-cov
cd ui && npm install && npm run build && cd ..

# 3. 合并到 main
git checkout main
git merge --no-ff release/v0.2.X -m "release: v0.2.X"
git push origin main

# 4. 打 tag 并推送 — 这一步触发 GitHub Actions
git tag -a v0.2.X -m "Mnemosync v0.2.X"
git push origin v0.2.X

# 5. 观察 Actions
# https://github.com/HarryHello/mnemosync/actions/workflows/release.yml
# 成功后 https://github.com/HarryHello/mnemosync/releases 会出现新 Release
# 附件包含 ui-dist.tar.gz
```

Actions 的具体动作:

1. checkout tag 指向的 commit
2. setup Node.js 22 + npm 缓存
3. `cd ui && npm ci && npm run build`
4. 校验 `ui/dist/index.html` 存在
5. `tar -czf ui-dist.tar.gz -C ui dist`
6. `gh release create $TAG --generate-notes ui-dist.tar.gz`

**回滚**: 直接删除 tag 与 GitHub Release, 再重新推 tag; 用户 install.sh 会自动拉最新 release, 无需通知。

---

## 12. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-03-25 | 初始部署文档: Docker Compose + 源码 |
| v0.2.1 | 2026-07-15 | 与代码对齐: 移除虚构环境变量表 (`MNEMOSYNC_DB_PATH` 等), 补 `mnemosync upgrade` / `--debug` / `--daemon`, 补 SSE 反代要点, 移除未实现的 Redis 缓存/metrics 章节 |
| v0.2.6 | 2026-07-18 | 与代码对齐: 目录/data 列表补 `conversation.db` (v0.2.6 短期记忆) / `http_logs.db` (v0.2.5) / `prompts/` (v0.2.1); 模型绑定改由 `role_bindings` + CLI `set-model` (v0.2.3+), 不再写 `[chat]/[embedding]/[rerank]`; 换嵌入模型走 `memory reindex` (v0.2.4) 而非手动清 Chroma |
| v0.2.11 | 2026-07-19 | 与发布流程对齐: 补 §3.2 管理面板构建 (Release 预编译 tarball 优先 / 本地 npm build 兜底); §2 Docker 改由多阶段自动 build UI, 不再挂载 `./ui`; §8 `mnemosync upgrade` 默认分支改 `main`; 新增 §11 发布流程 (GitHub Actions 自动打 Release + 上传 ui-dist.tar.gz) |
| v0.2.12 | 2026-07-20 | 面板首次登录强制改账号密码: 新增 `POST /panel/auth/setup-credentials` 与 `require_password_settled` dependency (面板非 auth 路由 include 时统一注入); `must_change_password=True` 时所有 `/panel/*` (除 `/panel/auth/*`) 返回 `403 password_change_required`; 面板新增 `/setup` 页面 (BlankLayout, 无侧栏), 全局路由守卫强制跳转; `/settings` 简化为改密, 用户名只读展示; CLI `mnemosync login` 仍是唯一修改用户名的入口 |
| v0.3.0 | 2026-07-26 | data 列表补 `identity.db` / `idempotency.db` (多用户身份 + 幂等重放) 与 `notifications.db`; 备份清单同步; 目录树补 `src/core/identity/` 与 persistence 新库; CLI 模型命令名修正为 `model add` |
| v0.3.3 | 2026-08-01 | data 列表补 `persona.db` / `lorebook.db` / `space_policy.db` (结构化人格 / Lorebook / 空间策略); 目录树补 `src/core/persona/` / `src/core/tools/` / `src/infra/space_lock.py` / `src/infra/character_card.py`; plugins/ 目录说明 |
| v0.3.4 | 2026-07-30 | 人格系统重构: 多人格 profile 支持 (`personas` 表 + 切换 API); `PersonaIdentity` 移除 per-user 字段 (`user_addressing`/`context`, 由 `Relationship` 维护); 人格改名支持; 默认提示词结构化; 前端人格编辑器重构 |
