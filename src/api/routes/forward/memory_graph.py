"""异步执行记忆图 (流式模式下后台运行)."""
import logging
from typing import Any

from src.infra.forwarder import parse_sse_stream_full

from . import _get_compiled_graph

logger = logging.getLogger(__name__)


async def _run_memory_graph(
    initial_state: dict[str, Any],
    stream_chunks: list[bytes],
    graph_config: dict[str, Any] | None = None,
) -> None:
    """异步执行记忆图 (流式模式下后台运行)."""
    stream_result = parse_sse_stream_full(stream_chunks)
    response_text = stream_result.text or ""
    initial_state["response"] = response_text
    initial_state["response_chunks"] = stream_chunks
    # 流式响应无法获得完整的 assistant message;
    # 但 finish_reason 可用于判断是否跳过记忆/关系分析
    if stream_result.finish_reason:
        initial_state["finish_reason"] = stream_result.finish_reason

    # 纯工具调用 (无文本) 不需要记忆图, 直接返回
    if not response_text and stream_result.tool_calls:
        logger.debug("  ⏭️ 纯工具调用响应, 跳过记忆图")
        return

    graph = _get_compiled_graph()
    try:
        await graph.ainvoke(initial_state, config=graph_config)
    except Exception as e:
        # 后台任务失败仅日志
        import logging
        logging.getLogger(__name__).warning("后台记忆图执行失败: %s", e)
