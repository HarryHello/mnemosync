"""Parse request node: message extraction + user identity resolution."""

from __future__ import annotations

from typing import Any

from src.core.graph.state import AgentState


async def parse_request_node(state: AgentState) -> dict[str, Any]:
    """Message extraction + user identity resolution."""
    messages = state.get("messages", [])
    source_user = state.get("source_user") or ""

    extracted = state.get("extracted_new")
    if extracted is None:
        extracted = [m for m in messages if m.get("role") == "user"]

    return {"extracted_new": extracted, "source_user": source_user}
