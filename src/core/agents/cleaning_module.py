"""提示词模块分割: 用 markdown-it-py token 流把客户端 system 提示词切成清洗模块.

设计 (v0.4.1):
- 模块边界: 1~3 级标题 (heading_open) + 分割线 (hr, `---`); 4~6 级标题不分割
- 代码块/引用块/列表整体归属当前模块 (token 天然保证, 内部 `#` 不误判)
- 开头无标题段 = preamble 独立模块
- 游离段 (无标题正文) 归属前一模块 (markdown 标准语义)

每个模块输出:
- title: 标题文本 (清洗后拼接时由 LLM 决定是否保留标题, 但 title 用于面板展示/跳过配置)
- text: 模块源文本 (含标题行)
- 用 token 的 map 区间切原文, 保持字节级保真
"""

from __future__ import annotations

from dataclasses import dataclass

from markdown_it import MarkdownIt

# 最多分割到 3 级标题 (#### 及更深不分割)
MAX_HEADING_LEVEL = 3

_md = MarkdownIt("commonmark", {"html": False, "maxNesting": 20})


@dataclass
class CleaningModule:
    """一个清洗模块: 标题 + 源文本切片."""

    title: str
    text: str


def split_prompt_modules(text: str) -> list[CleaningModule]:
    """把 system 提示词按标题/分割线切成模块.

    Returns:
        有序模块列表; 无标题文本时返回单个 `[pre]` 模块.
    """
    source = text
    # 用 tokenize 拿结构 (不渲染)
    tokens = _md.parse(source)

    # 记录边界 token 的行号区间: (start_line, end_line, title)
    boundaries: list[tuple[int, int, str | None]] = []
    # token.map = [start_line, end_line); 标题行在 heading_open.map[0]
    for tok in tokens:
        if tok.type == "heading_open":
            level = int(tok.tag[1]) if tok.tag.startswith("h") else 0
            if level <= MAX_HEADING_LEVEL and tok.map is not None:
                boundaries.append((tok.map[0], tok.map[1], None))  # 标题行; title 由 inline 取
        elif tok.type == "hr" and tok.map is not None:
            # 分割线作为模块边界: 线前内容归上一模块, 线后开新模块
            boundaries.append((tok.map[0], tok.map[1], "---"))

    if not boundaries:
        return [CleaningModule(title="pre", text=source)]

    # 把标题行号和标题文本关联 (heading_open 下一 token 是 inline with content)
    title_by_line: dict[int, str] = {}
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map is not None:
            level = int(tok.tag[1]) if tok.tag.startswith("h") else 0
            if level <= MAX_HEADING_LEVEL:
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                content = inline_tok.content if inline_tok and inline_tok.type == "inline" else ""
                title_by_line[tok.map[0]] = content.strip()

    # line_count = 总行数
    line_count = (tokens[-1].map[1] if tokens and tokens[-1].map else source.count("\n") + 1)
    lines = source.splitlines()

    def slice_lines(start: int, end: int) -> str:
        """按行区间切原文 (start 含, end 不含)."""
        if start >= end:
            return ""
        return "\n".join(lines[start:end])

    # 构建模块: 按边界行排序, 切出 [boundary_line, next_boundary_line)
    modules: list[CleaningModule] = []
    sorted_boundaries = sorted(boundaries, key=lambda b: b[0])

    # preamble: 第一个边界之前
    first_line = sorted_boundaries[0][0]
    if first_line > 0:
        pre = slice_lines(0, first_line)
        if pre.strip():
            modules.append(CleaningModule(title="pre", text=pre))

    for i, (start, _, is_hr) in enumerate(sorted_boundaries):
        end = sorted_boundaries[i + 1][0] if i + 1 < len(sorted_boundaries) else line_count
        seg = slice_lines(start, end).strip()
        if not seg:
            continue
        if is_hr:
            # 分割线只是切分点, 不单独成模块 (纯 `---` 丢弃, 后续内容归入下一模块已由边界保证)
            if seg.strip("-\n \t") == "":
                continue
            modules.append(CleaningModule(title="---", text=seg))
            continue
        title = title_by_line.get(start, "pre") or "pre"
        modules.append(CleaningModule(title=title, text=seg))

    if not modules:
        return [CleaningModule(title="pre", text=source.strip())]

    return modules
