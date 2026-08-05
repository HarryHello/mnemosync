"""版本更新检查器测试."""

from __future__ import annotations

from src.infra.update_checker import _parse_version


def test_parse_version_basic():
    assert _parse_version("v0.3.5") == (0, 3, 5)
    assert _parse_version("1.0.0") == (1, 0, 0)
    assert _parse_version("v2.10.3") == (2, 10, 3)


def test_parse_version_no_v_prefix():
    assert _parse_version("0.3.5") == (0, 3, 5)


def test_parse_version_empty():
    assert _parse_version("") == (0,)


def test_parse_version_partial():
    assert _parse_version("v0.3") == (0, 3)
    assert _parse_version("1") == (1,)


def test_parse_version_non_numeric():
    # 非数字部分被忽略
    assert _parse_version("v0.3.5-beta") == (0, 3)


def test_version_comparison():
    assert _parse_version("v0.3.5") > _parse_version("v0.3.4")
    assert _parse_version("v0.4.0") > _parse_version("v0.3.5")
    assert _parse_version("v1.0.0") > _parse_version("v0.9.9")
    assert _parse_version("v0.3.5") == _parse_version("0.3.5")
