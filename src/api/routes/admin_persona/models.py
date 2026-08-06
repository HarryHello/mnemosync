"""admin_persona 包共享的 Pydantic 模型.

将原 admin_persona.py 中分散在各节的请求/响应模型集中于此, 供各子模块复用.
"""

from pydantic import BaseModel


class PersonaIdentityBody(BaseModel):
    """结构化人格身份 (v0.4.0: 移除了 per-user 的 user_addressing/context)."""

    personality: str = ""
    speaking_style: str = ""
    values: list[str] = []
    persona_addressing: str = "人格"


class PersonaOverrideBody(BaseModel):
    """单空间覆盖 (v0.4.0: 移除了 per-user 的 context)."""

    speaking_style: str | None = None
    personality: str | None = None
    scenario: str | None = None


class PersonaDefinitionSaveBody(BaseModel):
    """保存结构化人格 (v0.4.0: 增加 name 字段支持改名)."""

    name: str
    identity: PersonaIdentityBody
    space_overrides: dict[str, PersonaOverrideBody] = {}
    changelog: str = ""


class PersonaDefinitionRead(BaseModel):
    """结构化人格读取."""

    version: str
    name: str
    identity: PersonaIdentityBody
    space_overrides: dict[str, PersonaOverrideBody]
    created_at: str
    updated_at: str


class PersonaVersionItem(BaseModel):
    """人格版本摘要."""

    id: int
    version: str
    name: str
    changelog: str | None
    author: str | None
    created_at: str
    active: bool


class PersonaVersionListResponse(BaseModel):
    items: list[PersonaVersionItem]
    total: int


class PersonaProfileCreateBody(BaseModel):
    name: str
    description: str = ""


class PersonaProfileUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None


class PersonaProfileRead(BaseModel):
    id: str
    name: str
    description: str
    is_active: bool
    created_at: str
    updated_at: str


class PersonaProfileListResponse(BaseModel):
    items: list[PersonaProfileRead]
    total: int


class CharacterCardPreview(BaseModel):
    """角色卡预览."""

    name: str
    source_format: str
    identity: PersonaIdentityBody
    has_lorebook: bool = False
    has_examples: bool = False


class SpacePolicyBody(BaseModel):
    """空间社交策略."""

    expressor_enabled: bool = True
    expressor_temperature: float = 0.4
    preferred_max_length: int | None = 200
    use_emojis: bool | None = True


class SpacePolicyRead(BaseModel):
    space_id: str
    config: SpacePolicyBody
    updated_at: str
