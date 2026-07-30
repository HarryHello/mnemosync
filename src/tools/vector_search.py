"""工具: 向量语义检索.

主对话 Agent 和记忆分析 Agent 通过 function_call 调用本工具,
检索与查询语义最相似的历史记忆.

流程: query → embedding → ChromaDB 粗筛 → reranker 精排 → 返回 MemoryEntry
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from src.infra.forwarder.multi import MultiForwarder
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore

if TYPE_CHECKING:
    from src.core.memory.audience import RetrievalContext


@dataclass
class RetrievedMemory:
    """检索结果项."""

    memory_id: str
    content: str
    similarity: float
    relevance_score: float | None  # rerank 分数（无 rerank 时为 None）
    importance: float
    memory_type: str
    emotional_tags: list[str]
    source_user: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "similarity": round(self.similarity, 4),
            "relevance_score": round(self.relevance_score, 4) if self.relevance_score is not None else None,
            "importance": self.importance,
            "memory_type": self.memory_type,
            "emotional_tags": self.emotional_tags,
            "source_user": self.source_user,
        }


# 共享的检索器实现（非 LangChain Tool 部分, 便于直接复用）
class MemoryRetriever:
    """记忆语义检索器（供 vector_search 工具和 memory.retrieval 共用）."""

    def __init__(
        self,
        forwarder: MultiForwarder,
        vector_store: VectorStore,
        memory_store: SqliteMemoryStore,
    ):
        self.forwarder = forwarder
        self.vector_store = vector_store
        self.memory_store = memory_store

    async def search(
        self,
        query: str,
        top_k: int = 5,
        source_user: str | None = None,
        use_rerank: bool = True,
        retrieval_ctx: RetrievalContext | None = None,
    ) -> list[RetrievedMemory]:
        """语义检索.

        Args:
            query: 查询文本
            top_k: 最终返回条数
            source_user: 限定来源用户 (v0.2.x 路径; 传 retrieval_ctx 时忽略)
            use_rerank: 是否使用 reranker 精排（无候选则降级）
            retrieval_ctx: 受众上下文 (v0.3.0). 传入时粗筛放宽到可见超集
                ($or: 自己 / public / 本空间), 拿到 MemoryEntry 后按
                AudienceFilter 精筛 — 其他参与者的私有记忆不会进结果.

        embedding 维度由所选模型决定, 无需外部配置.
        """
        from src.core.memory.audience import AudienceFilter

        # 1. embedding (role=EMBEDDING, 维度由绑定模型决定)
        from src.infra.debug_context import use_agent
        with use_agent("memory_retriever"):
            vectors = await self.forwarder.embed(query)
        query_vector = vectors[0]

        # 2. ChromaDB 粗筛. 有受众上下文时放宽 where 并多取候选 (精筛会淘汰一批)
        if retrieval_ctx is not None:
            where = AudienceFilter.build_chromadb_where(retrieval_ctx)
            candidates = self.vector_store.search(
                query_vector, top_k=max(top_k * 3, 15), where=where,
            )
        else:
            candidates = self.vector_store.search(
                query_vector, top_k=max(top_k * 2, 10),
                source_user=source_user,
            )
        if not candidates:
            return []

        # 3. reranker 精排（可选; 无绑定则降级为纯 cosine）
        results: list[RetrievedMemory] = []
        if use_rerank:
            try:
                documents = [c["content"] for c in candidates]
                rerank_results = await self.forwarder.rerank(
                    query, documents, top_n=max(top_k * 2, 10) if retrieval_ctx else top_k,
                )
                for r in rerank_results:
                    idx = r["index"]
                    if idx >= len(candidates):
                        continue
                    c = candidates[idx]
                    entry = await self.memory_store.get_by_id(c["id"])
                    if entry is None:
                        continue
                    if retrieval_ctx is not None and not AudienceFilter.is_visible(entry, retrieval_ctx):
                        continue
                    results.append(self._build_result(entry, c["similarity"], r.get("relevance_score")))
                    if len(results) >= top_k:
                        break
            except Exception:
                # rerank 未配置或失败降级为纯 cosine
                for c in candidates:
                    entry = await self.memory_store.get_by_id(c["id"])
                    if entry is None:
                        continue
                    if retrieval_ctx is not None and not AudienceFilter.is_visible(entry, retrieval_ctx):
                        continue
                    results.append(self._build_result(entry, c["similarity"], None))
                    if len(results) >= top_k:
                        break
        else:
            for c in candidates:
                entry = await self.memory_store.get_by_id(c["id"])
                if entry is None:
                    continue
                if retrieval_ctx is not None and not AudienceFilter.is_visible(entry, retrieval_ctx):
                    continue
                results.append(self._build_result(entry, c["similarity"], None))
                if len(results) >= top_k:
                    break

        return results

    def _build_result(
        self, entry, similarity: float, relevance: float | None
    ) -> RetrievedMemory:
        return RetrievedMemory(
            memory_id=entry.id,
            content=entry.content,
            similarity=similarity,
            relevance_score=relevance,
            importance=entry.importance,
            memory_type=entry.memory_type.value,
            emotional_tags=entry.emotional_tags,
            source_user=entry.source_user,
        )

def make_vector_search_tool(
    retriever: MemoryRetriever,
    retrieval_ctx: RetrievalContext | None = None,
):
    """创建 vector_search LangChain Tool.

    Args:
        retriever: 已初始化的 MemoryRetriever
        retrieval_ctx: 受众上下文 (v0.3.0). 传入后检索按当前会话受众过滤,
            Agent 无法越权检索其他参与者的私有记忆; source_user 参数被忽略.

    Returns:
        LangChain Tool 实例
    """

    @tool
    async def vector_search(
        query: str,
        top_k: int = 5,
        source_user: str | None = None,
    ) -> list[dict[str, Any]]:
        """搜索与查询文本语义最相似的历史记忆.

        用于:
        - 主对话 Agent: 回忆与当前对话相关的历史记忆
        - 记忆分析 Agent: 判断新信息是否与已有记忆重复/冲突/关联

        Args:
            query: 查询文本（通常是用户最新消息或要查重的关键词）
            top_k: 返回结果数量, 默认 5
            source_user: 限定来源用户（通常传入当前对话用户的标识）

        Returns:
            相关记忆列表, 每条包含 memory_id, content, similarity,
            relevance_score, importance, memory_type, emotional_tags, source_user
        """
        results = await retriever.search(
            query, top_k=top_k, source_user=source_user,
            retrieval_ctx=retrieval_ctx,
        )
        return [r.to_dict() for r in results]

    return vector_search
