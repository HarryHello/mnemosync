"""测试 vector_search 工具的创建和基本调用.

覆盖: 工具创建, 工具名称/描述, 使用 mock retriever 的调用.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.tools.vector_search import (
    MemoryRetriever,
    RetrievedMemory,
    make_vector_search_tool,
)

# ─── RetrievedMemory ───────────────────────────────────


def test_retrieved_memory_to_dict():
    m = RetrievedMemory(
        memory_id="mem-1",
        content="hello world",
        similarity=0.85,
        relevance_score=0.9,
        importance=0.7,
        memory_type="permanent",
        emotional_tags=["happy"],
        source_user="alice",
    )
    d = m.to_dict()
    assert d["memory_id"] == "mem-1"
    assert d["content"] == "hello world"
    assert d["similarity"] == 0.85
    assert d["relevance_score"] == 0.9
    assert d["importance"] == 0.7
    assert d["emotional_tags"] == ["happy"]
    assert d["source_user"] == "alice"


def test_retrieved_memory_to_dict_no_relevance():
    m = RetrievedMemory(
        memory_id="mem-2",
        content="test",
        similarity=0.5,
        relevance_score=None,
        importance=0.3,
        memory_type="normal",
        emotional_tags=[],
        source_user=None,
    )
    d = m.to_dict()
    assert d["relevance_score"] is None


# ─── Tool creation ─────────────────────────────────────


def test_make_vector_search_tool_returns_tool():
    retriever = MagicMock(spec=MemoryRetriever)
    tool_fn = make_vector_search_tool(retriever)
    # LangChain @tool decorates the function; it should have a .name
    assert hasattr(tool_fn, "name")
    assert tool_fn.name == "vector_search"


def test_make_vector_search_tool_has_description():
    retriever = MagicMock(spec=MemoryRetriever)
    tool_fn = make_vector_search_tool(retriever)
    desc = tool_fn.description
    assert "记忆" in desc or "memory" in desc.lower()


# ─── Basic invocation ─────────────────────────────────


@pytest.mark.asyncio
async def test_vector_search_tool_invocation():
    retriever = AsyncMock(spec=MemoryRetriever)
    retriever.search = AsyncMock(return_value=[
        RetrievedMemory(
            memory_id="m1",
            content="user likes cats",
            similarity=0.9,
            relevance_score=0.85,
            importance=0.7,
            memory_type="permanent",
            emotional_tags=["animal"],
            source_user="bob",
        ),
        RetrievedMemory(
            memory_id="m2",
            content="user allergic to peanuts",
            similarity=0.6,
            relevance_score=None,
            importance=0.9,
            memory_type="permanent",
            emotional_tags=["health"],
            source_user="bob",
        ),
    ])

    tool_fn = make_vector_search_tool(retriever)
    results = await tool_fn.ainvoke({"query": "pets and food", "top_k": 2})

    assert len(results) == 2
    assert results[0]["memory_id"] == "m1"
    assert results[0]["content"] == "user likes cats"
    assert results[1]["memory_id"] == "m2"
    retriever.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_vector_search_tool_empty_results():
    retriever = AsyncMock(spec=MemoryRetriever)
    retriever.search = AsyncMock(return_value=[])

    tool_fn = make_vector_search_tool(retriever)
    results = await tool_fn.ainvoke({"query": "nothing relevant", "top_k": 5})

    assert results == []


@pytest.mark.asyncio
async def test_vector_search_tool_passes_retrieval_ctx():
    retriever = AsyncMock(spec=MemoryRetriever)
    retriever.search = AsyncMock(return_value=[])

    ctx = MagicMock()
    tool_fn = make_vector_search_tool(retriever, retrieval_ctx=ctx)
    await tool_fn.ainvoke({"query": "test", "top_k": 1})

    retriever.search.assert_awaited_once_with(
        "test", top_k=1, source_user=None, retrieval_ctx=ctx,
    )


@pytest.mark.asyncio
async def test_vector_search_tool_passes_source_user():
    retriever = AsyncMock(spec=MemoryRetriever)
    retriever.search = AsyncMock(return_value=[])

    tool_fn = make_vector_search_tool(retriever)
    await tool_fn.ainvoke({"query": "test", "top_k": 3, "source_user": "alice"})

    retriever.search.assert_awaited_once_with(
        "test", top_k=3, source_user="alice", retrieval_ctx=None,
    )
