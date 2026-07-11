"""向量存储: ChromaDB 封装.

存储记忆的 embedding 向量 + 关键元数据, 提供语义检索.
与 SQLite (persistence/memory_store) 通过 memory_id 关联.
"""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings

from src.core.memory.models import MemoryEntry


class VectorStore:
    """ChromaDB 向量存储.

    一个 collection 存所有用户的记忆, 通过 metadata.source_user 过滤.
    """

    def __init__(self, persist_dir: str, collection_name: str = "mnemosync_memories"):
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, entry: MemoryEntry, vector: list[float]) -> None:
        """添加/更新一条记忆的向量."""
        self._collection.add(
            ids=[entry.id],
            embeddings=[vector],
            metadatas=[self._entry_to_metadata(entry)],
            documents=[entry.content],
        )

    def update(self, entry: MemoryEntry, vector: list[float]) -> None:
        """更新已有记忆（含向量和元数据）."""
        # chromadb 的 upsert 在新版本可用; 用 add + 处理已存在的情况
        try:
            self._collection.delete(ids=[entry.id])
        except Exception:
            pass
        self.add(entry, vector)

    def delete(self, memory_id: str) -> None:
        try:
            self._collection.delete(ids=[memory_id])
        except Exception:
            pass

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        source_user: str | None = None,
    ) -> list[dict[str, Any]]:
        """向量相似度检索（粗筛）.

        Returns:
            list of {id, content, similarity, metadata}
        """
        where = {"source_user": source_user} if source_user else None
        # 注意: chromadb where 子句对 None 字段需特殊处理; source_user 总是存在
        result = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )

        # 结果结构: {ids: [[...]], documents: [[...]], distances: [[...]], metadatas: [[...]]}
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        out: list[dict[str, Any]] = []
        for i, _id in enumerate(ids):
            # cosine distance → similarity (chromadb cosine: distance 0 = 最相似)
            distance = distances[i] if i < len(distances) else 1.0
            similarity = max(0.0, 1.0 - distance)
            out.append({
                "id": _id,
                "content": documents[i] if i < len(documents) else "",
                "similarity": similarity,
                "metadata": metadatas[i] if i < len(metadatas) else {},
            })
        return out

    def count(self) -> int:
        return self._collection.count()

    @staticmethod
    def _entry_to_metadata(entry: MemoryEntry) -> dict[str, Any]:
        """提取用于 ChromaDB metadata 的字段（需是基础类型）."""
        return {
            "source_user": entry.source_user or "",
            "memory_type": entry.memory_type.value,
            "importance": float(entry.importance),
            "priority": float(entry.priority),
            "is_forgotten": bool(entry.is_forgotten),
            "emotional_tags": "|".join(entry.emotional_tags),
            "visibility": entry.visibility.value,
            "created_at": entry.created_at.isoformat(),
        }
