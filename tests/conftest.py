"""Pytest 顶层 conftest.

- 保证 tests 能 import src.* (从项目根目录跑 pytest 就无需 sys.path 调整,
  但通过 conftest 显式声明更稳)
- 提供 reset_settings 钩子, 让依赖 Settings 单例的用例互不污染
"""

from __future__ import annotations

import pytest
from src.core.config import _reset_settings


@pytest.fixture(autouse=True)
def _reset_settings_singleton():
    """每个用例前后重置 Settings 单例, 防止跨用例污染."""
    _reset_settings()
    yield
    _reset_settings()
