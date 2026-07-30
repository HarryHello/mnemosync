"""调试事件总线.

给"调试面板"页面用. 每次 HTTP 请求 (前台→Mnemosync / Mnemosync→上游) 都
往这里 emit 一条事件, SSE 端点把事件推给前端。

设计:
  * 内存 ring buffer (默认 500 条) — 崩溃即丢, 反正是调试临时状态
  * 惰性触发: 只有当有活跃 SSE 订阅者时才 emit (subscribers==0 时是空操作)
  * 订阅者计数从 1 到 0 时启动 grace timer (默认 30s), 到期后触发清理回调
    (清理 panel-debug 来源的 API key)
  * 单例通过 app.state.debug_bus 暴露
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 单条事件 body 预览长度 (前端列表卡片展示); 超过让前端按 id 拉全量
BODY_PREVIEW_MAX = 6 * 1024
# 存储 ring buffer 里事件全量 body 上限 (超过截断, 避免大流吞内存)
BODY_FULL_MAX = 512 * 1024


@dataclass
class DebugEvent:
    """一条 HTTP hop 事件.

    direction:
      * inbound_request  — 客户端 → Mnemosync (含请求体 + 请求头)
      * inbound_response — Mnemosync → 客户端 (含响应体 + 状态码)
      * upstream_request — Mnemosync → 上游服务商 (含 agent 名 + payload)
      * upstream_response — 上游 → Mnemosync (含响应 / 错误)
      * upstream_stream_chunk — 上游流式返回单帧 (assembled 到卡片 stream_chunks)
    """

    id: str
    correlation_id: str
    ts: float
    direction: str
    method: str | None
    url: str
    port: int | None
    agent: str | None
    status: int | None
    duration_ms: float | None
    key_note: str | None  # inbound 请求的 API Key note (只在 inbound 有意义)
    headers: dict[str, str] | None
    body_preview: Any | None  # 预览 (dict / str / None); 完整由 id 拉
    body_full_size: int  # 全量字节数
    is_truncated: bool

    def to_summary(self) -> dict[str, Any]:
        """列表用的轻量摘要 (不含 body_full)."""
        d = asdict(self)
        return d


@dataclass
class _StoredEvent:
    summary: DebugEvent
    body_full: bytes | str | dict | list | None
    # 流式帧 assemble 结果 (只在 stream 起始事件保存)
    stream_chunks: list[bytes] = field(default_factory=list)
    stream_assembled: str | None = None
    stream_finished: bool = False


class DebugEventBus:
    """全局单例事件总线."""

    def __init__(self, capacity: int = 500, grace_seconds: float = 30.0):
        self._capacity = capacity
        self._grace_seconds = grace_seconds
        self._buffer: deque[_StoredEvent] = deque(maxlen=capacity)
        self._by_id: dict[str, _StoredEvent] = {}
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._grace_task: asyncio.Task | None = None
        self._on_grace_expired: Optional[Callable[[], Awaitable[None]]] = None
        self._lock = asyncio.Lock()

    def set_grace_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """注册"订阅数掉到 0 且 grace 超时"时的清理回调."""
        self._on_grace_expired = callback

    # ============ 订阅生命周期 ============

    async def subscribe(self) -> tuple[str, asyncio.Queue]:
        """新订阅者. 返回 (id, queue). 会取消 pending grace timer."""
        sub_id = uuid.uuid4().hex[:12]
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[sub_id] = q
            if self._grace_task and not self._grace_task.done():
                self._grace_task.cancel()
                self._grace_task = None
                logger.info("Debug SSE: 订阅恢复 → 取消 grace timer")
        logger.info("Debug SSE 订阅+1 (总数=%d)", len(self._subscribers))
        return sub_id, q

    async def unsubscribe(self, sub_id: str) -> None:
        """订阅结束. 掉到 0 时启动 grace timer."""
        async with self._lock:
            self._subscribers.pop(sub_id, None)
            count = len(self._subscribers)
            if count == 0 and self._grace_task is None:
                self._grace_task = asyncio.create_task(self._run_grace_timer())
        logger.info("Debug SSE 订阅-1 (总数=%d)", count)

    async def _run_grace_timer(self) -> None:
        try:
            await asyncio.sleep(self._grace_seconds)
            logger.info(
                "Debug SSE: grace %.0fs 到期 (仍 0 订阅), 触发清理", self._grace_seconds
            )
            if self._on_grace_expired:
                try:
                    await self._on_grace_expired()
                except Exception as e:
                    logger.warning("Debug grace cleanup 回调失败: %s", e)
            async with self._lock:
                self._grace_task = None
        except asyncio.CancelledError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def should_emit(self) -> bool:
        """惰性 emit 门: 只有活跃订阅时才 emit."""
        return len(self._subscribers) > 0

    # ============ Emit ============

    def emit(
        self,
        *,
        direction: str,
        correlation_id: str,
        url: str,
        method: str | None = None,
        port: int | None = None,
        agent: str | None = None,
        status: int | None = None,
        duration_ms: float | None = None,
        key_note: str | None = None,
        headers: dict[str, str] | None = None,
        body: Any = None,
    ) -> str | None:
        """同步 emit. 无订阅者时静默返回 None."""
        if not self.should_emit():
            return None
        event_id = uuid.uuid4().hex[:16]

        # 预览 & 大小估算
        preview, full_size, truncated = _shape_body(body)

        summary = DebugEvent(
            id=event_id,
            correlation_id=correlation_id,
            ts=time.time(),
            direction=direction,
            method=method,
            url=url,
            port=port,
            agent=agent,
            status=status,
            duration_ms=duration_ms,
            key_note=key_note,
            headers=_redact_headers(headers) if headers else None,
            body_preview=preview,
            body_full_size=full_size,
            is_truncated=truncated,
        )
        stored = _StoredEvent(summary=summary, body_full=body)
        self._buffer.append(stored)
        self._by_id[event_id] = stored

        # 清理超出容量的 by_id 条目
        if len(self._by_id) > self._capacity * 2:
            keep_ids = {e.summary.id for e in self._buffer}
            self._by_id = {k: v for k, v in self._by_id.items() if k in keep_ids}

        # 分发给订阅者
        for q in list(self._subscribers.values()):
            try:
                q.put_nowait(summary)
            except asyncio.QueueFull:
                # 慢消费者丢帧不阻塞发送方
                pass

        return event_id

    def append_stream_chunk(self, event_id: str, chunk: bytes) -> None:
        """流式响应的每一帧, 追加到对应事件. 装配后仅装配结果推送新事件."""
        stored = self._by_id.get(event_id)
        if not stored:
            return
        stored.stream_chunks.append(chunk)

    def finalize_stream(
        self,
        event_id: str,
        assembled: str,
        status: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        stored = self._by_id.get(event_id)
        if not stored:
            return
        stored.stream_assembled = assembled
        stored.stream_finished = True
        if status is not None:
            stored.summary.status = status
        if duration_ms is not None:
            stored.summary.duration_ms = duration_ms
        # 发一条 assembled 摘要给订阅者
        finish_summary = DebugEvent(
            id=event_id,
            correlation_id=stored.summary.correlation_id,
            ts=time.time(),
            direction=stored.summary.direction + "_final",
            method=stored.summary.method,
            url=stored.summary.url,
            port=stored.summary.port,
            agent=stored.summary.agent,
            status=stored.summary.status,
            duration_ms=stored.summary.duration_ms,
            key_note=stored.summary.key_note,
            headers=None,
            body_preview=assembled[:BODY_PREVIEW_MAX],
            body_full_size=len(assembled),
            is_truncated=len(assembled) > BODY_PREVIEW_MAX,
        )
        for q in list(self._subscribers.values()):
            try:
                q.put_nowait(finish_summary)
            except asyncio.QueueFull:
                pass

    # ============ Pipeline Events ============

    def emit_pipeline(
        self,
        *,
        correlation_id: str,
        event_kind: str,
        data: dict[str, Any],
    ) -> str | None:
        """发射语义管线事件 (非 HTTP hop).

        与 emit() 不同, 这类事件描述管线决策 (工具策略命中、事务提取、
        Expressor 改写等), 不是上游 HTTP 请求/响应.

        Args:
            correlation_id: 关联到入站请求的 cid
            event_kind: 事件类型 (tool_policy / tool_transaction / trigger_reason
                        / tool_call_decision / expressor_rewrite / cooldown_blocked)
            data: 结构化事件数据
        """
        if not self.should_emit():
            return None
        event_id = uuid.uuid4().hex[:16]
        body = {"event_kind": event_kind, **data}
        preview, full_size, truncated = _shape_body(body)
        summary = DebugEvent(
            id=event_id,
            correlation_id=correlation_id,
            ts=time.time(),
            direction="pipeline",
            method=None,
            url=f"pipeline:{event_kind}",
            port=None,
            agent=None,
            status=None,
            duration_ms=None,
            key_note=None,
            headers=None,
            body_preview=preview,
            body_full_size=full_size,
            is_truncated=truncated,
        )
        stored = _StoredEvent(summary=summary, body_full=body)
        self._buffer.append(stored)
        self._by_id[event_id] = stored
        if len(self._by_id) > self._capacity * 2:
            keep_ids = {e.summary.id for e in self._buffer}
            self._by_id = {k: v for k, v in self._by_id.items() if k in keep_ids}
        for q in list(self._subscribers.values()):
            try:
                q.put_nowait(summary)
            except asyncio.QueueFull:
                pass
        return event_id

    # ============ Read ============

    def list_recent(self, limit: int = 100) -> list[DebugEvent]:
        items = list(self._buffer)[-limit:]
        return [e.summary for e in items]

    def get_full(self, event_id: str) -> dict[str, Any] | None:
        stored = self._by_id.get(event_id)
        if not stored:
            return None
        return {
            "summary": stored.summary.to_summary(),
            "body_full": _normalize_body(stored.body_full),
            "stream_assembled": stored.stream_assembled,
            "stream_chunks_count": len(stored.stream_chunks),
        }

    def clear(self) -> None:
        self._buffer.clear()
        self._by_id.clear()


def _shape_body(body: Any) -> tuple[Any, int, bool]:
    """返回 (预览, 全量字节数, 是否截断)."""
    if body is None:
        return None, 0, False
    if isinstance(body, (dict, list)):
        import json as _json

        try:
            full = _json.dumps(body, ensure_ascii=False)
        except Exception:
            full = str(body)
        size = len(full)
        if size <= BODY_PREVIEW_MAX:
            return body, size, False
        return full[:BODY_PREVIEW_MAX], size, True
    if isinstance(body, bytes):
        try:
            s = body.decode("utf-8", errors="replace")
        except Exception:
            s = str(body)
        size = len(s)
        return s[:BODY_PREVIEW_MAX], size, size > BODY_PREVIEW_MAX
    s = str(body)
    size = len(s)
    return s[:BODY_PREVIEW_MAX], size, size > BODY_PREVIEW_MAX


def _normalize_body(body: Any) -> Any:
    """get_full 时把 bytes / 大结构规范化, 并按上限截断."""
    if body is None:
        return None
    if isinstance(body, bytes):
        s = body.decode("utf-8", errors="replace")
        return s[:BODY_FULL_MAX]
    if isinstance(body, (dict, list)):
        import json as _json

        try:
            s = _json.dumps(body, ensure_ascii=False)
        except Exception:
            return body
        if len(s) <= BODY_FULL_MAX:
            return body
        return s[:BODY_FULL_MAX]
    s = str(body)
    return s[:BODY_FULL_MAX]


_SENSITIVE_HEADER_KEYS = ("auth", "token", "cookie")


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """脱敏但保留 Authorization 结构 (前 4 + **** + 后 4), 方便定位。"""
    redacted: dict[str, str] = {}
    for k, v in headers.items():
        low = k.lower()
        if low == "authorization" and v.lower().startswith("bearer "):
            token = v[7:]
            if len(token) > 12:
                redacted[k] = f"Bearer {token[:6]}****{token[-4:]}"
            else:
                redacted[k] = "Bearer ****"
        elif any(s in low for s in _SENSITIVE_HEADER_KEYS):
            redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted
