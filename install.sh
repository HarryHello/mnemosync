#!/bin/bash
# Mnemosync 安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/HarryHello/mnemosync/dev/install.sh | sh
#
# 需要: git
# 可选: uv (脚本会自动安装), node + npm (若需要本地 build UI 而非从 Release 下载)
# 备注: Python 3.12+ 若系统未安装, uv 会在同步依赖时自动下载并管理, 无需手动准备.

set -e

# ============================================================================
# 配置
# ============================================================================
REPO_URL="https://github.com/HarryHello/mnemosync.git"
API_URL="https://api.github.com/repos/HarryHello/mnemosync"
INSTALL_DIR="${MNEMOSYNC_INSTALL_DIR:-$HOME/.mnemosync}"
BIN_DIR="${MNEMOSYNC_BIN_DIR:-$HOME/.local/bin}"
BRANCH="${MNEMOSYNC_BRANCH:-dev}"

# 颜色 (使用 printf 兼容 sh)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { printf "${GREEN}[INFO]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$1"; exit 1; }

# ============================================================================
# 检查依赖
# 只硬性要求 git; Python 由 uv 负责 (系统 python3 不满足 3.12+ 时 uv 会自动下载)
# ============================================================================
check_dependencies() {
    if ! command -v git > /dev/null 2>&1; then
        error "git 未安装。请先安装 git:\n  Ubuntu/Debian: sudo apt-get install git\n  macOS: xcode-select --install"
    fi

    if command -v python3 > /dev/null 2>&1; then
        PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
        PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
        PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

        if [ "$PYTHON_MAJOR" = "3" ] && [ "$PYTHON_MINOR" -ge 12 ] 2>/dev/null; then
            info "系统 Python $PYTHON_VERSION ✓ (可复用)"
        else
            info "系统 Python $PYTHON_VERSION 不满足 3.12+, uv 将自动下载所需版本"
        fi
    else
        info "未检测到系统 Python, uv 将自动下载所需版本"
    fi
}

# ============================================================================
# 安装 uv
# ============================================================================
install_uv() {
    if command -v uv > /dev/null 2>&1; then
        info "uv 已安装 ✓"
        return
    fi

    info "安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # 确保 uv 在 PATH 中
    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv > /dev/null 2>&1; then
        error "uv 安装失败"
    fi

    info "uv 已安装 ✓"
}

# ============================================================================
# 克隆/更新代码
# ============================================================================
setup_code() {
    if [ -d "$INSTALL_DIR" ]; then
        info "更新 Mnemosync..."
        cd "$INSTALL_DIR"

        # 确保是 git 仓库
        if [ ! -d ".git" ]; then
            warn "$INSTALL_DIR 存在但不是 git 仓库，重新克隆"
            cd ..
            rm -rf "$INSTALL_DIR"
            git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
            cd "$INSTALL_DIR"
        fi

        # 拉取最新代码
        git fetch origin "$BRANCH"
        git reset --hard "origin/$BRANCH"
    else
        info "下载 Mnemosync..."
        git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi

    info "代码就绪 ✓"
}

# ============================================================================
# 安装依赖 (uv sync 会按 pyproject.toml requires-python 自动准备 Python)
# ============================================================================
install_deps() {
    info "安装依赖 (uv 会按需下载 Python 3.12+)..."
    cd "$INSTALL_DIR"
    uv sync --frozen 2>/dev/null || uv sync
    info "依赖安装完成 ✓"
}

# ============================================================================
# 准备管理面板 (ui/dist)
# 优先级: 从 GitHub Release 下载预编译 tarball → 本地 npm build → 跳过并警告
# ============================================================================
setup_ui() {
    cd "$INSTALL_DIR"

    if [ -f "ui/dist/index.html" ]; then
        info "管理面板已存在, 跳过构建 ✓"
        return
    fi

    # 尝试从 latest release 拉取 ui-dist.tar.gz
    if command -v curl > /dev/null 2>&1; then
        info "尝试从 GitHub Release 下载预编译面板..."
        DIST_URL=$(curl -fsSL "$API_URL/releases/latest" 2>/dev/null \
            | grep -oE '"browser_download_url":[[:space:]]*"[^"]*ui-dist\.tar\.gz"' \
            | head -1 \
            | cut -d'"' -f4)

        if [ -n "$DIST_URL" ]; then
            if curl -fsSL "$DIST_URL" -o /tmp/mnemosync-ui-dist.tar.gz 2>/dev/null; then
                tar -xzf /tmp/mnemosync-ui-dist.tar.gz -C ui/
                rm -f /tmp/mnemosync-ui-dist.tar.gz
                if [ -f "ui/dist/index.html" ]; then
                    info "预编译面板下载完成 ✓"
                    return
                fi
            fi
            warn "预编译面板下载失败, 尝试本地构建"
        else
            warn "未找到预编译面板产物, 尝试本地构建"
        fi
    fi

    # 本地 npm build
    if command -v npm > /dev/null 2>&1; then
        info "本地构建管理面板 (需要 Node.js 22+)..."
        (
            cd ui
            npm install
            npm run build
        )
        if [ -f "ui/dist/index.html" ]; then
            info "本地构建完成 ✓"
            return
        fi
        warn "本地构建失败"
    fi

    warn "跳过管理面板 (未安装 Node.js 且下载预编译产物失败)"
    warn "后端 API 仍可用, 但面板 /panel 将 404"
    warn "如需启用面板, 请手动执行: cd $INSTALL_DIR/ui && npm install && npm run build"
}

# ============================================================================
# 初始化数据库
# ============================================================================
init_database() {
    info "初始化数据库..."
    cd "$INSTALL_DIR"
    uv run python -m src.main init-internal
    info "数据库初始化完成 ✓"
}

# ============================================================================
# 注册命令
# ============================================================================
register_command() {
    mkdir -p "$BIN_DIR"

    # 创建符号链接
    ln -sf "$INSTALL_DIR/.venv/bin/mnemosync" "$BIN_DIR/mnemosync"

    info "命令注册完成 ✓"

    # 检查 PATH
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo ""
        warn "目录 $BIN_DIR 不在 PATH 中"
        echo ""
        echo "请将以下内容添加到你的 shell 配置文件:"
        echo ""
        echo "  # 对于 bash:"
        echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
        echo "  source ~/.bashrc"
        echo ""
        echo "  # 对于 zsh:"
        echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
        echo "  source ~/.zshrc"
        echo ""
    fi
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    echo ""
    echo "========================================="
    echo "  Mnemosync 安装程序"
    echo "========================================="
    echo ""

    check_dependencies
    install_uv
    setup_code
    install_deps
    setup_ui
    init_database
    register_command

    echo ""
    echo "========================================="
    echo "  安装完成！"
    echo "========================================="
    echo ""
    echo "使用方法:"
    echo ""
    echo "  mnemosync serve     # 启动服务"
    echo "  mnemosync login     # 进入交互式 CLI"
    echo "  mnemosync help      # 查看帮助"
    echo ""
    echo "配置文件: $INSTALL_DIR/config.local.toml"
    echo "数据目录: $INSTALL_DIR/data/"
    echo ""
    echo "首次使用请先编辑配置文件，填入你的 LLM 服务商 API Key。"
    echo ""
}

main "$@"
