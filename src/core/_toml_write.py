r'''简易 TOML 序列化器: 覆盖项目所需的 basic string + 多行 basic string + 嵌套表.

用于将 persona override 等小型配置写入 .toml 文件. 不依赖第三方库,
匹配 Python 3.11+ 内置 tomllib 的读取能力.

支持:
- Basic string: 正确转义反斜杠、双引号、控制字符
- Multi-line basic string: 保留换行, 转义反斜杠与三双引号
- 嵌套 table
'''

from __future__ import annotations

from datetime import datetime
from typing import Any


def _escape_basic_string(s: str) -> str:
    out: list[str] = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _escape_multiline_basic(s: str) -> str:
    """TOML multi-line basic string 转义.

    - 反斜杠转义为双反斜杠
    - 三个连续双引号转义为 反斜杠+三双引号
    - 保留换行
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"""', '\\"""')
    return s


def _format_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_basic_string(value)}"'
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def dumps_toml(data: dict[str, Any], *, comments: list[str] | None = None) -> str:
    """将嵌套 dict 序列化为 TOML 字符串.

    - 顶层 key-value 作为 inline 字段
    - 嵌套 dict 作为 [section] 表头
    - 仅支持项目使用的子集 (str/int/bool/float/datetime)
    """
    lines: list[str] = []
    if comments:
        for c in comments:
            lines.append(f"# {c}")
        lines.append("")

    scalars: dict[str, Any] = {}
    tables: dict[str, dict[str, Any]] = {}

    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        else:
            scalars[key] = value

    for key, value in scalars.items():
        lines.append(f"{key} = {_format_value(value)}")

    if scalars and tables:
        lines.append("")

    for table_name, table_data in tables.items():
        lines.append(f"[{table_name}]")
        for key, value in table_data.items():
            lines.append(f"{key} = {_format_value(value)}")
        lines.append("")

    return "\n".join(lines)
