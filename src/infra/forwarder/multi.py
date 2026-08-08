"""多候选转发器: 按角色优先级依次尝试, 遇上游错误 fallback.

与单一 ``Forwarder`` 的关系:
- ``Forwarder`` 面向单个 (base_url, api_key), 是最底层的 HTTP 调用
- ``MultiForwarder`` 在其之上, 按 ``RoleResolver`` 返回的候选列表遍历, 对
  ``UpstreamTimeout`` / ``UpstreamError(5xx)`` / 连接错误做 fallback
- 4xx 上游客户端错误 (bad request / auth 失败 / 上下文超长) 不 fallback, 直接抛

流式 (chat_stream) 只对"首字节前"的错误 fallback. 一旦已经产出 chunk, 中途中断保留
原样, 不切候选 —— 这符合用户"下次请求生效"的语义, 且实现简单 (无需 buffering).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.models.resolver import NoCandidateForRoleError, RoleResolver
from src.infra.llm_service.models import ModelType, ResolvedCandidate

from .anthropic import AnthropicForwarder, AnthropicForwarderConfig
from .connection_pool import ConnectionPool
from .forwarder import Forwarder, ForwarderConfig, UpstreamError, UpstreamTimeout
from .responses import ResponsesForwarder, ResponsesForwarderConfig

logger = logging.getLogger(__name__)

# 通用转发器类型 (OpenAI Forwarder / AnthropicForwarder / ResponsesForwarder)
ForwarderType = Forwarder | AnthropicForwarder | ResponsesForwarder


class UpstreamAllCandidatesFailed(Exception):
    """所有候选都尝试失败."""

    def __init__(self, role: ModelType, errors: list[tuple[ResolvedCandidate, Exception]]):
        self.role = role
        self.errors = errors
        summary = "; ".join(
            f"{c.service_id}/{c.model} → {type(e).__name__}: {e}" for c, e in errors
        )
        super().__init__(f"角色 '{role.value}' 全部 {len(errors)} 个候选失败: {summary}")


def _should_fallback(exc: BaseException) -> bool:
    """判断某个异常是否属于'该候选挂了, 试下一个'类别."""
    if isinstance(exc, UpstreamTimeout):
        return True
    if isinstance(exc, UpstreamError):
        # 4xx = 客户端请求本身有问题, 换服务也没用
        if exc.status_code is not None and 400 <= exc.status_code < 500:
            return False
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
        return True
    return False


@dataclass
class MultiForwarderConfig:
    """共享的超时/连接配置, 应用到所有候选."""

    timeout: float = 60.0
    connect_timeout: float = 10.0


class MultiForwarder:
    """按角色候选列表进行 HTTP 调用, 内部维护每个 service 的 ``Forwarder``.

    使用方式::

        multi = MultiForwarder(resolver)
        result = await multi.chat(role=ModelType.MAIN, messages=[...])
        async for chunk in multi.chat_stream(role=ModelType.MAIN, messages=[...]):
            ...
    """

    def __init__(
        self,
        resolver: RoleResolver,
        *,
        pool: ConnectionPool | None = None,
        config: MultiForwarderConfig | None = None,
    ):
        self._resolver = resolver
        self._pool = pool
        self._config = config or MultiForwarderConfig()
        # 按 service_id 缓存 Forwarder, 根据 api_format 选择实现
        self._forwarders: dict[str, ForwarderType] = {}

    def _get_forwarder(self, candidate: ResolvedCandidate) -> ForwarderType:
        fwd = self._forwarders.get(candidate.service_id)
        if fwd is None:
            if candidate.api_format == "anthropic":
                fwd = AnthropicForwarder(
                    AnthropicForwarderConfig(
                        base_url=candidate.base_url,
                        api_key=candidate.api_key,
                        default_model=candidate.model,
                        timeout=self._config.timeout,
                    ),
                )
            elif candidate.api_format == "responses":
                fwd = ResponsesForwarder(
                    ResponsesForwarderConfig(
                        base_url=candidate.base_url,
                        api_key=candidate.api_key,
                        default_model=candidate.model,
                        timeout=self._config.timeout,
                    ),
                )
            else:
                fwd = Forwarder(
                    ForwarderConfig(
                        base_url=candidate.base_url,
                        api_key=candidate.api_key,
                        default_model=candidate.model,
                        timeout=self._config.timeout,
                        connect_timeout=self._config.connect_timeout,
                    ),
                    pool=self._pool,
                )
            self._forwarders[candidate.service_id] = fwd
        return fwd

    def _get_openai_forwarder(self, candidate: ResolvedCandidate) -> Forwarder:
        """获取 OpenAI 格式转发器. embed/rerank 仅 OpenAI 兼容端点支持."""
        fwd = self._get_forwarder(candidate)
        if not isinstance(fwd, Forwarder):
            raise UpstreamError(
                None,
                f"embed/rerank 不支持 api_format='{candidate.api_format}' "
                f"(service={candidate.service_id}), 仅支持 openai 格式",
            )
        return fwd

    async def _candidates(
        self, role: ModelType, override: Iterable[ResolvedCandidate] | None = None
    ) -> list[ResolvedCandidate]:
        if override is not None:
            return list(override)
        return await self._resolver.for_role(role, require=True)

    # ============ 对话 ============

    async def chat(
        self,
        role: ModelType,
        messages: list[dict[str, Any]],
        *,
        candidates: Iterable[ResolvedCandidate] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """非流式对话. 按候选依次尝试, 首个成功返回."""
        cands = await self._candidates(role, candidates)
        errors: list[tuple[ResolvedCandidate, Exception]] = []
        for c in cands:
            fwd = self._get_forwarder(c)
            try:
                return await fwd.chat(messages=messages, model=c.model, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if not _should_fallback(exc):
                    raise
                logger.warning(
                    "chat fallback: role=%s service=%s model=%s error=%s",
                    role.value, c.service_id, c.model, exc,
                )
                errors.append((c, exc))
        raise UpstreamAllCandidatesFailed(role, errors)

    async def chat_stream(
        self,
        role: ModelType,
        messages: list[dict[str, Any]],
        *,
        candidates: Iterable[ResolvedCandidate] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """流式对话.

        只在"首字节前"的错误上 fallback. 一旦某个候选开始产出 chunk, 中途中断不切换.
        """
        cands = await self._candidates(role, candidates)
        errors: list[tuple[ResolvedCandidate, Exception]] = []

        async def _iter() -> AsyncIterator[bytes]:
            for c in cands:
                fwd = self._get_forwarder(c)
                gen = fwd.chat_stream(messages=messages, model=c.model, **kwargs)
                # 首字节前的错误 → fallback; 首字节后的错误 → 原样抛给调用方
                first_chunk: bytes | None = None
                try:
                    try:
                        first_chunk = await gen.__anext__()
                    except StopAsyncIteration:
                        # 空响应也算成功建立, 但没有数据可 yield —— 直接返回
                        return
                except Exception as exc:  # noqa: BLE001
                    if not _should_fallback(exc):
                        raise
                    logger.warning(
                        "chat_stream fallback: role=%s service=%s model=%s error=%s",
                        role.value, c.service_id, c.model, exc,
                    )
                    errors.append((c, exc))
                    continue
                # 已经产出首字节, 后续错误不再 fallback
                yield first_chunk
                async for chunk in gen:
                    yield chunk
                return
            raise UpstreamAllCandidatesFailed(role, errors)

        async for chunk in _iter():
            yield chunk

    # ============ 嵌入 ============

    async def embed(
        self,
        input: str | list[str],
        *,
        role: ModelType = ModelType.EMBEDDING,
        candidates: Iterable[ResolvedCandidate] | None = None,
        dimensions: int | None = None,
    ) -> list[list[float]]:
        """嵌入调用. 嵌入语义空间不兼容不同模型, 不做 fallback: 只用第一条候选.

        `dimensions` 参数**只在必要时透传**上游:
          * 调用方显式传入 `dimensions` → 无条件透传 (由调用方负责判断合法性,
            例如 probe-dimension 端点在探测阶段)
          * 未显式传入且绑定 `send_dimensions=True` → 用 `c.embedding_dim` 透传
            (可变维模型: text-embedding-3-*, text-embedding-v3/v4, qwen3-embedding-*)
          * 其余情况 → 不发 `dimensions` (兼容 bge/bce/jina/mistral/gemini 等
            固定维模型, 它们对 `dimensions` 参数会返 400)

        `embedding_dim` 本身依然用于向量库维度锁定 (VectorStore.assert_embedding_matches),
        与是否透传上游正交.
        """
        cands = await self._candidates(role, candidates)
        c = cands[0]
        fwd = self._get_openai_forwarder(c)
        if dimensions is not None:
            effective_dim: int | None = dimensions
        elif c.send_dimensions:
            effective_dim = c.embedding_dim
        else:
            effective_dim = None
        return await fwd.embed(input=input, model=c.model, dimensions=effective_dim)

    # ============ 重排序 ============

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        role: ModelType = ModelType.RERANK,
        candidates: Iterable[ResolvedCandidate] | None = None,
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        cands = await self._candidates(role, candidates)
        errors: list[tuple[ResolvedCandidate, Exception]] = []
        for c in cands:
            try:
                fwd = self._get_openai_forwarder(c)
                return await fwd.rerank(query=query, documents=documents, model=c.model, top_n=top_n)
            except Exception as exc:  # noqa: BLE001
                if not _should_fallback(exc):
                    raise
                logger.warning(
                    "rerank fallback: service=%s model=%s error=%s",
                    c.service_id, c.model, exc,
                )
                errors.append((c, exc))
        raise UpstreamAllCandidatesFailed(role, errors)

    # ============ 生命周期 ============

    async def close(self) -> None:
        for fwd in self._forwarders.values():
            try:
                await fwd.close()
            except Exception:  # noqa: BLE001
                logger.exception("Forwarder close failed")
        self._forwarders.clear()

    async def __aenter__(self) -> MultiForwarder:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()


__all__ = [
    "MultiForwarder",
    "MultiForwarderConfig",
    "UpstreamAllCandidatesFailed",
    "NoCandidateForRoleError",
]
