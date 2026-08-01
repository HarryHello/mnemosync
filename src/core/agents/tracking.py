"""Agent 运行追踪: 超时、运行记录、调试事件."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable
from typing import Any, TypeVar

from src.core.agents.spec import get_spec
from src.infra.debug_context import emit_pipeline

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def run_agent_tracked(
    spec_name: str,
    coro: Awaitable[T],
    *,
    store: Any | None = None,
    debug_bus: Any | None = None,
    parent_request_id: str | None = None,
    input_event_ids: list[str] | None = None,
    base_version: str | None = None,
) -> T:
    """包装 Agent 协程: 加载 spec → 创建记录 → 超时保护 → 更新记录 → 调试事件.

    用法::

        result = await run_agent_tracked(
            "memory_analysis",
            run_memory_analysis(forwarder=..., ...),
            store=agent_run_store,
            debug_bus=debug_bus,
        )

    协程传入时未 await, 由本函数施加 asyncio.wait_for 超时.
    """
    spec = get_spec(spec_name)
    run_id = uuid.uuid4().hex[:16]
    started_at = time.time()

    # emit start
    emit_pipeline(
        debug_bus,
        event_kind="agent_run_start",
        run_id=run_id,
        agent=spec.name,
        timeout=spec.timeout_seconds,
    )

    # 写入 DB (best-effort)
    if store is not None:
        try:
            await store.create_run(
                run_id,
                parent_request_id,
                spec.name,
                input_event_ids=input_event_ids,
                base_version=base_version,
            )
        except Exception as exc:
            logger.warning("Failed to create agent run record: %s", exc)

    status = "ok"
    result = None
    error_msg: str | None = None

    try:
        result = await asyncio.wait_for(coro, timeout=spec.timeout_seconds)
    except asyncio.TimeoutError:
        status = "timeout"
        error_msg = f"Agent {spec.name} timed out after {spec.timeout_seconds}s"
        logger.warning(error_msg)
    except asyncio.CancelledError:
        status = "cancelled"
        error_msg = f"Agent {spec.name} was cancelled"
        logger.warning(error_msg)
        raise
    except Exception as exc:
        status = "failed"
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("Agent %s failed: %s", spec.name, error_msg)
        raise
    finally:
        duration_ms = (time.time() - started_at) * 1000

        if store is not None:
            try:
                await store.finish_run(
                    run_id,
                    status=status,
                    error=error_msg,
                )
            except Exception as exc:
                logger.warning("Failed to finish agent run record: %s", exc)

        emit_pipeline(
            debug_bus,
            event_kind="agent_run_end",
            run_id=run_id,
            agent=spec.name,
            status=status,
            duration_ms=round(duration_ms, 1),
            error=error_msg,
        )

    return result
