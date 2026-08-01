"""输入工具与退出处理."""

import signal
import sys
import termios
import tty

# 全局退出标志
_exit_requested = False


def secure_input(prompt: str = "") -> str:
    """安全输入，显示星号代替字符."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars = []

    try:
        tty.setraw(fd)
        sys.stdout.write(prompt)
        sys.stdout.flush()

        while True:
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                break
            elif ch == '\x7f' or ch == '\x08':  # Backspace
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif ch.isprintable():
                chars.append(ch)
                sys.stdout.write('*')
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ''.join(chars)


def mask_api_key(key: str) -> str:
    """遮蔽 API Key，只显示前4位和后4位."""
    if len(key) <= 8:
        return key
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def setup_exit_handler():
    """设置全局退出处理器."""
    global _exit_requested

    def signal_handler(sig, frame):
        _exit_requested = True
        raise KeyboardInterrupt("Ctrl+C pressed")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def check_exit_requested():
    """检查是否请求退出."""
    if _exit_requested:
        print("\n\n👋 Exiting CLI (Mnemosync service keeps running in background).\n")
        return True
    return False
