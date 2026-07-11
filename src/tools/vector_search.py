"""工具: 向量语义检索.

主对话 Agent 和记忆分析 Agent 通过 function_call 调用本工具,
检索与查询语义最相似的历史记忆.

流程: query → embedding → ChromaDB 粗筛 → reranker 精排 → 返回 MemoryEntry
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import tool

from src.core.config import get_settings
from src.infra import Forwarder, ForwarderConfig
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore


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
        forwarder: Forwarder,
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
    ) -> list[RetrievedMemory]:
        """语义检索.

        Args:
            query: 查询文本
            top_k: 最终返回条数
            source_user: 限定来源用户
            use_rerank: 是否使用 reranker 精排（无配置则降级）
        """
        settings = get_settings()

        # 1. embedding
        vectors = await self.forwarder.embed(
            query, model=settings.embedding.model,
            dimensions=settings.embedding.dimensions,
        )
        query_vector = vectors[0]

        # 2. ChromaDB 粗筛（取 top_k * 2 候选）
        candidates = self.vector_store.search(
            query_vector, top_k=max(top_k * 2, 10),
            source_user=source_user,
        )
        if not candidates:
            return []

        # 3. reranker 精排（可选）
        results: list[RetrievedMemory] = []
        if use_rerank and settings.rerank:
            try:
                documents = [c["content"] for c in candidates]
                rerank_results = await self.forwarder.rerank(
                    query, documents, model=settings.rerank.model, top_n=top_k,
                )
                # 构建 memory_id → relevance 映射
                for r in rerank_results:
                    idx = r["index"]
                    if idx >= len(candidates):
                        continue
                    c = candidates[idx]
                    entry = await self.memory_store.get_by_id(c["id"])
                    if entry is None:
                        continue
                    results.append(self._build_result(entry, c["similarity"], r.get("relevance_score")))
            except Exception:
                # rerank 失败降级为纯 cosine
                results = [self._build_result_from_search(c) for c in candidates[:top_k]]
                # 补完整字段
                patched: list[RetrievedMemory] = []
                for r in results:
                    entry = await self.memory_store.get_by_id(r.memory_id)
                    if entry:
                        patched.append(self._build_result(entry, r.similarity, None))
                results = patched
        else:
            for c in candidates[:top_k]:
                entry = await self.memory_store.get_by_id(c["id"])
                if entry:
                    results.append(self._build_result(entry, c["similarity"], None))

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

    def _build_result_from_search(self, c: dict) -> RetrievedMemory:
        meta = c.get("metadata", {})
        return RetrievedMemory(
            memory_id=c["id"],
            content=c["content"],
            similarity=c["similarity"],
            relevance_score=None,
            importance=meta.get("importance", 0.5),
            memory_type=meta.get("memory_type", "normal"),
            emotional_tags=meta.get("emotional_tags", "").split("|") if meta.get("emotional_tags") else [],
            source_user=meta.get("source_user"),
        )


def make_vector_search_tool(retriever: MemoryRetriever):
    """创建 vector_search LangChain Tool.

    Args:
        retriever: 已初始化的 MemoryRetriever

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
        results = await retriever.search(query, top_k=top_k, source_user=source_user)
        return [r.to_dict() for r in results]

    return vector_search
