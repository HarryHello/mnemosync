# 部署指南

> **系统版本**: v0.1.0
> **创建时间**: 2026-03-25
> **作者**: HarryHelloo

---

## 部署方式

### 方式一：Docker Compose（推荐）

适合大多数用户，简单快捷。

#### 1. 安装 Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker

# 安装 Docker Compose
sudo apt-get install docker-compose-plugin
```

#### 2. 克隆项目

```bash
git clone https://github.com/Mnemosync/Mnemosync.git
cd Mnemosync
```

#### 3. 启动服务

```bash
docker compose up -d
```

#### 4. 初始化

```bash
docker compose exec mnemosync uv run mnemosync init
```

#### 5. 查看日志

```bash
docker compose logs -f
```

#### 6. 数据持久化

数据存储在 `./data` 目录，确保该目录有写入权限：

```bash
chmod 755 ./data
```

---

### 方式二：Docker 直接运行

```bash
# 构建镜像
docker build -t mnemosync:latest .

# 运行容器
docker run -d \
  --name mnemosync \
  -p 16125:16125 \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  mnemosync:latest

# 初始化
docker exec mnemosync uv run mnemosync init
```

---

### 方式三：源码部署

适合开发者和需要自定义配置的用户。

#### 1. 安装 Python 3.12+

```bash
# Ubuntu/Debian
sudo apt-get install python3.12 python3.12-venv python3.12-dev

# 或使用 pyenv
curl https://pyenv.run | bash
pyenv install 3.12.0
pyenv global 3.12.0
```

#### 2. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### 3. 克隆并安装依赖

```bash
git clone https://github.com/Mnemosync/Mnemosync.git
cd Mnemosync
uv sync
```

#### 4. 初始化并启动

```bash
uv run mnemosync init
uv run mnemosync serve
```

#### 5. 后台运行（可选）

```bash
# 使用 nohup
nohup uv run mnemosync serve > mnemosync.log 2>&1 &

# 或使用 systemd（推荐）
sudo tee /etc/systemd/system/mnemosync.service > /dev/null <<EOF
[Unit]
Description=Mnemosync Service
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/Mnemosync
ExecStart=/home/your-user/.local/bin/uv run mnemosync serve
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mnemosync
sudo systemctl start mnemosync
```

---

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `16125` | 服务端口 |
| `MNEMOSYNC_DB_PATH` | `data/api_keys.db` | API Key 数据库路径 |
| `AUTH_DB_PATH` | `data/auth.db` | 认证数据库路径 |
| `LOG_LEVEL` | `info` | 日志级别 |

### 配置文件

创建 `.env` 文件（可选）：

```bash
# .env
HOST=0.0.0.0
PORT=16125
LOG_LEVEL=info
```

---

## 防火墙配置

### Ubuntu (UFW)

```bash
sudo ufw allow 16125/tcp
sudo ufw reload
```

### CentOS (firewalld)

```bash
sudo firewall-cmd --permanent --add-port=16125/tcp
sudo firewall-cmd --reload
```

### 云服务器

在安全组中添加入站规则：
- 端口：`16125`
- 协议：`TCP`
- 源 IP：`0.0.0.0/0`（或指定 IP）

---

## HTTPS 配置（推荐生产环境）

### 使用 Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:16125;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}
```

### 使用 Caddy（自动 HTTPS）

```caddyfile
your-domain.com {
    reverse_proxy localhost:16125
}
```

---

## 备份与恢复

### 备份数据

```bash
# 备份数据库
tar -czf mnemosync-backup-$(date +%Y%m%d).tar.gz data/

# 或使用 docker
docker compose exec mnemosync tar -czf /tmp/backup.tar.gz /app/data
docker compose cp mnemosync:/tmp/backup.tar.gz ./backup.tar.gz
```

### 恢复数据

```bash
# 解压备份
tar -xzf mnemosync-backup-*.tar.gz

# 重启服务
docker compose restart
```

---

## 故障排查

### 查看日志

```bash
# Docker
docker compose logs -f

# 源码部署
journalctl -u mnemosync -f
```

### 常见错误

#### 端口被占用

```bash
# 检查端口占用
sudo lsof -i :16125

# 修改端口
export PORT=16126
uv run mnemosync serve
```

#### 数据库权限问题

```bash
# 修复权限
sudo chown -R $(whoami):$(whoami) data/
chmod 755 data/
```

#### 内存不足

```bash
# 查看内存
free -h

# 限制内存（systemd）
# 在 service 文件中添加：
# MemoryLimit=512M
```

---

## 性能优化

### 调整连接池

编辑 `src/infra/forwarder/connection_pool.py`：

```python
MAX_CONNECTIONS = 100  # 根据服务器配置调整
```

### 使用 Redis 缓存（可选）

```bash
# 安装 Redis
sudo apt-get install redis-server

# 配置
export REDIS_URL=redis://localhost:6379
```

---

## 监控与告警

### Prometheus + Grafana

（待实现）导出 `/metrics` 端点。

### 健康检查

```bash
curl http://localhost:16125/health
```

---

## 升级指南

### Docker 升级

```bash
# 拉取最新代码
git pull

# 重新构建
docker compose build

# 重启服务
docker compose up -d

# 数据迁移（如有）
docker compose exec mnemosync uv run mnemosync migrate
```

### 源码升级

```bash
git pull
uv sync
sudo systemctl restart mnemosync
```

---

## 卸载

### Docker

```bash
docker compose down -v  # 删除数据和容器
docker compose down     # 保留数据
```

### 源码

```bash
sudo systemctl stop mnemosync
sudo systemctl disable mnemosync
sudo rm /etc/systemd/system/mnemosync.service
sudo systemctl daemon-reload
rm -rf Mnemosync/
```

---

## 技术支持

- 📖 [架构文档](./architecture.md)
- 📝 [配置指南](./configuration.md)
- 🐛 [Issue 追踪](https://github.com/Mnemosync/Mnemosync/issues)
- 💬 [讨论区](https://github.com/Mnemosync/Mnemosync/discussions)
