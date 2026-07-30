"""身份解析模块 (v0.3.0)."""
from .models import (
    Actor,
    ActorGroupMembership,
    IdentityContext,
    IdentityStrategy,
    StrategyType,
    UserGroup,
)
from .resolver import IdentityResolver

__all__ = [
    "Actor", "ActorGroupMembership", "IdentityContext", "IdentityStrategy",
    "StrategyType", "UserGroup", "IdentityResolver",
]
