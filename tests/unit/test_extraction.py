"""infra.extraction 消息提取单元测试 (技术债 T7).

覆盖: 最新 user 消息提取 / 全部 user 消息 / 最长历史前缀匹配去重 /
消息相等判定 (仅 role/content/name, 忽略额外字段).
"""

from __future__ import annotations

from src.infra.extraction import (
    extract_all_user_messages,
    extract_latest_user_message,
    extract_new_messages,
)


class TestExtractLatestUserMessage:
    def test_last_user_message(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        assert extract_latest_user_message(messages) == {"role": "user", "content": "c"}

    def test_ignores_non_user_tail(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        assert extract_latest_user_message(messages) == {"role": "user", "content": "a"}

    def test_no_user_message_returns_none(self) -> None:
        assert extract_latest_user_message([{"role": "system", "content": "x"}]) is None

    def test_empty_messages_returns_none(self) -> None:
        assert extract_latest_user_message([]) is None


class TestExtractAllUserMessages:
    def test_returns_all_in_order(self) -> None:
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        assert extract_all_user_messages(messages) == [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "c"},
        ]

    def test_no_user_messages(self) -> None:
        assert extract_all_user_messages([{"role": "assistant", "content": "b"}]) == []

    def test_empty(self) -> None:
        assert extract_all_user_messages([]) == []


class TestExtractNewMessages:
    def test_entire_history_matched_no_new(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        server = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        assert extract_new_messages(messages, server) == []

    def test_tail_only_is_new(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        server = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        assert extract_new_messages(messages, server) == [{"role": "user", "content": "c"}]

    def test_empty_server_history_all_new(self) -> None:
        messages = [{"role": "user", "content": "a"}]
        assert extract_new_messages(messages, []) == messages

    def test_empty_messages(self) -> None:
        assert extract_new_messages([], [{"role": "user", "content": "a"}]) == []

    def test_matched_middle_becomes_new_after_prefix(self) -> None:
        # 历史有重复内容: 前缀匹配到 idx=0 后, 后续相同内容按顺序推进
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        server = [{"role": "user", "content": "hello"}]
        assert extract_new_messages(messages, server) == [{"role": "user", "content": "hello"}]

    def test_unmatched_history_skipped_forward(self) -> None:
        # 前端缺了历史中间段: 服务器按序扫过未匹配历史, 仍能对齐后续
        messages = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "c"},
        ]
        server = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},  # 前端缺失
            {"role": "user", "content": "c"},
        ]
        assert extract_new_messages(messages, server) == []

    def test_equality_ignores_extra_fields(self) -> None:
        # 相等判定只看 role/content/name — 客户端额外字段不阻断匹配
        messages = [
            {"role": "user", "content": "a", "timestamp": 111, "extra": {"x": 1}},
        ]
        server = [{"role": "user", "content": "a"}]
        assert extract_new_messages(messages, server) == []

    def test_different_content_is_new(self) -> None:
        messages = [{"role": "user", "content": "edit", "name": "alice"}]
        server = [{"role": "user", "content": "original", "name": "alice"}]
        assert extract_new_messages(messages, server) == messages

    def test_name_field_disambiguates(self) -> None:
        messages = [
            {"role": "user", "content": "hi", "name": "alice"},
            {"role": "user", "content": "hi", "name": "bob"},
        ]
        server = [{"role": "user", "content": "hi", "name": "alice"}]
        assert extract_new_messages(messages, server) == [
            {"role": "user", "content": "hi", "name": "bob"}
        ]
