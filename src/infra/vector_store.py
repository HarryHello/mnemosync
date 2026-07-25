"""向量存储: ChromaDB 封装.

存储记忆的 embedding 向量 + 关键元数据, 提供语义检索.
与 SQLite (persistence/memory_store) 通过 memory_id 关联.

**嵌入模型锁定** (v0.2.4+): collection metadata 记录 `(embedding_service_id,
embedding_model, embedding_dim)`。首次写入时自动 lock; 之后每次写入前
`assert_embedding_matches` 校验; 不一致抛 `VectorStoreLockError` — 换模型必须
走 reindex (清 lock → 清 collection → 重新写入)。
"""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings

from src.core.memory.models import MemoryEntry


class VectorStoreLockError(RuntimeError):
    """向量库锁定的嵌入模型元数据与当前调用不匹配."""

    def __init__(
        self,
        *,
        locked_service_id: str,
        locked_model: str,
        locked_dim: int,
        got_service_id: str,
        got_model: str,
        got_dim: int,
    ):
        self.locked_service_id = locked_service_id
        self.locked_model = locked_model
        self.locked_dim = locked_dim
        self.got_service_id = got_service_id
        self.got_model = got_model
        self.got_dim = got_dim
        super().__init__(
            f"向量库锁定为 {locked_service_id}/{locked_model} (dim={locked_dim}), "
            f"当前请求 {got_service_id}/{got_model} (dim={got_dim}). "
            "换嵌入模型必须走 reindex 流程."
        )


# 存到 collection metadata 的键名
_META_EMB_SERVICE = "embedding_service_id"
_META_EMB_MODEL = "embedding_model"
_META_EMB_DIM = "embedding_dim"
_META_HNSW = "hnsw:space"


class VectorStore:
    """ChromaDB 向量存储.

    一个 collection 存所有用户的记忆, 通过 metadata.source_user 过滤.
    """

    def __init__(self, persist_dir: str, collection_name: str = "mnemosync_memories"):
        self._collection_name = collection_name
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={_META_HNSW: "cosine"},
        )

    # ─── 嵌入锁 ──────────────────────────────────────────────

    def get_embedding_lock(self) -> dict[str, Any] | None:
        """返回当前锁定的嵌入元数据; 未锁定返回 None."""
        meta = self._collection.metadata or {}
        svc = meta.get(_META_EMB_SERVICE)
        model = meta.get(_META_EMB_MODEL)
        dim = meta.get(_META_EMB_DIM)
        if svc is None or model is None or dim is None:
            return None
        return {"service_id": svc, "model": model, "dim": int(dim)}

    def lock_embedding(self, service_id: str, model: str, dim: int) -> None:
        """设置 (或覆盖) 嵌入锁. 由首次写入或 reindex 完成时调用."""
        meta = dict(self._collection.metadata or {})
        meta.pop(_META_HNSW, None)  # chromadb 拒绝在 modify 中修改距离函数
        meta[_META_EMB_SERVICE] = service_id
        meta[_META_EMB_MODEL] = model
        meta[_META_EMB_DIM] = int(dim)
        self._collection.modify(metadata=meta)

    def clear_embedding_lock(self) -> None:
        """清除锁 (reindex 起始阶段). 保留除嵌入元数据外的其他字段."""
        meta = dict(self._collection.metadata or {})
        meta.pop(_META_HNSW, None)
        meta.pop(_META_EMB_SERVICE, None)
        meta.pop(_META_EMB_MODEL, None)
        meta.pop(_META_EMB_DIM, None)
        # chromadb 不允许 metadata 为空 dict, 传 None 表示"不动"; 需要至少一个键.
        # 我们塞一个惰性占位, 用户后续 lock_embedding 会覆盖。
        if not meta:
            meta = {"_placeholder": "unlocked"}
        self._collection.modify(metadata=meta)

    def assert_embedding_matches(self, service_id: str, model: str, dim: int) -> None:
        """写入前校验. 未锁定 → 直接设锁; 已锁定但不一致 → raise."""
        lock = self.get_embedding_lock()
        if lock is None:
            self.lock_embedding(service_id, model, dim)
            return
        if (
            lock["service_id"] != service_id
            or lock["model"] != model
            or lock["dim"] != int(dim)
        ):
            raise VectorStoreLockError(
                locked_service_id=lock["service_id"],
                locked_model=lock["model"],
                locked_dim=lock["dim"],
                got_service_id=service_id,
                got_model=model,
                got_dim=int(dim),
            )

    def reset_collection(self) -> None:
        """删除 + 重建空 collection. 用于 reindex 起始阶段."""
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={_META_HNSW: "cosine"},
        )

    # ─── 数据读写 ────────────────────────────────────────────

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
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """向量相似度检索（粗筛）.

        Args:
            query_vector: 查询向量
            top_k: 返回条数
            source_user: 限定来源用户 (v0.2.x 单用户路径)
            where: ChromaDB 复合 where 子句 (v0.3.0 受众粗筛, 支持 $or);
                显式传入时优先于 source_user

        Returns:
            list of {id, content, similarity, metadata}
        """
        if where is None and source_user:
            where = {"source_user": source_user}
        result = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )

        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        out: list[dict[str, Any]] = []
        for i, _id in enumerate(ids):
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
            "space_id": entry.space_id or "",
            "created_at": entry.created_at.isoformat(),
        }


__all__ = ["VectorStore", "VectorStoreLockError"]
