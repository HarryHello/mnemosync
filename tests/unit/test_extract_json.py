"""_extract_json 覆盖: 代码围栏 / 嵌套对象 / 字符串内大括号 / 尾随文本."""

from src.core.agents.factory import _extract_json


def test_fenced_nested_object_preserved():
    """回归: 记忆分析输出的嵌套 JSON (new_memories 数组含对象) 曾被逐行启发式截断."""
    content = """没有找到已有记忆。

```json
{
  "new_memories": [
    {
      "content": "用户希望被称为'马达'",
      "memory_type": "PERMANENT",
      "importance": 1.0
    }
  ],
  "decay_evaluations": []
}
```"""
    result = _extract_json(content)
    assert result is not None
    assert len(result["new_memories"]) == 1
    assert result["new_memories"][0]["content"] == "用户希望被称为'马达'"
    assert result["decay_evaluations"] == []


def test_bare_json():
    result = _extract_json('{"new_memories":[{"content":"x"}],"decay_evaluations":[]}')
    assert result == {"new_memories": [{"content": "x"}], "decay_evaluations": []}


def test_json_with_leading_and_trailing_text():
    result = _extract_json('前言 {"a": {"b": 1}} 后言')
    assert result == {"a": {"b": 1}}


def test_string_containing_closing_brace():
    result = _extract_json('{"content": "包含 } 的字符串", "n": 1}')
    assert result == {"content": "包含 } 的字符串", "n": 1}


def test_escaped_quotes_in_string():
    result = _extract_json(r'{"content": "she said \"hi\"", "n": 2}')
    assert result == {"content": 'she said "hi"', "n": 2}


def test_missing_json_returns_none():
    assert _extract_json("模型只说了一段自然语言, 没输出 JSON") is None


def test_unbalanced_returns_none():
    assert _extract_json('{"a": 1, "b": {') is None


def test_fenced_without_language_tag():
    content = """```
{"new_memories": [], "decay_evaluations": []}
```"""
    result = _extract_json(content)
    assert result == {"new_memories": [], "decay_evaluations": []}
