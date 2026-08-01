"""工具: 更新关系称呼 / 关系背景 (v0.2.10).

关系分析 Agent 通过 function_call 调用. 当且仅当用户消息本身给出可信信号时,
Agent 决定调用此工具把 (persona_addressing / user_addressing / context) 写回
relationships 表. 判断维度 (是否玩笑/是否场景扮演/是否引用/是否撤回) 由 prompt
指导, 代码层只做兜底: reason 非空 + persona_id/user_id 通过闭包 bind (Agent 看不见)
+ 三字段全 None 拒绝.

审计日志 (relationship_audit_log) 由 SqliteRelationshipStore.update_relationship_addressing
统一写入.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.persistence.memory_store import SqliteRelationshipStore

_MIN_REASON_LEN = 10


def make_update_addressing_tool(
    relationship_store: SqliteRelationshipStore,
    persona_id: str,
    user_id: str,
    actor_id: str | None = None,
):
    """构建绑定 (persona_id, user_id) 的 update_addressing tool.

    persona_id / user_id 在闭包中固化, Agent 无法跨用户 / 跨人格写。
    user_id 是 effective_user_id — 群成员的多个 Actor 共享同一份关系
    (称呼属于"这个人", 不属于某个平台账号)。

    actor_id (v0.3.0): 触发本次更新的 Actor, 仅用于溯源 (返回给调用方 +
    日志), 不影响写入目标。
    """

    @tool
    async def update_addressing(
        persona_addressing: str | None = None,
        user_addressing: str | None = None,
        context: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """更新关系称呼或关系背景, 并写入审计日志.

        **只在当前用户消息**给出清晰、可信的信号时调用. 判断维度:
        - 是显式请求还是玩笑 / 场景扮演 / 引用他人 / 情绪化抱怨?
        - 是要求关系或称呼改变, 还是短期情境需要?
        - 有没有撤回或矛盾信号?

        Args:
            persona_addressing: 人格如何自称 (如 "我" / "人家"), None = 不改
            user_addressing: 人格如何称呼用户 (如 "哥哥" / "小哥"), None = 不改
            context: 关系背景 (如 "同住的兄妹" / "恋人"), None = 不改
            reason: 触发依据 (至少 10 字), 必填. 应包含原文片段或概述.

        Returns:
            {updated_fields, prev, current, audit_ids}
            - updated_fields: 实际写入的字段列表 (相同值会被跳过)
            - prev / current: 三字段的旧/新值 (None 表示尚未覆盖, 沿用 TOML 基线)
            - audit_ids: 本次写入的审计记录 ID 列表
        """
        r = (reason or "").strip()
        if len(r) < _MIN_REASON_LEN:
            raise ValueError(
                f"reason 至少 {_MIN_REASON_LEN} 字, 需说明触发依据 (当前 {len(r)} 字)"
            )
        if persona_addressing is None and user_addressing is None and context is None:
            raise ValueError("至少需要传入一个字段 (persona_addressing / user_addressing / context)")

        prev_rel = await relationship_store.get_relationship(persona_id, user_id)
        prev = {
            "persona_addressing": prev_rel.persona_addressing if prev_rel else None,
            "user_addressing": prev_rel.user_addressing if prev_rel else None,
            "context": prev_rel.context if prev_rel else None,
        }
        entries = await relationship_store.update_relationship_addressing(
            persona_id, user_id,
            persona_addressing=persona_addressing,
            user_addressing=user_addressing,
            context=context,
            source="agent",
            reason=r,
        )
        new_rel = await relationship_store.get_relationship(persona_id, user_id)
        current = {
            "persona_addressing": new_rel.persona_addressing if new_rel else None,
            "user_addressing": new_rel.user_addressing if new_rel else None,
            "context": new_rel.context if new_rel else None,
        }
        return {
            "updated_fields": [e.field_name for e in entries],
            "prev": prev,
            "current": current,
            "audit_ids": [e.id for e in entries],
            "actor_id": actor_id,  # v0.3.0: 溯源用, 哪个 Actor 触发的更新
        }

    return update_addressing
