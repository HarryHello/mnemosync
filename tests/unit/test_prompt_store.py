"""PromptStore CRUD / 校验 / 备份 / 回退 单元测试.

测试用独立临时目录, 不依赖全局单例 (避免污染其他测试).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.core.prompts.registry import PROMPT_REGISTRY
from src.core.prompts.store import BACKUP_KEEP, PromptStore

# v0.2.9: memory_analysis 新增 4 个占位符, 用一个常量集中管理测试用 body 片段
_MA_ALL_PH = (
    "SOURCE=__SOURCE_USER__ CONV=__CONVERSATION__ DECAY=__DECAY_TARGETS__ "
    "PN=__PERSONA_NAME__ PA=__PERSONA_ADDRESSING__ UA=__USER_ADDRESSING__ RC=__RELATION_CONTEXT__"
)


# ─── 辅助 ──────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> PromptStore:
    """在 tmp_path 下搭 default + override, 写一份最小 default."""
    default_dir = tmp_path / "defaults"
    override_dir = tmp_path / "overrides"
    default_dir.mkdir()
    override_dir.mkdir()

    # 为 registry 中每个 name 生成含所有占位符的默认文件
    for name, spec in PROMPT_REGISTRY.items():
        body = f"# default for {name}\n"
        for ph in spec.placeholders:
            body += f"placeholder: __{ph}__\n"
        (default_dir / f"{name}.md").write_text(body, encoding="utf-8")

    return PromptStore(override_dir=override_dir, default_dir=default_dir)


# ─── load / load_default ─────────────────────────────────


def test_load_falls_back_to_default_when_no_override(store: PromptStore) -> None:
    text = store.load("memory_analysis")
    assert "default for memory_analysis" in text


def test_override_wins_over_default(store: PromptStore) -> None:
    body = "# custom memory_analysis\n" + _MA_ALL_PH + "\n"
    store.save("memory_analysis", body)
    assert store.load("memory_analysis") == body


def test_frontmatter_is_stripped_on_load(store: PromptStore) -> None:
    body = "---\nversion: 3\n---\n" + _MA_ALL_PH
    store.save("memory_analysis", body)
    loaded = store.load("memory_analysis")
    assert loaded.startswith("SOURCE=")  # frontmatter 已去除
    assert "version" not in loaded


def test_load_raw_keeps_frontmatter(store: PromptStore) -> None:
    body = "---\nversion: 5\n---\n" + _MA_ALL_PH
    store.save("memory_analysis", body)
    raw = store.load_raw("memory_analysis")
    assert raw.startswith("---\nversion: 5\n---")


def test_corrupted_yaml_frontmatter_falls_back_gracefully(
    store: PromptStore, caplog: pytest.LogCaptureFixture
) -> None:
    """YAML 损坏 → warn + 忽略 frontmatter, body 仍可用."""
    bad = (
        "---\n"
        "version: [unterminated\n"
        "---\n"
        + _MA_ALL_PH
    )
    # 直接写文件 (绕过 save 校验)
    (store.override_dir / "memory_analysis.md").write_text(bad, encoding="utf-8")

    with caplog.at_level("WARNING"):
        text = store.load("memory_analysis")
    assert "SOURCE=" in text
    assert any("frontmatter" in r.message for r in caplog.records)


# ─── validate ────────────────────────────────────────────


def test_validate_ok(store: PromptStore) -> None:
    body = _MA_ALL_PH
    r = store.validate("memory_analysis", body)
    assert r.ok
    assert r.missing_placeholders == []


def test_validate_missing_placeholder(store: PromptStore) -> None:
    body = "SOURCE=__SOURCE_USER__ CONV=__CONVERSATION__"  # 缺 DECAY_TARGETS
    r = store.validate("memory_analysis", body)
    assert not r.ok
    assert "DECAY_TARGETS" in r.missing_placeholders
    assert r.error and "__DECAY_TARGETS__" in r.error


def test_validate_ignores_frontmatter_body_split(store: PromptStore) -> None:
    """frontmatter 里出现占位符不算数, 必须在 body 里."""
    body = (
        "---\nplaceholders: [SOURCE_USER, CONVERSATION, DECAY_TARGETS]\n---\n"
        "only __SOURCE_USER__ and __CONVERSATION__"  # 缺 DECAY_TARGETS
    )
    r = store.validate("memory_analysis", body)
    assert not r.ok
    assert "DECAY_TARGETS" in r.missing_placeholders


# ─── save 拒绝非法内容 ────────────────────────────────────


def test_save_refuses_missing_placeholders(store: PromptStore) -> None:
    with pytest.raises(ValueError):
        store.save("memory_analysis", "只有 __SOURCE_USER__")
    assert not (store.override_dir / "memory_analysis.md").exists()


# ─── registry 白名单 (路径穿越防御) ────────────────────


def test_unknown_name_rejected_on_load(store: PromptStore) -> None:
    with pytest.raises(KeyError):
        store.load("../../../etc/passwd")


def test_unknown_name_rejected_on_save(store: PromptStore) -> None:
    with pytest.raises(KeyError):
        store.save("unknown_agent", "__X__")


def test_unknown_name_rejected_on_validate(store: PromptStore) -> None:
    with pytest.raises(KeyError):
        store.validate("../evil", "content")


# ─── reset ──────────────────────────────────────────────


def test_reset_deletes_override_and_backs_up(store: PromptStore) -> None:
    body = _MA_ALL_PH
    store.save("memory_analysis", body)
    assert (store.override_dir / "memory_analysis.md").is_file()

    changed = store.reset("memory_analysis")
    assert changed is True
    assert not (store.override_dir / "memory_analysis.md").exists()
    # 已备份
    backups = list(store.history_dir.glob("memory_analysis-*.md"))
    assert len(backups) >= 1


def test_reset_no_override_is_noop(store: PromptStore) -> None:
    assert store.reset("memory_analysis") is False


# ─── 备份轮转 ────────────────────────────────────────────


def test_backups_rotate_to_keep_limit(store: PromptStore) -> None:
    body = _MA_ALL_PH
    # save 一次不产生备份 (首次无旧覆盖)
    store.save("memory_analysis", body + " v0")
    # 再 save BACKUP_KEEP + 3 次 → 每次都备份旧的
    for i in range(BACKUP_KEEP + 3):
        store.save("memory_analysis", body + f" v{i+1}")
        # 保证 mtime 严格递增, 避免同秒 tie 导致排序不稳定
        time.sleep(0.01)

    backups = list(store.history_dir.glob("memory_analysis-*.md"))
    assert len(backups) == BACKUP_KEEP


def test_same_second_backups_do_not_collide(store: PromptStore) -> None:
    body = _MA_ALL_PH
    store.save("memory_analysis", body + " v0")
    # 快速连续 save (可能同秒)
    for i in range(3):
        store.save("memory_analysis", body + f" v{i+1}")

    backups = list(store.history_dir.glob("memory_analysis-*.md"))
    # 应有 3 份备份且文件名互不相同
    assert len(backups) == 3
    assert len({b.name for b in backups}) == 3


# ─── list / get_info / list_history ─────────────────────


def test_list_returns_all_registry_entries(store: PromptStore) -> None:
    infos = store.list()
    assert {info.name for info in infos} == set(PROMPT_REGISTRY)
    # 默认无覆盖
    assert all(not info.overridden for info in infos)


def test_get_info_reports_version_from_frontmatter(store: PromptStore) -> None:
    body = "---\nversion: 7\n---\n" + _MA_ALL_PH
    store.save("memory_analysis", body)
    info = store.get_info("memory_analysis")
    assert info.overridden is True
    assert info.version == 7


def test_list_history_returns_backups(store: PromptStore) -> None:
    body = _MA_ALL_PH
    store.save("memory_analysis", body + " v0")
    time.sleep(0.01)
    store.save("memory_analysis", body + " v1")
    time.sleep(0.01)
    store.save("memory_analysis", body + " v2")

    history = store.list_history("memory_analysis")
    assert len(history) == 2  # v0 和 v1 被备份 (v2 是当前)
    # mtime 倒序
    assert history[0]["mtime"] >= history[1]["mtime"]
