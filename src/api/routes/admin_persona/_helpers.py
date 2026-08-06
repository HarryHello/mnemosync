"""admin_persona 包共享的辅助函数."""

from fastapi import Request

from src.api.deps import get_persona_store
from src.api.schemas.admin import PersonaConfigRead, PersonaConfigRelation
from src.core.config import (
    _load_persona_override,
    get_settings,
)
from src.persistence.persona_store import SqlitePersonaStore


def _build_persona_read() -> PersonaConfigRead:
    """从多层合并后的 settings 构建响应."""
    s = get_settings()
    rel = s.persona.relation
    override_exists = _load_persona_override() is not None
    return PersonaConfigRead(
        name=s.persona.name,
        prompt=s.persona.prompt,
        relation=PersonaConfigRelation(
            persona_addressing=rel.persona_addressing,
            user_addressing=rel.user_addressing,
            context=rel.context,
        ),
        overridden=override_exists,
    )


def _get_persona_store(request: Request) -> SqlitePersonaStore:
    return get_persona_store(request)
