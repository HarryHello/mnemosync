"""管理 API 路由 — Agent 运行记录.

GET /admin/agent-runs          — 列表 (分页, 按 agent_name/status 过滤)
GET /admin/agent-runs/{run_id} — 单条详情 (含 tool_trace)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import get_agent_run_store
from src.persistence.agent_run_store import AgentRunRecord, AgentRunStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Runs"])


# ── Response schemas ────────────────────────────────────────────────


class AgentRunSummary(BaseModel):
    run_id: str
    parent_request_id: str | None
    agent_name: str
    started_at: str
    finished_at: str | None
    status: str
    duration_ms: float | None = None
    error: str | None


class AgentRunDetail(BaseModel):
    run_id: str
    parent_request_id: str | None
    agent_name: str
    input_event_ids: list[str]
    base_version: str | None
    started_at: str
    finished_at: str | None
    status: str
    tool_trace: list[dict[str, Any]]
    usage: dict[str, Any]
    structured_result: Any | None
    error: str | None
    duration_ms: float | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunSummary]
    total: int
    page: int
    page_size: int


# ── Helpers ─────────────────────────────────────────────────────────


def _duration_ms(record: AgentRunRecord) -> float | None:
    if record.finished_at and record.started_at:
        return round(
            (record.finished_at - record.started_at).total_seconds() * 1000, 1
        )
    return None


def _to_summary(record: AgentRunRecord) -> AgentRunSummary:
    return AgentRunSummary(
        run_id=record.run_id,
        parent_request_id=record.parent_request_id,
        agent_name=record.agent_name,
        started_at=record.started_at.isoformat() if record.started_at else "",
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        status=record.status,
        duration_ms=_duration_ms(record),
        error=record.error,
    )


def _to_detail(record: AgentRunRecord) -> AgentRunDetail:
    return AgentRunDetail(
        run_id=record.run_id,
        parent_request_id=record.parent_request_id,
        agent_name=record.agent_name,
        input_event_ids=record.input_event_ids,
        base_version=record.base_version,
        started_at=record.started_at.isoformat() if record.started_at else "",
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        status=record.status,
        tool_trace=record.tool_trace,
        usage=record.usage,
        structured_result=record.structured_result,
        error=record.error,
        duration_ms=_duration_ms(record),
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/agent-runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    agent_name: str | None = None,
    status: str | None = None,
    store: AgentRunStore = Depends(get_agent_run_store),
) -> AgentRunListResponse:
    """列出最近的 Agent 运行记录."""
    total = await store.count(agent_name=agent_name, status=status)
    records = await store.list_recent(
        limit=page_size,
        offset=(page - 1) * page_size,
        agent_name=agent_name,
        status=status,
    )
    return AgentRunListResponse(
        items=[_to_summary(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/agent-runs/{run_id}", response_model=AgentRunDetail)
async def get_agent_run(
    run_id: str,
    store: AgentRunStore = Depends(get_agent_run_store),
) -> AgentRunDetail:
    """获取单条 Agent 运行记录详情."""
    record = await store.get_by_id(run_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Agent run {run_id} not found")
    return _to_detail(record)
