#!/bin/bash
# Mnemosync 安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/HarryHello/mnemosync/main/install.sh | sh
#
# 需要: bash 4.0+, git, Python 3.12+
# 可选: uv (脚本会自动安装)

set -e

# ============================================================================
# 配置
# ============================================================================
REPO_URL="https://github.com/HarryHello/mnemosync.git"
INSTALL_DIR="${MNEMOSYNC_INSTALL_DIR:-$HOME/.mnemosync}"
BIN_DIR="${MNEMOSYNC_BIN_DIR:-$HOME/.local/bin}"
BRANCH="${MNEMOSYNC_BRANCH:-main}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ============================================================================
# 检查依赖
# ============================================================================
check_dependencies() {
    # 检查 git
    if ! command -v git &> /dev/null; then
        error "git 未安装。请先安装 git:\n  Ubuntu/Debian: sudo apt-get install git\n  macOS: xcode-select --install"
    fi

    # 检查 Python
    if ! command -v python3 &> /dev/null; then
        error "python3 未安装。请先安装 Python 3.12+: https://www.python.org/downloads/"
    fi

    # 检查 Python 版本
    PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 12 ]); then
        error "Python 版本过低: $PYTHON_VERSION (需要 3.12+)"
    fi

    info "Python $PYTHON_VERSION ✓"
}

# ============================================================================
# 安装 uv
# ============================================================================
install_uv() {
    if command -v uv &> /dev/null; then
        info "uv 已安装 ✓"
        return
    fi

    info "安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # 确保 uv 在 PATH 中
    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv &> /dev/null; then
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
# 安装依赖
# ============================================================================
install_deps() {
    info "安装依赖..."
    cd "$INSTALL_DIR"
    uv sync --frozen 2>/dev/null || uv sync
    info "依赖安装完成 ✓"
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
        echo '  echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.bashrc'
        echo '  source ~/.bashrc'
        echo ""
        echo "  # 对于 zsh:"
        echo '  echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshrc'
        echo '  source ~/.zshrc'
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
