"""提示词存储 (两层: default + override).

设计要点:
- **无缓存**: 每次 load() 读盘. 文件 <10KB, IO 可忽略; 换取"CLI 改文件立即生效".
- **白名单**: 所有 name 都必须在 PROMPT_REGISTRY 中, 防路径穿越.
- **备份**: save() 覆盖旧 override 前先备份到 .history/, 保留最近 10 份.
- **失败模式**:
    - save 阶段占位符校验失败 → 抛异常, 拒绝写盘
    - load 阶段 override 文件不存在 → 静默回退默认
    - load 阶段 override 文件 YAML frontmatter 解析失败 → warn + 回退默认
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List

from .registry import PROMPT_REGISTRY, PromptSpec

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
BACKUP_KEEP = 10


@dataclass
class PromptInfo:
    """列表条目."""

    name: str
    description: str
    placeholders: tuple[str, ...]
    overridden: bool
    version: int = 0


@dataclass
class ValidationResult:
    """校验结果. `ok=False` 时至少填 error 或 missing_placeholders 之一."""

    ok: bool
    missing_placeholders: list[str] = field(default_factory=list)
    error: str | None = None


class PromptStore:
    """两层提示词存储, 供 CLI + 面板 API 共享."""

    def __init__(self, override_dir: Path, default_dir: Path) -> None:
        self.override_dir = Path(override_dir)
        self.default_dir = Path(default_dir)
        self.history_dir = self.override_dir / ".history"

    # ── 路径与 registry 校验 ──────────────────────────────────

    def _spec(self, name: str) -> PromptSpec:
        spec = PROMPT_REGISTRY.get(name)
        if spec is None:
            raise KeyError(f"未知的提示词名称: {name!r}")
        return spec

    def _default_path(self, name: str) -> Path:
        self._spec(name)
        return self.default_dir / f"{name}.md"

    def _override_path(self, name: str) -> Path:
        self._spec(name)
        return self.override_dir / f"{name}.md"

    # ── 读取 ──────────────────────────────────────────────────

    @staticmethod
    def _strip_frontmatter(text: str) -> tuple[str, dict[str, Any]]:
        """去掉 YAML frontmatter, 返回 (body, meta).

        frontmatter 解析失败 → warn + 返回 ({}, 原文).
        """
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return text, {}
        raw = m.group(1)
        body = text[m.end():]
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("PyYAML 未安装, 忽略 frontmatter")
            return body, {}
        try:
            meta = yaml.safe_load(raw) or {}
            if not isinstance(meta, dict):
                logger.warning("frontmatter 不是 mapping: %r", meta)
                return body, {}
            return body, meta
        except Exception as e:
            logger.warning("frontmatter 解析失败, 忽略: %s", e)
            return body, {}

    def _read_raw(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def load_default(self, name: str) -> str:
        """加载默认层 (剥离 frontmatter)."""
        path = self._default_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"默认提示词缺失: {path}")
        body, _ = self._strip_frontmatter(self._read_raw(path))
        return body

    def load(self, name: str) -> str:
        """override > default. 剥离 frontmatter. 无缓存."""
        self._spec(name)
        override = self._override_path(name)
        if override.is_file():
            try:
                body, _ = self._strip_frontmatter(self._read_raw(override))
                return body
            except Exception as e:
                logger.warning("读取覆盖文件失败, 回退默认: %s (%s)", override, e)
        return self.load_default(name)

    def load_raw(self, name: str, *, default: bool = False) -> str:
        """加载原始文本 (**不**剥离 frontmatter). 供 CLI show / 面板 edit 用."""
        if default:
            path = self._default_path(name)
        else:
            path = self._override_path(name)
            if not path.is_file():
                path = self._default_path(name)
        return self._read_raw(path)

    # ── 校验 ──────────────────────────────────────────────────

    def validate(self, name: str, content: str) -> ValidationResult:
        """占位符齐全性检查. registry 是权威."""
        spec = self._spec(name)
        body, _ = self._strip_frontmatter(content)
        missing = [p for p in spec.placeholders if f"__{p}__" not in body]
        if missing:
            return ValidationResult(
                ok=False,
                missing_placeholders=missing,
                error=f"缺失占位符: {', '.join('__' + p + '__' for p in missing)}",
            )
        return ValidationResult(ok=True)

    # ── 写入与备份 ────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        self.override_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _backup(self, name: str) -> Path | None:
        """将当前 override 备份到 .history/. 无覆盖时返回 None."""
        current = self._override_path(name)
        if not current.is_file():
            return None
        self._ensure_dirs()
        # 生成不冲突的时间戳文件名 (同秒计数器)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        for i in range(1, 1000):
            dst = self.history_dir / f"{name}-{ts}-{i:03d}.md"
            if not dst.exists():
                break
        else:
            raise RuntimeError("备份文件名冲突过多")
        dst.write_bytes(current.read_bytes())
        self._rotate_backups(name)
        return dst

    def _rotate_backups(self, name: str) -> None:
        """按 mtime 保留最新 BACKUP_KEEP 份, 其余删除."""
        if not self.history_dir.is_dir():
            return
        backups = sorted(
            self.history_dir.glob(f"{name}-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[BACKUP_KEEP:]:
            try:
                old.unlink()
            except OSError as e:
                logger.warning("删除旧备份失败: %s (%s)", old, e)

    def save(self, name: str, content: str) -> None:
        """校验 → 备份旧 override → 写新 override."""
        result = self.validate(name, content)
        if not result.ok:
            raise ValueError(result.error or "校验失败")
        self._ensure_dirs()
        self._backup(name)
        self._override_path(name).write_text(content, encoding="utf-8")

    def reset(self, name: str) -> bool:
        """删除 override (备份最后一版), 回到默认.

        Returns:
            True 如果确实删除了覆盖; False 如果本来就没有覆盖.
        """
        self._spec(name)
        override = self._override_path(name)
        if not override.is_file():
            return False
        self._backup(name)
        override.unlink()
        return True

    # ── 列表与元数据 ──────────────────────────────────────────

    def list(self) -> list[PromptInfo]:
        infos: list[PromptInfo] = []
        for name, spec in PROMPT_REGISTRY.items():
            override_path = self._override_path(name)
            overridden = override_path.is_file()
            version = 0
            if overridden:
                try:
                    _, meta = self._strip_frontmatter(self._read_raw(override_path))
                    version = int(meta.get("version", 0) or 0)
                except Exception:
                    version = 0
            infos.append(PromptInfo(
                name=name,
                description=spec.description,
                placeholders=spec.placeholders,
                overridden=overridden,
                version=version,
            ))
        return infos

    def get_info(self, name: str) -> PromptInfo:
        spec = self._spec(name)
        override_path = self._override_path(name)
        overridden = override_path.is_file()
        version = 0
        if overridden:
            try:
                _, meta = self._strip_frontmatter(self._read_raw(override_path))
                version = int(meta.get("version", 0) or 0)
            except Exception:
                version = 0
        return PromptInfo(
            name=name,
            description=spec.description,
            placeholders=spec.placeholders,
            overridden=overridden,
            version=version,
        )

    def list_history(self, name: str) -> List[dict[str, Any]]:
        """.history/ 下针对某 name 的备份列表 (按 mtime 倒序)."""
        self._spec(name)
        if not self.history_dir.is_dir():
            return []
        backups = sorted(
            self.history_dir.glob(f"{name}-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "filename": p.name,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                "size": p.stat().st_size,
            }
            for p in backups
        ]


# ── 全局单例 ──────────────────────────────────────────────────

_store: PromptStore | None = None


def get_prompt_store() -> PromptStore:
    """获取全局 PromptStore 单例 (惰性初始化, 从 settings.storage 派生)."""
    global _store
    if _store is None:
        from src.core.config import PROJECT_ROOT, get_settings

        settings = get_settings()
        default_dir = PROJECT_ROOT / "src" / "core" / "agents" / "prompts" / "defaults"
        override_dir = settings.storage.prompts_override_dir_abs
        _store = PromptStore(override_dir=override_dir, default_dir=default_dir)
    return _store


def _reset_prompt_store() -> None:
    """测试用: 清空单例."""
    global _store
    _store = None


__all__ = [
    "PromptStore",
    "PromptInfo",
    "ValidationResult",
    "get_prompt_store",
    "_reset_prompt_store",
    "BACKUP_KEEP",
]
