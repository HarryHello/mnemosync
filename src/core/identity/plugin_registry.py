"""插件发现与加载 (v0.3.1).

扫描 plugins/ 目录, 自动发现所有实现 IdentityPlugin 的类。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.identity.plugin import IdentityPlugin

logger = logging.getLogger(__name__)

# 插件目录: 项目根目录下的 plugins/
PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "plugins"


def discover_plugins() -> dict[str, "IdentityPlugin"]:
    """扫描 plugins/ 目录, 返回 {plugin_name: plugin_instance}.

    插件目录结构::

        plugins/
          astrbot.py          # 一个文件一个插件
          maibot.py
          my-adapter/
            __init__.py        # 也支持子目录

    每个插件文件必须定义一个继承 IdentityPlugin 的类,
    类名不限, 系统通过 name 属性识别。
    """
    from src.core.identity.plugin import IdentityPlugin

    if not PLUGIN_DIR.exists():
        logger.info("插件目录不存在, 跳过扫描: %s", PLUGIN_DIR)
        return {}

    plugins: dict[str, "IdentityPlugin"] = {}

    for py_file in _find_plugin_files(PLUGIN_DIR):
        try:
            instances = _load_plugins_from_file(py_file)
            for inst in instances:
                if not isinstance(inst, IdentityPlugin):
                    continue
                if not inst.name:
                    logger.warning("插件 %s 未设置 name, 跳过", inst.__class__.__name__)
                    continue
                if inst.name in plugins:
                    logger.warning(
                        "插件名冲突: %s (来自 %s 和 %s), 后者覆盖",
                        inst.name,
                        plugins[inst.name].__class__.__name__,
                        inst.__class__.__name__,
                    )
                plugins[inst.name] = inst
                logger.info("已加载插件: %s (%s)", inst.name, inst.description)
        except Exception as e:
            logger.warning("加载插件失败 %s: %s", py_file, e)

    return plugins


def _find_plugin_files(root: Path) -> list[Path]:
    """查找所有插件文件."""
    files: list[Path] = []

    # 直接的 .py 文件
    for py_file in sorted(root.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        files.append(py_file)

    # 子目录
    for subdir in sorted(root.glob("*/")):
        if subdir.name.startswith("_") or subdir.name.startswith("__"):
            continue
        init = subdir / "__init__.py"
        if init.exists():
            files.append(init)

    return files


def _load_plugins_from_file(path: Path) -> list:
    """从 .py 文件加载所有 IdentityPlugin 子类实例."""
    from src.core.identity.plugin import IdentityPlugin

    module_name = f"mnemosync_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    instances = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, IdentityPlugin)
            and attr is not IdentityPlugin
        ):
            instances.append(attr())
    return instances