# 故障排查指南

> **系统版本**: v0.3.4
> **最后更新**: 2026-08-01

常见问题、症状与解决方案索引。

---

## 1. 向量库 / Embedding

### 1.1 `向量库锁定为 X/Y (dim=N), 当前请求 A/B (dim=M)`

**症状**: 记忆入库失败，日志 / 通知中心出现 `VectorStoreLockError`，提示嵌入模型元数据不匹配。

**原因**:
- 首次写入记忆时，Mnemosync 将当前嵌入角色的 `(service_id, model, dim)` 锁入 ChromaDB collection metadata。
- 换了嵌入模型但未走 reindex 流程，新向量维度与旧向量不同，检索结果无意义。

**解决**:
1. 在管理后台 **模型管理 → Embedding 角色绑定** 确认当前绑定。
2. 到 **记忆管理 → 维护** 点击 **Reindex 全部记忆**（重建向量库，SQLite 元数据保留）。
3. reindex 完成前写入会被拒绝（日志 warning，不影响对话）。

### 1.2 Embedding 调用失败 / 上游超时

**症状**: 日志出现 `生成 embedding 失败, 记忆未入库`，通知中心 warning。

**可能原因**:
- 上游 API Key 过期或额度耗尽
- 网络不通（检查代理 / DNS）
- 模型名拼写错误

**排查**:
1. 管理后台 → 上游 API → 点开对应服务 → 点 **可用模型** 看是否能拉到列表。
2. 用 `curl` 直接打上游 `/v1/embeddings` 验证。
3. 检查 `config.local.toml` 里的 `[llm_services]` 配置。

---

## 2. 数据库 / 迁移

### 2.1 `database is locked` (SQLite)

**症状**: 偶发 `aiosqlite.OperationalError: database is locked`。

**原因**:
- SQLite WAL 模式下，高并发写入 + 长事务会导致锁等待超时（默认 5s）。
- 常见于同时进行批量 prune / reindex + 正常聊天流量。

**解决**:
- Mnemosync 将不同域拆到独立 DB 文件（`memory.db`、`conversation.db`、`http_logs.db` 等）来隔离 WAL 争用，若仍出现：
  - 减少后台批量任务的并发度
  - 避免在流量高峰手动触发 reindex
- 检查 `data/*.db` 文件权限：进程用户必须对 `data/` 目录有读写权限。

### 2.2 迁移失败 / Schema 版本异常

**症状**: 启动日志报 `duplicate column name` 或表结构相关错误。

**原因**: 早期版本（v0.2.x）用裸 `PRAGMA table_info` + `ALTER TABLE` 兜底迁移，若中途中断可能留下半迁移状态。

**解决**:
1. 停止服务。
2. 备份 `data/*.db`。
3. 删除出问题的 `.db` 文件（`memory.db` / `conversation.db` 等）—— Mnemosync 启动时会自动重建空库。
4. 若有需要保留的数据，用 `sqlite3` 手动导出。

v0.3.x 起改用 `MigrationRunner` + `_migrations` 跟踪表，幂等迁移不应再卡在此类问题。

---

## 3. API Key / 认证

### 3.1 `Token 无效` / `Token 已过期`

**症状**: 面板登录后操作返回 401，或 CLI 报认证失败。

**原因**:
- Session token 默认 24 小时过期
- 服务重启若没有 `MNEMOSYNC_SESSION_KEY` 且丢失 `data/.session_key`，旧 token 无法验证

**解决**:
- 重新登录（面板会自动跳转到登录页）。
- 生产部署建议设置 `MNEMOSYNC_SESSION_KEY` 环境变量（hex 或 base64 编码的 32 字节密钥），重启不丢会话。

### 3.2 `用户名或密码错误`（刚 setup 完）

**症状**: 首次初始化后无法登录。

**原因**: 密码策略：最少 6 字符，不能是默认密码 `mnemosync`（除非 setup 时明确指定）。

**解决**: 重新 setup 或用 CLI `mnemosync auth reset-password` 重置。

---

## 4. Docker / 部署

### 4.1 Docker 容器 `unhealthy`

**症状**: `docker compose ps` 显示容器状态为 `unhealthy`。

**排查**:
1. `docker compose logs mnemosync` 看启动日志。
2. 健康检查访问 `http://localhost:16125/health`，应返回 `{"status":"ok"}`。
3. 常见原因：
   - 端口 16125 被宿主机占用
   - `config.local.toml` 未挂载，用的是默认空配置
   - `data/` 目录权限不允许容器内用户写入
4. 进入容器：`docker compose exec mnemosync sh`，手动 `curl http://localhost:16125/health`。

### 4.2 面板打不开 / 静态资源 404

**症状**: 浏览器访问面板白屏 / 控制台 404。

**原因**: Docker 镜像构建时前端未打包，或 `ui/dist` 未被 COPY。

**解决**:
- 确保使用仓库根的 `Dockerfile`（多阶段构建会自动 `npm run build`）。
- 若自己构建：`cd ui && npm ci && npm run build`，然后启动后端。

---

## 5. SSE / 流式

### 5.1 调试面板 SSE 订阅断连 / 不重连

**症状**: 调试页"SSE 已订阅"标签消失，提示"SSE 断开"。

**排查**:
1. 打开浏览器 DevTools → Network，看 `events/stream` 请求状态。
2. 401 = session key 过期，刷新页面重新获取。
3. 持续断连 = 反向代理（nginx / caddy）buffer 了 SSE 流：
   - nginx: 加 `proxy_buffering off;` 和 `chunked_transfer_encoding on;`
   - 读超时设长一些（`proxy_read_timeout 3600s`）。

### 5.2 流式响应卡住但非流式正常

**症状**: stream=true 时响应一直 pending，但 stream=false 正常返回。

**原因**: 几乎总是反向代理 buffer 问题。见 5.1。

---

## 6. 聊天 / 记忆

### 6.1 人格回复不像自己 / 不读取记忆

**症状**: 助手回答空泛、不认识用户、不使用之前聊过的信息。

**排查**:
1. 打开 Debug Chat 页，打开 **调试模式**，看 inbound / outbound 事件流。
2. 检查 `memory_context` 节点是否加载了永久记忆：事件中应包含 `permanent_memories`。
3. 检查向量库是否为空（记忆管理页）。
4. 检查 Embedding 绑定是否配置（模型管理 → Embedding 角色）。

### 6.2 群聊中身份串了 / 回复对象错

**症状**: 群里 A 说话，回答像是对 B 说的。

**排查**:
1. 管理后台 → 身份管理 → 检查 Actors / Groups 绑定是否正确。
2. 确认 API Key 对应的策略中 identity strategy 配置正确。
3. 打开 Debug Chat 查看 `identity_resolution` 节点的入参和出参。

### 6.3 消息重复记录 / 工具调用两次

**症状**: 短期记忆中出现重复事件，或工具被执行两次。

**原因**: 平台（AstrBot/MaiBot 等）在网络抖动时重发同一条消息。

**解决**: v0.3.0+ 已实现幂等缓存（`idempotency.db`），首次响应缓存后重放。若仍重复：
- 确认 API Key 对应的集成开启了幂等（`idempotency_strategy` 配置为从消息内容提取 event ID）。
- 检查 `external_event_id` 是否被正确提取（Debug 面板看 inbound 事件 body）。

---

## 7. CLI

### 7.1 `mnemosync` 命令找不到

**解决**:
- 激活 venv：`source .venv/bin/activate`
- 或用 uv 运行：`uv run mnemosync --help`

### 7.2 CLI 连不上服务

**症状**: CLI 操作报 connection refused。

**原因**: CLI 默认连 `http://localhost:16125`，若服务跑在其他地址需要 `--host` 参数。

---

## 日志位置

- **应用日志**: stdout/stderr（Docker 下 `docker compose logs -f mnemosync`）
- **HTTP 请求日志**: `data/http_logs.db`（面板 → 请求日志页查看）
- **通知中心**: 面板右上角铃铛图标（记忆写入失败、reindex 进度等）
- **调试事件流**: Debug Chat 页下半部分（实时展示每一跳 HTTP）
