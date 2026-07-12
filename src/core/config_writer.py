"""Config file writer - sync CLI changes to config.local.toml."""

import os
import re
from pathlib import Path


def get_config_path() -> Path:
    """获取配置文件路径."""
    # 优先使用环境变量
    if os.getenv("MNEMOSYNC_CONFIG"):
        return Path(os.getenv("MNEMOSYNC_CONFIG"))

    # 使用项目根目录下的 config.local.toml
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    return project_root / "config.local.toml"


def read_config() -> str:
    """读取配置文件内容."""
    config_path = get_config_path()
    if not config_path.exists():
        # 创建默认配置
        return _create_default_config()
    return config_path.read_text(encoding="utf-8")


def write_config(content: str) -> None:
    """写入配置文件."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")


def _create_default_config() -> str:
    """创建默认配置."""
    return """# Mnemosync 本地开发配置

[chat]
base_url = ""
api_key  = ""
main_model = ""
assist_model = ""

[embedding]
base_url = ""
api_key  = ""
model    = ""
dimensions = 1024

[rerank]
base_url = ""
api_key  = ""
model    = ""
"""


def update_model(section: str, model_key: str, model_value: str, 
                 base_url: str = "", api_key: str = "") -> bool:
    """更新配置文件中的模型设置.

    Args:
        section: 配置段名 (chat, embedding, rerank)
        model_key: 模型字段名 (main_model, assist_model, model)
        model_value: 模型值
        base_url: 基础URL (可选，如已存在则不覆盖)
        api_key: API密钥 (可选，如已存在则不覆盖)

    Returns:
        bool: 是否成功
    """
    content = read_config()
    lines = content.split("\n")

    # 找到 section 的位置
    section_start = -1
    section_end = len(lines)

    for i, line in enumerate(lines):
        if re.match(rf"^\[{re.escape(section)}\]", line):
            section_start = i
        elif section_start >= 0 and line.startswith("[") and i > section_start:
            section_end = i
            break

    if section_start == -1:
        # Section 不存在，添加到末尾
        lines.append("")
        lines.append(f"[{section}]")
        if base_url:
            lines.append(f'base_url = "{base_url}"')
        if api_key:
            lines.append(f'api_key  = "{api_key}"')
        lines.append(f'{model_key} = "{model_value}"')
    else:
        # Section 存在，更新或添加字段
        updated_model = False
        updated_base_url = False
        updated_api_key = False

        for i in range(section_start + 1, section_end):
            line = lines[i]

            # 更新模型
            if re.match(rf"^{model_key}\s*=", line):
                lines[i] = f'{model_key} = "{model_value}"'
                updated_model = True

            # 更新 base_url (如果提供了新的)
            if base_url and re.match(r"^base_url\s*=", line):
                lines[i] = f'base_url = "{base_url}"'
                updated_base_url = True

            # 更新 api_key (如果提供了新的)
            if api_key and re.match(r"^api_key\s*=", line):
                lines[i] = f'api_key  = "{api_key}"'
                updated_api_key = True

        # 如果字段不存在，添加到 section 末尾
        if not updated_model:
            insert_pos = section_end
            lines.insert(insert_pos, f'{model_key} = "{model_value}"')
            section_end += 1

        if base_url and not updated_base_url:
            # 在 section 开头插入 base_url
            insert_pos = section_start + 1
            lines.insert(insert_pos, f'base_url = "{base_url}"')
            section_end += 1

        if api_key and not updated_api_key:
            # 在 base_url 后插入 api_key
            for i in range(section_start + 1, section_end):
                if "base_url" in lines[i]:
                    lines.insert(i + 1, f'api_key  = "{api_key}"')
                    section_end += 1
                    break

    write_config("\n".join(lines))
    return True


def update_chat_model(main_model: str = None, assist_model: str = None,
                      base_url: str = "", api_key: str = "") -> bool:
    """更新 chat 模型配置."""
    content = read_config()
    lines = content.split("\n")

    section_start = -1
    section_end = len(lines)

    for i, line in enumerate(lines):
        if re.match(r"^\[chat\]", line):
            section_start = i
        elif section_start >= 0 and line.startswith("[") and i > section_start:
            section_end = i
            break

    if section_start == -1:
        # 添加新 section
        lines.append("")
        lines.append("[chat]")
        if base_url:
            lines.append(f'base_url = "{base_url}"')
        if api_key:
            lines.append(f'api_key  = "{api_key}"')
        if main_model:
            lines.append(f'main_model = "{main_model}"')
        if assist_model:
            lines.append(f'assist_model = "{assist_model}"')
    else:
        # 更新现有 section
        for i in range(section_start + 1, section_end):
            line = lines[i]
            if main_model and re.match(r"^main_model\s*=", line):
                lines[i] = f'main_model = "{main_model}"'
            if assist_model and re.match(r"^assist_model\s*=", line):
                lines[i] = f'assist_model = "{assist_model}"'
            if base_url and re.match(r"^base_url\s*=", line):
                lines[i] = f'base_url = "{base_url}"'
            if api_key and re.match(r"^api_key\s*=", line):
                lines[i] = f'api_key  = "{api_key}"'

    write_config("\n".join(lines))
    return True


def get_current_config() -> dict:
    """获取当前配置（简化版）."""
    content = read_config()
    config = {}

    current_section = None
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            config[current_section] = {}
        elif "=" in line and current_section:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            config[current_section][key] = value

    return config


def get_model_for_section(section: str) -> str:
    """获取指定 section 的模型名."""
    config = get_current_config()
    if section not in config:
        return ""

    section_config = config[section]
    if section == "chat":
        return section_config.get("main_model", "")
    else:
        return section_config.get("model", "")
