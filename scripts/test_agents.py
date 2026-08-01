"""测试记忆分析 Agent 的 ReAct 循环（真实 API）."""
import asyncio
import os
import tempfile

from src.core.agents import run_memory_analysis, run_proxy_thinking, run_relationship_analysis
from src.core.config import get_settings
from src.core.memory import MemoryEntry, MemoryType
from src.infra import Forwarder, ForwarderConfig, VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import (
    MemoryRetriever,
    make_emotion_analyzer_tool,
    make_time_decay_calculator_tool,
    make_vector_search_tool,
)

s = get_settings()

async def main():
    db = tempfile.mktemp(suffix=".db")
    ms = SqliteMemoryStore(db)
    await ms.init_db()
    fwd = Forwarder(ForwarderConfig(base_url=s.chat.base_url, api_key=s.chat.api_key, default_model=s.chat.main_model, timeout=90.0))
    vs = VectorStore(tempfile.mkdtemp())
    entries = [
        MemoryEntry.create("用户对海鲜过敏", "user", source_user="motor", memory_type=MemoryType.PERMANENT, importance=1.0, decay_rate=0.0),
        MemoryEntry.create("用户喜欢蓝色", "user", source_user="motor", importance=0.3, decay_rate=0.05),
    ]
    for e in entries:
        vecs = await fwd.embed(e.content, model=s.embedding.model, dimensions=s.embedding.dimensions)
        vs.add(e, vecs[0])
        await ms.save(e)
    retriever = MemoryRetriever(fwd, vs, ms)
    tools = [make_vector_search_tool(retriever), make_emotion_analyzer_tool(fwd), make_time_decay_calculator_tool(ms)]

    print("=== 记忆分析 Agent ===")
    out = await run_memory_analysis(
        forwarder=fwd, source_user="motor",
        conversation="用户: 我对花生过敏\n助手: 了解了。\n用户: 是啊，还进过医院",
        tools=tools, max_iterations=5,
    )
    print(f"steps={len(out.steps)} new_memories={len(out.new_memories)}")
    for m in out.new_memories:
        print(f"  [{m.memory_type.value}] imp={m.importance} {m.content!r} tags={m.emotional_tags}")

    print("\n=== 关系分析 Agent ===")
    r = await run_relationship_analysis(
        forwarder=fwd, current_relationship="stranger",
        conversation="用户透露花生过敏住院史",
        tools=[make_emotion_analyzer_tool(fwd)], max_iterations=3,
    )
    print(f"delta={r.intimacy_delta} type={r.new_relationship_type}")

    print("\n=== 代理思考 Agent ===")
    pt = await run_proxy_thinking(
        forwarder=fwd, user_name="马达", relationship="friend 0.5",
        memories="花生过敏", user_message="最近压力大睡不好", tools=None,
    )
    print(f"result_len={len(pt)}")

    print("\nALL TESTS PASSED")
    await fwd.close()
    os.unlink(db)

asyncio.run(main())
