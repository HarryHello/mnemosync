"""角色 → 模型候选解析."""

from .resolver import NoCandidateForRoleError, RoleResolver

__all__ = ["RoleResolver", "NoCandidateForRoleError"]
