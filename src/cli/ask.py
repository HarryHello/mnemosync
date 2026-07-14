"""`mnemosync ask` — 命令行直连 LangGraph 主对话.

不经过 HTTP 服务器, 直接 in-process 调用 build_graph().ainvoke(),
方便调试 prompt / 图流程 / 记忆读写.

用法:
    mnemosync ask "你好"
    mnemosync ask --user harry --persona-file persona.txt "..."
    mnemosync ask --stream "..."
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


DEFAULT_PERSONA = "你是一个温暖、有记忆能力的 AI 助手。"
DEFAULT_PERSONA_NAME = "助手"


def _read_persona(path: str | None) -> tuple[str, str]:
    """从文件读取人格 prompt. 返回 (persona, persona_name).

    persona_name 从文件名 (不含扩展名) 派生, 便于观察多人格差异.
    """
    if not path:
        return DEFAULT_PERSONA, DEFAULT_PERSONA_NAME
    p = Path(path)
    if not p.is_file():
        print(f"❌ 人格文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    return p.read_text(encoding="utf-8").strip(), p.stem


async def _run_non_stream(question: str, source_user: str, persona: str, persona_name: str) -> int:
    from src.core.graph import build_graph

    graph = build_graph()
    initial_state = {
        "messages": [{"role": "user", "content": question}],
        "source_user": source_user,
        "persona": persona,
        "persona_name": persona_name,
        "proxy_thinking_enabled": False,
        "stream_mode": False,
    }

    print(f"💬 [{source_user} → {persona_name}] {question}\n")
    print("⏳ 运行图...", file=sys.stderr)
    final_state = await graph.ainvoke(initial_state)
    response = final_state.get("response", "")
    if not response:
        print("(无回复)", file=sys.stderr)
        return 1
    print(response)
    return 0


async def _run_stream(question: str, source_user: str, persona: str, persona_name: str) -> int:
    """流式模式: 复用 forward.py 的 _handle_stream 逻辑, 直接把 SSE chunks 打到 stdout.

    与生产的差异: 生产里 forward.py 用 asyncio.create_task 把记忆图挂后台;
    CLI 进程短命, 后台任务会被 kill, 所以这里显式 await 让记忆分析完整跑完.
    """
    from src.core.config import get_settings
    from src.core.graph import build_graph
    from src.core.memory import format_relationship
    from src.core.memory.context import build_main_dialogue_messages
    from src.infra.forwarder import Forwarder, ForwarderConfig, UpstreamError, UpstreamTimeout, parse_sse_stream
    from src.infra.vector_store import VectorStore
    from src.persistence.memory_store import SqliteMemoryStore
    from src.tools import MemoryRetriever

    settings = get_settings()
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    perms = await memory_store.list_permanent(source_user, limit=settings.memory.permanent_load_top)

    retrieved_entries: list = []
    forwarder_config = ForwarderConfig(
        base_url=settings.chat.base_url,
        api_key=settings.chat.api_key,
        default_model=settings.chat.main_model,
        timeout=30.0,
    )
    async with Forwarder(forwarder_config) as forwarder:
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)
        results = await retriever.search(
            question, top_k=settings.memory.retrieval_top_k, source_user=source_user,
        )
        for r in results:
            await memory_store.mark_accessed(r.memory_id)
            entry = await memory_store.get_by_id(r.memory_id)
            if entry:
                retrieved_entries.append(entry)

    rel = await memory_store.get_relationship("default", source_user)
    print(
        f"🧠 记忆: 永久 {len(perms)} 条 · 检索 {len(retrieved_entries)} 条 · 关系 "
        f"{format_relationship(rel) if rel else '(无)'}",
        file=sys.stderr,
    )

    messages_with_memory = build_main_dialogue_messages(
        persona_prompt=persona,
        persona_name=persona_name,
        user_name=source_user,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        conversation_history=[{"role": "user", "content": question}],
        relationship=rel,
    )

    print(f"💬 [{source_user} → {persona_name}] {question}\n", file=sys.stderr)

    stream_config = ForwarderConfig(
        base_url=settings.chat.base_url,
        api_key=settings.chat.api_key,
        default_model=settings.chat.main_model,
        timeout=90.0,
    )
    chunks: list[bytes] = []
    buf = b""
    async with Forwarder(stream_config) as forwarder:
        try:
            async for chunk in forwarder.chat_stream(
                messages=messages_with_memory,
                model=settings.chat.main_model,
                temperature=None,
                max_tokens=None,
            ):
                chunks.append(chunk)
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line or not line.startswith(b"data: "):
                        continue
                    data = line[6:]
                    if data == b"[DONE]":
                        continue
                    try:
                        import json
                        obj = json.loads(data)
                        delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            sys.stdout.write(delta)
                            sys.stdout.flush()
                    except Exception:
                        continue
        except UpstreamTimeout as e:
            print(f"\n⏰ 上游超时: {e}", file=sys.stderr)
            return 1
        except UpstreamError as e:
            print(f"\n❌ 上游错误: {e.message}", file=sys.stderr)
            return 1

    print()

    response_text = parse_sse_stream(chunks)

    print("🔄 触发记忆图 (记忆分析 + 关系分析)...", file=sys.stderr)
    graph = build_graph()
    memory_state = {
        "messages": [{"role": "user", "content": question}],
        "source_user": source_user,
        "persona": persona,
        "persona_name": persona_name,
        "proxy_thinking_enabled": False,
        "stream_mode": True,
        "response": response_text,
        "response_chunks": chunks,
    }
    try:
        await graph.ainvoke(memory_state)
        print("✅ 记忆图执行完成", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  记忆图执行失败: {e}", file=sys.stderr)
    return 0


async def _run_via_http(question: str, source_user: str, persona: str, api_key: str, base_url: str, stream: bool) -> int:
    """--via-http: 走本地 serve 的 OpenAI 兼容接口."""
    import httpx

    messages = [
        {"role": "system", "content": persona},
        {"role": "user", "content": question},
    ]
    payload = {
        "model": "mnemosync-any",
        "messages": messages,
        "user": source_user,
        "stream": stream,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=90.0) as client:
        if not stream:
            r = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, headers=headers)
            if r.status_code != 200:
                print(f"❌ HTTP {r.status_code}: {r.text}", file=sys.stderr)
                return 1
            data = r.json()
            print(data["choices"][0]["message"]["content"])
            return 0

        async with client.stream(
            "POST", f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, headers=headers,
        ) as r:
            if r.status_code != 200:
                body = await r.aread()
                print(f"❌ HTTP {r.status_code}: {body.decode(errors='replace')}", file=sys.stderr)
                return 1
            async for line in r.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    import json
                    obj = json.loads(data)
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        print(delta, end="", flush=True)
                except Exception:
                    continue
            print()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """`mnemosync ask` 主入口."""
    # 让 src.* 可导入 (与其它 cmd_* 一致)
    project_root = Path(__file__).resolve().parents[2]
    os.chdir(project_root)
    os.environ.setdefault("PYTHONPATH", str(project_root))

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    question = args.question
    if not question:
        if sys.stdin.isatty():
            print(
                "❌ 未提供问题。用法示例:\n"
                '   mnemosync ask "你好"\n'
                '   echo "你好" | mnemosync ask\n'
                "提示: --user 需要一个值, 之后再跟问题, 如 mnemosync ask --user harry \"你好\"",
                file=sys.stderr,
            )
            return 1
        question = sys.stdin.read().strip()
        if not question:
            print("❌ stdin 为空", file=sys.stderr)
            return 1

    persona, persona_name = _read_persona(args.persona_file)

    if args.via_http:
        api_key = args.api_key or os.getenv("MNEMOSYNC_API_KEY")
        if not api_key:
            print("❌ --via-http 需要 --api-key 或 MNEMOSYNC_API_KEY 环境变量", file=sys.stderr)
            return 1
        return asyncio.run(
            _run_via_http(question, args.user, persona, api_key, args.base_url, args.stream)
        )

    if args.stream:
        return asyncio.run(_run_stream(question, args.user, persona, persona_name))
    return asyncio.run(_run_non_stream(question, args.user, persona, persona_name))
