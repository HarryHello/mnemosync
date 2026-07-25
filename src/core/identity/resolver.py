"""身份解析器 (v0.3.0).

从请求中解析身份信息，按 API Key 绑定的策略执行。
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from src.core.identity.models import IdentityContext, StrategyType
from src.infra.forwarder.multi import MultiForwarder
from src.infra.llm_service.models import ModelType

if TYPE_CHECKING:
    from src.persistence.identity_store import SqliteIdentityStore

logger = logging.getLogger(__name__)

# Regex 策略的默认搜索范围
SEARCH_SYSTEM_FIRST = "system_or_first_user"
SEARCH_ALL = "all"
SEARCH_SYSTEM = "system"


class IdentityResolver:
    """从请求中解析身份，按 API Key 绑定的策略执行。"""

    def __init__(self, store: "SqliteIdentityStore", forwarder: MultiForwarder | None = None):
        self.store = store
        self.forwarder = forwarder

    async def resolve(
        self,
        request_user: str | None,
        messages: list[dict],
        strategy_config: dict | None,  # 来自 API Key 绑定的策略
        strategy_type: str | None,
        strategy_name: str | None,
    ) -> IdentityContext:
        """从请求中解析身份。

        Args:
            request_user: request.user 字段 (OpenAI 标准)
            messages: 完整的 messages 列表
            strategy_config: 策略的 config dict
            strategy_type: 策略类型 (direct / api_key_bound / regex / llm)
            strategy_name: 策略名称 (调试用)

        Returns:
            IdentityContext
        """
        if strategy_type is None or strategy_config is None:
            # 无策略 → 非归属模式: 不创建 Actor, 不读写私有记忆
            return IdentityContext(
                actor_id=None,
                actor=None,
                effective_user_id=None,  # None = 非归属, 不映射到任何用户桶
                frontend=None,
                external_key=None,
                display_name=None,
                space_id=None,
                channel_type=None,
                strategy_name=None,
            )

        try:
            match strategy_type:
                case StrategyType.DIRECT.value:
                    return await self._resolve_direct(request_user, strategy_config, strategy_name)
                case StrategyType.API_KEY_BOUND.value:
                    return await self._resolve_api_key_bound(strategy_config, strategy_name)
                case StrategyType.REGEX.value:
                    return await self._resolve_regex(messages, strategy_config, strategy_name)
                case StrategyType.LLM.value:
                    return await self._resolve_llm(messages, strategy_config, strategy_name)
                case _:
                    logger.warning("未知策略类型: %s", strategy_type)
                    return self._unattributed()
        except Exception as e:
            logger.warning("身份解析失败: %s", e)
            return self._unattributed()

    def _unattributed(self) -> IdentityContext:
        return IdentityContext(
            actor_id=None,
            actor=None,
            effective_user_id=None,  # None = 非归属模式
            frontend=None,
            external_key=None,
            display_name=None,
            space_id=None,
            channel_type=None,
            strategy_name=None,
        )

    async def _resolve_direct(
        self, request_user: str | None, config: dict, strategy_name: str | None,
    ) -> IdentityContext:
        """使用 request.user 字段。"""
        if not request_user:
            return self._unattributed()
        frontend = config.get("frontend", "direct")
        actor = await self.store.find_or_create_actor(
            external_key=request_user,
            frontend=frontend,
            display_name=request_user,
        )
        effective_id = await self.store.get_effective_user_id(actor.id)
        return IdentityContext(
            actor_id=actor.id,
            actor=actor,
            effective_user_id=effective_id,
            frontend=frontend,
            external_key=request_user,
            display_name=request_user,
            space_id=None,
            channel_type=None,
            strategy_name=strategy_name,
        )

    async def _resolve_api_key_bound(
        self, config: dict, strategy_name: str | None,
    ) -> IdentityContext:
        """Key 即身份，固定 external_key。"""
        external_key = config.get("external_key", "api-key-bound")
        frontend = config.get("frontend", "api_key_bound")
        display_name = config.get("display_name")
        actor = await self.store.find_or_create_actor(
            external_key=external_key,
            frontend=frontend,
            display_name=display_name,
        )
        effective_id = await self.store.get_effective_user_id(actor.id)
        space_id = config.get("space_id")
        channel_type = config.get("channel_type", "direct")
        return IdentityContext(
            actor_id=actor.id,
            actor=actor,
            effective_user_id=effective_id,
            frontend=frontend,
            external_key=external_key,
            display_name=display_name or external_key,
            space_id=space_id,
            channel_type=channel_type,
            strategy_name=strategy_name,
        )

    async def _resolve_regex(
        self, messages: list[dict], config: dict, strategy_name: str | None,
    ) -> IdentityContext:
        """从消息内容中正则提取身份。"""
        frontend = config.get("frontend", "regex")
        search_in = config.get("search_in", SEARCH_SYSTEM_FIRST)
        text = self._extract_search_text(messages, search_in)

        external_key = self._match_pattern(text, config.get("actor_pattern"))
        if not external_key:
            return self._unattributed()

        display_name = self._match_pattern(text, config.get("name_pattern"))
        space_id = self._match_pattern(text, config.get("space_pattern"))

        actor = await self.store.find_or_create_actor(
            external_key=external_key,
            frontend=frontend,
            display_name=display_name or external_key,
        )
        effective_id = await self.store.get_effective_user_id(actor.id)
        return IdentityContext(
            actor_id=actor.id,
            actor=actor,
            effective_user_id=effective_id,
            frontend=frontend,
            external_key=external_key,
            display_name=display_name or external_key,
            space_id=space_id,
            channel_type="group" if space_id else "direct",
            strategy_name=strategy_name,
        )

    async def _resolve_llm(
        self, messages: list[dict], config: dict, strategy_name: str | None,
    ) -> IdentityContext:
        """用辅助模型从消息内容中提取身份。"""
        if self.forwarder is None:
            return self._unattributed()
        frontend = config.get("frontend", "llm")
        prompt_template = config.get(
            "prompt_template",
            "从以下对话中识别发言者身份。返回 JSON：{\"actor_id\":\"...\",\"actor_name\":\"...\",\"space_id\":\"...\"}\n\n{content}"
        )
        text = self._extract_search_text(messages, SEARCH_ALL)
        filled = prompt_template.replace("{content}", text)
        try:
            resp = await self.forwarder.chat(
                ModelType.ASSIST,
                messages=[{"role": "user", "content": filled}],
                temperature=0.1,
                response_format={"type": "json_object"},
                extra_body={"enable_thinking": False},
            )
            raw = resp["choices"][0]["message"]["content"].strip()
            if " thinking" in raw:
                raw = raw.split(" response")[-1].strip()
            data = json.loads(raw)
            external_key = data.get("actor_id") or data.get("actor_key")
            if not external_key:
                return self._unattributed()
            display_name = data.get("actor_name")
            space_id = data.get("space_id")
            actor = await self.store.find_or_create_actor(
                external_key=external_key,
                frontend=frontend,
                display_name=display_name or external_key,
            )
            effective_id = await self.store.get_effective_user_id(actor.id)
            return IdentityContext(
                actor_id=actor.id,
                actor=actor,
                effective_user_id=effective_id,
                frontend=frontend,
                external_key=external_key,
                display_name=display_name or external_key,
                space_id=space_id,
                channel_type="group" if space_id else "direct",
                strategy_name=strategy_name,
            )
        except Exception as e:
            logger.warning("LLM 身份解析失败: %s", e)
            return self._unattributed()

    # ============ 辅助方法 ============

    @staticmethod
    def _extract_search_text(messages: list[dict], search_in: str) -> str:
        """从 messages 中提取搜索文本。"""
        if search_in == SEARCH_SYSTEM:
            for msg in messages:
                if msg.get("role") == "system" and msg.get("content"):
                    return str(msg.get("content", ""))
            return ""
        if search_in == SEARCH_SYSTEM_FIRST:
            for msg in messages:
                if msg.get("role") == "system" and msg.get("content"):
                    return str(msg.get("content", ""))
            for msg in messages:
                if msg.get("role") == "user" and msg.get("content"):
                    return str(msg.get("content", ""))
            return ""
        # SEARCH_ALL
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if content:
                parts.append(f"{msg.get('role', '?')}: {content}")
        return "\n".join(parts)

    @staticmethod
    def _match_pattern(text: str, pattern: str | None) -> str | None:
        """用正则提取第一个匹配值。"""
        if not pattern or not text:
            return None
        try:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
            return None
        except re.error as e:
            logger.warning("正则错误 %s: %s", pattern, e)
            return None
