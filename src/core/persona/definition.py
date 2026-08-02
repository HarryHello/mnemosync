"""结构化人格定义数据模型.

PersonaDefinition 将单段人格 prompt 演进为结构化格式, 各字段独立可管理。
支持按空间覆盖表达倾向 (不同群聊用不同说话风格)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class PersonaIdentity:
    """人格身份: 谁在说话.

    Note:
        user_addressing 和 context (关系背景) 是 per-user 级别的字段,
        由 ``Relationship`` 模型维护, 不在人格级定义中存储.
        参见 ``src.core.memory.models.Relationship``.
    """

    personality: str = ""               # 性格描述
    speaking_style: str = ""            # 说话风格
    values: list[str] = field(default_factory=list)  # 核心价值观
    persona_addressing: str = "人格"     # 人格自称


@dataclass
class PersonaOverride:
    """单个空间的覆盖配置. 非空字段覆盖默认值.

    Note:
        context (关系背景) 已移除 — 它是 per-user 级别的字段,
        由 ``Relationship`` 模型维护, 不适合作为空间覆盖.
    """

    speaking_style: str | None = None
    personality: str | None = None
    scenario: str | None = None


@dataclass
class PersonaDefinition:
    """结构化人格定义."""

    version: str
    name: str
    identity: PersonaIdentity
    space_overrides: dict[str, PersonaOverride] = field(default_factory=dict)
    author: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def get_identity_for_space(self, space_id: str | None) -> PersonaIdentity:
        """获取指定空间的有效身份 (应用覆盖后).

        Note:
            只有 ``personality``, ``speaking_style`` 支持空间覆盖;
            ``persona_addressing`` 为人格级不可覆盖.
            ``user_addressing`` / ``context`` (关系背景) 是 per-user 字段,
            由 ``Relationship`` 模型维护, 不在人格级定义中.
        """
        if not space_id:
            return self.identity
        override = self.space_overrides.get(space_id)
        if override is None:
            return self.identity

        return PersonaIdentity(
            personality=override.personality if override.personality is not None else self.identity.personality,
            speaking_style=override.speaking_style if override.speaking_style is not None else self.identity.speaking_style,
            values=list(self.identity.values),
            persona_addressing=self.identity.persona_addressing,
        )

    @staticmethod
    def from_legacy(name: str, prompt: str, persona_addressing: str = "人格") -> PersonaDefinition:
        """从 v0.3.x 单段 prompt 创建结构化定义.

        Note:
            ``user_addressing`` 和 ``context`` 已被移除 — 它们是 per-user 级别的字段,
            由 ``Relationship`` 模型维护. 遗留迁移时不传入 per-user 信息.
        """
        return PersonaDefinition(
            version="0.0.0",  # 标记为遗留迁移
            name=name,
            identity=PersonaIdentity(
                personality=prompt,
                speaking_style="",
                persona_addressing=persona_addressing,
            ),
        )

    def to_legacy_prompt(self, identity: PersonaIdentity | None = None) -> str:
        """构建单段 prompt 文本 (供 __PERSONA_SECTION__ 注入).

        只包含人格级字段: 人格设定、说话风格、核心价值.
        关系背景 (context) 是 per-user 级别的, 不由这里注入.

        Args:
            identity: 可选的覆盖身份 (含空间覆盖). 为 None 时使用 self.identity.
        """
        ident = identity or self.identity
        parts: list[str] = []
        if ident.personality:
            parts.append(f"## 人格设定\n{ident.personality}")
        if ident.speaking_style:
            parts.append(f"## 说话风格\n{ident.speaking_style}")
        if ident.values:
            vals = "\n".join(f"- {v}" for v in ident.values)
            parts.append(f"## 核心价值\n{vals}")
        return "\n\n".join(parts) if parts else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "identity": {
                "personality": self.identity.personality,
                "speaking_style": self.identity.speaking_style,
                "values": self.identity.values,
                "persona_addressing": self.identity.persona_addressing,
            },
            "space_overrides": {
                sid: {
                    k: v for k, v in {
                        "speaking_style": ov.speaking_style,
                        "personality": ov.personality,
                        "scenario": ov.scenario,
                    }.items() if v is not None
                }
                for sid, ov in self.space_overrides.items()
            },
            "author": self.author,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PersonaDefinition:
        identity_data = d.get("identity", {})
        overrides_data = d.get("space_overrides", {}) or {}
        return PersonaDefinition(
            version=d.get("version", "0.0.0"),
            name=d.get("name", ""),
            identity=PersonaIdentity(
                personality=identity_data.get("personality", ""),
                speaking_style=identity_data.get("speaking_style", ""),
                values=identity_data.get("values", []),
                persona_addressing=identity_data.get("persona_addressing", "人格"),
            ),
            space_overrides={
                sid: PersonaOverride(
                    speaking_style=ov.get("speaking_style"),
                    personality=ov.get("personality"),
                    scenario=ov.get("scenario"),
                )
                for sid, ov in overrides_data.items()
            },
            author=d.get("author"),
            created_at=datetime.fromisoformat(d["created_at"]) if "created_at" in d else datetime.now(UTC),
            updated_at=datetime.fromisoformat(d["updated_at"]) if "updated_at" in d else datetime.now(UTC),
        )
