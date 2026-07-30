#!/bin/bash
# Mnemosync 卸载脚本
#
# 用法 (推荐):
#   ~/.mnemosync/uninstall.sh
#
# 脚本自动以自身所在目录为安装目录, 无需指定路径.
# 若通过 curl 远程执行 (无本地副本), 才回退到符号链接反推.
#
# 清理内容:
#   1. 符号链接 ~/.local/bin/mnemosync
#   2. 安装目录 (代码 + venv)
#   3. 可选: 保留 data/ 和 config.local.toml (默认保留)
#   4. 可选: 卸载 uv (默认不卸载, 可能被其他项目复用)

set -e

# ============================================================================
# 确定安装目录
#   优先级:
#     1. 脚本自身所在目录 (最可靠, 脚本就在 INSTALL_DIR 里)
#     2. 符号链接反推 (兜底: curl 远程执行场景)
#     3. 默认 ~/.mnemosync
# ============================================================================
BIN_DIR="${MNEMOSYNC_BIN_DIR:-$HOME/.local/bin}"
SYMLINK="$BIN_DIR/mnemosync"

# 1. 脚本自身所在目录
# $0 可能是相对路径, 先 cd 到脚本目录再 pwd 获取绝对路径
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P || true)
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    # 2. 兜底: 通过符号链接反推
    #    install.sh 创建: $BIN_DIR/mnemosync → $INSTALL_DIR/.venv/bin/mnemosync
    if [ -L "$SYMLINK" ]; then
        target=$(readlink -f "$SYMLINK" 2>/dev/null || readlink "$SYMLINK")
        INSTALL_DIR=$(dirname "$(dirname "$(dirname "$target")")")
    elif [ -d "$HOME/.mnemosync" ]; then
        INSTALL_DIR="$HOME/.mnemosync"
    else
        INSTALL_DIR=""
    fi
fi

# ============================================================================
# 颜色与辅助函数
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { printf "${GREEN}[INFO]${NC} %s\n" "$1"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$1"; exit 1; }
muted()   { printf "${CYAN}%s${NC}\n" "$1"; }

confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local hint
    if [ "$default" = "y" ]; then
        hint="[Y/n]"
    else
        hint="[y/N]"
    fi
    printf "${YELLOW}%s${NC} ${hint} " "$prompt"
    read -r reply
    reply="${reply:-$default}"
    case "$reply" in
        [Yy]*) return 0 ;;
        *) return 1 ;;
    esac
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    echo ""
    echo "========================================="
    echo "  Mnemosync 卸载程序"
    echo "========================================="
    echo ""

    # ── 1. 定位安装 ──────────────────────
    local found_link=false
    local found_dir=false

    if [ -L "$SYMLINK" ]; then
        found_link=true
    fi

    if [ -n "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR" ]; then
        found_dir=true
    fi

    if ! $found_link && ! $found_dir; then
        warn "未检测到 Mnemosync 安装"
        echo ""
        muted "  检查过的路径:"
        muted "    符号链接: $SYMLINK"
        muted "    默认目录: $HOME/.mnemosync"
        if [ -n "$MNEMOSYNC_INSTALL_DIR" ]; then
            muted "    环境变量: $MNEMOSYNC_INSTALL_DIR"
        fi
        echo ""
        muted "  若使用了自定义安装目录, 请设置环境变量后重试:"
        muted "    export MNEMOSYNC_INSTALL_DIR=/your/custom/path"
        muted "    bash $0"
        echo ""
        exit 1
    fi

    # ── 2. 展示将要清理的内容 ──────────────────────
    echo "将要清理以下内容:"
    $found_link && echo "  • 命令链接: $SYMLINK"
    $found_dir  && echo "  • 安装目录: $INSTALL_DIR"
    echo ""

    if ! confirm "确认卸载 Mnemosync?"; then
        info "已取消卸载"
        exit 0
    fi

    # ── 3. 删除符号链接 ──────────────────────
    echo ""
    if $found_link; then
        rm -f "$SYMLINK"
        info "已删除命令链接: $SYMLINK ✓"
    else
        info "命令链接不存在, 跳过"
    fi

    # ── 4. 处理安装目录 ──────────────────────
    if $found_dir; then
        local has_data=false
        local has_config=false
        [ -d "$INSTALL_DIR/data" ] && [ -n "$(ls -A "$INSTALL_DIR/data" 2>/dev/null)" ] && has_data=true
        [ -f "$INSTALL_DIR/config.local.toml" ] && has_config=true

        if $has_data || $has_config; then
            echo ""
            echo "检测到用户数据:"
            $has_data    && echo "  • 数据目录: $INSTALL_DIR/data/"
            $has_config  && echo "  • 配置文件: $INSTALL_DIR/config.local.toml"
            echo ""

            if confirm "是否保留用户数据 (data/ 和 config.local.toml)?" "y"; then
                local backup_dir
                backup_dir=$(mktemp -d /tmp/mnemosync-backup.XXXXXX)
                $has_data   && cp -r "$INSTALL_DIR/data" "$backup_dir/"
                $has_config && cp "$INSTALL_DIR/config.local.toml" "$backup_dir/"

                rm -rf "$INSTALL_DIR"
                mkdir -p "$INSTALL_DIR"

                $has_data   && cp -r "$backup_dir/data" "$INSTALL_DIR/"
                $has_config && cp "$backup_dir/config.local.toml" "$INSTALL_DIR/"
                rm -rf "$backup_dir"

                info "已保留用户数据 ✓"
                info "用户数据位置: $INSTALL_DIR/"
                echo ""
                info "已删除代码和虚拟环境 ✓"
            else
                rm -rf "$INSTALL_DIR"
                info "已删除整个安装目录 (含用户数据) ✓"
            fi
        else
            rm -rf "$INSTALL_DIR"
            info "已删除安装目录 ✓"
        fi
    else
        info "安装目录不存在, 跳过"
    fi

    # ── 5. 可选: 卸载 uv ──────────────────────
    echo ""
    if command -v uv > /dev/null 2>&1; then
        if confirm "是否卸载 uv? (可能被其他项目复用)" "n"; then
            rm -f "$BIN_DIR/uv" "$BIN_DIR/uvx"
            rm -rf "$HOME/.local/share/uv" "$HOME/.cache/uv" "$HOME/.cargo/bin/uv"
            info "已卸载 uv ✓"
        else
            info "保留 uv (可能被其他项目使用)"
        fi
    fi

    # ── 6. 完成 ──────────────────────
    echo ""
    echo "========================================="
    echo "  卸载完成！"
    echo "========================================="
    echo ""

    if [ -d "$INSTALL_DIR" ]; then
        info "用户数据已保留在 $INSTALL_DIR/ (如需彻底清理可手动 rm -rf)"
    fi

    if [[ ":$PATH:" == *":$BIN_DIR:"* ]]; then
        muted "提示: $BIN_DIR 仍在 PATH 中, 如需移除:"
        muted "  echo '' >> ~/.bashrc && echo 'remove $BIN_DIR from PATH'  # 手动编辑"
    fi
}

main "$@"
