#!/bin/bash
# Mnemosync 安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/HarryHello/mnemosync/dev/install.sh | sh
#
# 需要: git
# 可选: uv (脚本会自动安装), node + npm (若需要本地 build UI 而非从 Release 下载)
# 备注: Python 3.12+ 若系统未安装, uv 会在同步依赖时自动下载并管理, 无需手动准备.
#
# 环境变量:
#   GITHUB_PROXY        GitHub 代理前缀, 如 https://ghproxy.com/
#   MNEMOSYNC_INSTALL_DIR  自定义安装目录 (默认 ~/.mnemosync)
#   MNEMOSYNC_DIR          MNEMOSYNC_INSTALL_DIR 的别名 (面板/CLI 升级时自动传入实际安装位置)
#   MNEMOSYNC_BIN_DIR   自定义命令目录 (默认 ~/.local/bin)
#   MNEMOSYNC_BRANCH    自定义分支 (默认 dev; 此分支的 install.sh 默认装本分支)
#   MNEMOSYNC_RELEASE_TAG  预编译 UI 的 release tag (默认 latest)
#   MNEMOSYNC_VERSION   锁定安装/升级到指定版本 tag (如 v0.4.0), 默认跟随分支最新
#
# 使用代理安装示例:
#   GITHUB_PROXY=https://ghproxy.com/ curl -fsSL https://ghproxy.com/https://raw.githubusercontent.com/HarryHello/mnemosync/dev/install.sh | sh

set -e

# ============================================================================
# 配置
# ============================================================================
GITHUB_PROXY="${GITHUB_PROXY:-}"
REPO_URL="${GITHUB_PROXY}https://github.com/HarryHello/mnemosync.git"
API_URL="${GITHUB_PROXY}https://api.github.com/repos/HarryHello/mnemosync"
INSTALL_DIR="${MNEMOSYNC_INSTALL_DIR:-${MNEMOSYNC_DIR:-$HOME/.mnemosync}}"
BIN_DIR="${MNEMOSYNC_BIN_DIR:-$HOME/.local/bin}"
BRANCH="${MNEMOSYNC_BRANCH:-dev}"
RELEASE_TAG="${MNEMOSYNC_RELEASE_TAG:-latest}"

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
# 版本工具
# ============================================================================

# 从 pyproject.toml 提取版本号
_version_of() {
    sed -n 's/^version = "\(.*\)"/\1/p' "$1" 2>/dev/null | head -1
}

# semver 比较 (低版本 < Beta版 < 正式版): $1 > $2 ? 返回 0 : 返回 1
# 规则: 数字部分先比, 相同则正式版(无 pre) > Beta版(有 pre)
_version_gt() {
    perl -e '
        sub parts {
            my ($v) = @_; $v =~ s/^v//;
            my ($num, $pre) = $v =~ /^(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.\-]+))?$/;
            $num //= "0";
            my @n = split /\./, $num; push @n, 0 while @n < 3;
            return (\@n, $pre // "");
        }
        sub cmp_ver {
            my ($a,$b)=@_;
            my ($na,$pa)=parts($a); my ($nb,$pb)=parts($b);
            for my $i (0..2){
                return 1 if $na->[$i] > $nb->[$i];
                return -1 if $na->[$i] < $nb->[$i];
            }
            return 1 if $pa eq "" && $pb ne "";
            return -1 if $pa ne "" && $pb eq "";
            return $pa cmp $pb;
        }
        exit 0 if cmp_ver($ARGV[0], $ARGV[1]) > 0;
        exit 1;
    ' "$1" "$2"
}

# 版本降级检测: 只能升不能降 (低版本 < Beta版 < 正式版)
check_not_downgrade() {
    local current_ver target_ver
    current_ver=$(_version_of "$INSTALL_DIR/pyproject.toml")
    if [ -n "$MNEMOSYNC_VERSION" ]; then
        # 锁定版本: 目标版本 = 指定 tag 的 pyproject 版本
        target_ver=$(git show "$MNEMOSYNC_VERSION:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
    else
        target_ver=$(git show "origin/$BRANCH:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
    fi
    # 缺版本信息 (如全新安装或无法读取) 时跳过检查
    [ -z "$current_ver" ] && return 0
    [ -z "$target_ver" ] && return 0
    [ "$current_ver" = "$target_ver" ] && return 0  # 同版本, 允许重装
    if _version_gt "$current_ver" "$target_ver"; then
        error "版本降级被拒绝: 当前 $current_ver → 目标 $target_ver。只能升级 (低版本 < Beta版 < 正式版)。"
    fi
    info "版本检查通过: $current_ver → $target_ver"
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
            # 数据保护 (v0.4.1): 非 git 仓库重建前先移出 data/, 重建后恢复 —
            # 绝不静默删除用户数据
            _data_bak=""
            if [ -d "$INSTALL_DIR/data" ] && [ -n "$(ls -A "$INSTALL_DIR/data" 2>/dev/null)" ]; then
                _data_bak="${INSTALL_DIR}.data-$(date +%Y%m%d-%H%M%S)"
                warn "检测到数据目录, 先备份到 $_data_bak (重建后自动恢复)"
                mv "$INSTALL_DIR/data" "$_data_bak"
            fi
            rm -rf "$INSTALL_DIR"
            git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
            cd "$INSTALL_DIR"
            if [ -n "$_data_bak" ]; then
                mkdir -p data
                mv "$_data_bak" "$INSTALL_DIR/data"
                info "数据目录已恢复到 $INSTALL_DIR/data"
            fi
        fi

        # 拉取最新代码 (支持代理)
        if [ -n "$GITHUB_PROXY" ]; then
            git remote set-url origin "$REPO_URL"
        fi
        git fetch origin "$BRANCH"
        if [ -n "$MNEMOSYNC_VERSION" ]; then
            # 锁定版本: 拉取指定 tag
            git fetch origin tag "$MNEMOSYNC_VERSION"
        fi
        # 版本降级检测 (只能升不能降)
        check_not_downgrade
        if [ -n "$MNEMOSYNC_VERSION" ]; then
            # 检出指定版本 tag (pinned 本地分支)
            git checkout -B "pin/$MNEMOSYNC_VERSION" "$MNEMOSYNC_VERSION"
            git reset --hard "$MNEMOSYNC_VERSION"
        else
            # 正确切换本地分支名 + 硬重置到目标分支
            git checkout -B "$BRANCH" "origin/$BRANCH"
            git reset --hard "origin/$BRANCH"
        fi
    else
        info "下载 Mnemosync..."
        git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi

    info "代码就绪 ✓"
}

# ============================================================================
# 安装依赖 (uv sync 会按 pyproject.toml requires-python 自动准备 Python)
# 自动检测 PyPI 连通性, 不通时切换到镜像源
# ============================================================================
install_deps() {
    info "安装依赖 (uv 会按需下载 Python 3.12+)..."
    cd "$INSTALL_DIR"

    # 检测 PyPI 连通性, 自动切换镜像
    if [ -z "$UV_INDEX_URL" ]; then
        if ! curl -s --connect-timeout 5 --max-time 10 "https://pypi.org/simple/" > /dev/null 2>&1; then
            warn "pypi.org 不可达, 尝试使用镜像源..."
            for mirror in \
                "https://pypi.tuna.tsinghua.edu.cn/simple" \
                "https://mirrors.aliyun.com/pypi/simple"; do
                if curl -s --connect-timeout 5 --max-time 10 "$mirror/" > /dev/null 2>&1; then
                    export UV_INDEX_URL="$mirror"
                    info "已切换到镜像: $mirror"
                    break
                fi
            done
            if [ -z "$UV_INDEX_URL" ]; then
                warn "所有镜像均不可达, 使用默认源 (可能较慢)"
            fi
        fi
    fi

    uv sync --frozen 2>/dev/null || uv sync
    info "依赖安装完成 ✓"
}

# ============================================================================
# 准备管理面板 (ui/dist)
# 优先级: 从 GitHub Release 下载预编译 tarball → 本地 npm build → 跳过并警告
# ============================================================================
setup_ui() {
    cd "$INSTALL_DIR"

    # 升级时清除旧面板 (确保下载新版)
    if [ -f "ui/dist/index.html" ]; then
        info "更新管理面板..."
        rm -rf ui/dist
    fi

    # 锁定版本时, UI 用对应版本的 release tag
    if [ -n "$MNEMOSYNC_VERSION" ]; then
        RELEASE_TAG="$MNEMOSYNC_VERSION"
    fi

    # 尝试从 release 拉取 ui-dist.tar.gz (latest 或指定 RELEASE_TAG)
    if command -v curl > /dev/null 2>&1; then
        if [ "$RELEASE_TAG" = "latest" ]; then
            RELEASE_URL="$API_URL/releases/latest"
        else
            RELEASE_URL="$API_URL/releases/tags/$RELEASE_TAG"
        fi
        info "尝试从 GitHub Release ($RELEASE_TAG) 下载预编译面板..."
        DIST_URL=$(curl -fsSL "$RELEASE_URL" 2>/dev/null \
            | grep -oE '"browser_download_url":[[:space:]]*"[^"]*ui-dist\.tar\.gz"' \
            | head -1 \
            | cut -d'"' -f4)

        if [ -n "$DIST_URL" ]; then
            DIST_URL="${GITHUB_PROXY}${DIST_URL}"
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
    echo "如需卸载: $INSTALL_DIR/uninstall.sh"
    echo ""
}

main "$@"
