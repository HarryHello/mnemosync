"""AstrBot QQ 机器人适配器.

将 AstrBot 的合并群聊快照拆成逐说话者事件，同时为模型编译一条带明确身份标签的
当前用户消息。历史事件只在身份无歧义时绑定 Actor，绝不归到当前请求者名下。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src.core.identity.plugin import (
    IdentityPlugin,
    NormalizedEvent,
    PluginPreprocessResult,
    PluginResult,
)

if TYPE_CHECKING:
    from src.core.identity.models import Actor, IdentityContext
    from src.persistence.identity_store import SqliteIdentityStore


class AstrBotPlugin(IdentityPlugin):
    name = "astrbot"
    description = "AstrBot QQ 适配器 — 逐说话者解析群聊快照并编译模型上下文"

    SYSTEM_REMINDER_RE = re.compile(
        r"<system_reminder>(.*?)</system_reminder>", re.DOTALL
    )
    USER_ID_RE = re.compile(r"User ID:\s*(\d+)")
    NICKNAME_RE = re.compile(r"Nickname:\s*(.+)")
    GROUP_NAME_RE = re.compile(r"Group name:\s*(.+)")
    DATETIME_RE = re.compile(r"Current datetime:\s*(.+)")
    CONTEXT_BLOCK_RE = re.compile(
        r"You are in a group chat.*?--- BEGIN CONTEXT---(.*?)--- END CONTEXT ---",
        re.DOTALL,
    )
    CONTEXT_LINE_RE = re.compile(r"\[(.+?)/(\d{2}:\d{2}:\d{2})\]:\s*(.*)")
    QQ_IN_NAME_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")

    async def extract(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: "SqliteIdentityStore",
    ) -> PluginResult | None:
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            sr = self._parse_system_reminder(content)
            if sr is None:
                continue
            actor = await store.find_or_create_actor(
                external_key=sr["user_id"],
                frontend=self.name,
                display_name=sr["nickname"],
            )
            return PluginResult(
                external_key=sr["user_id"],
                display_name=sr["nickname"],
                space_id=sr["group_name"] or config.get("space_id"),
                channel_type="group" if sr["group_name"] else "direct",
                metadata={
                    "frontend": self.name,
                    "actor_id": actor.id,
                    "datetime": sr["datetime"],
                },
            )
        return None

    async def preprocess(
        self,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        store: "SqliteIdentityStore",
        identity: "IdentityContext",
    ) -> PluginPreprocessResult:
        current_index = self._current_user_index(messages)
        if current_index is None:
            return PluginPreprocessResult(model_messages=messages, events=[])

        current_raw = messages[current_index].get("content", "")
        current_sr = self._parse_system_reminder(current_raw)
        reference_time = self._parse_current_datetime(
            current_sr.get("datetime") if current_sr else None
        )
        current_content = self._clean_current_content(current_raw)
        name_to_actor: dict[str, Actor] = {}
        if identity.actor is not None and identity.display_name:
            name_to_actor[self._normalize(identity.display_name)] = identity.actor

        events: list[NormalizedEvent] = []
        history_timestamps: list[datetime] = []
        context_match = self.CONTEXT_BLOCK_RE.search(current_raw)
        if context_match:
            for name, clock, text in self._parse_context_lines(context_match.group(1)):
                actor = await self._resolve_history_actor(name, name_to_actor, store)
                event_time = self._combine_clock(reference_time, clock)
                history_timestamps.append(event_time)
                events.append(NormalizedEvent(
                    role="user",
                    content=text,
                    source_frontend=self.name,
                    origin="history_snapshot",
                    source_timestamp=event_time,
                    actor_id=actor.id if actor else None,
                    effective_user_id=(
                        await store.get_effective_user_id(actor.id) if actor else None
                    ),
                    display_name=actor.display_name if actor else self._display_name(name),
                    external_key=actor.external_key if actor else self._external_key(name),
                    space_id=identity.space_id,
                ))

        # AstrBot 的 Current datetime 通常只有分钟精度。若同一分钟已有秒级历史，
        # 当前消息至少排在该分钟最后一条历史之后，避免 22:04:00 倒排到 22:04:43 前。
        current_time = reference_time
        same_minute = [
            timestamp for timestamp in history_timestamps
            if timestamp.replace(second=0, microsecond=0)
            == reference_time.replace(second=0, microsecond=0)
        ]
        if reference_time.second == 0 and same_minute:
            current_time = max(same_minute) + timedelta(microseconds=1)

        if current_content:
            events.append(NormalizedEvent(
                role="user",
                content=current_content,
                source_frontend=self.name,
                origin="current",
                source_timestamp=current_time,
                actor_id=identity.actor_id,
                effective_user_id=identity.effective_user_id,
                display_name=identity.display_name,
                external_key=identity.external_key,
                space_id=identity.space_id,
                external_event_id=identity.external_event_id,
            ))

        model_messages: list[dict[str, Any]] = []
        for index, msg in enumerate(messages):
            if index == current_index:
                compiled = self._compile_current_message(events)
                if compiled:
                    model_messages.append({**msg, "content": compiled})
                continue
            if msg.get("role") == "user":
                clean = self.SYSTEM_REMINDER_RE.sub("", msg.get("content", "")).strip()
                if clean:
                    model_messages.append({**msg, "content": clean})
            else:
                model_messages.append(msg)

        return PluginPreprocessResult(model_messages=model_messages, events=events)

    def _compile_current_message(self, events: list[NormalizedEvent]) -> str:
        """只编译本轮当前发言；历史快照由服务端事件流装填，避免重复上下文."""
        current = next((event for event in events if event.origin == "current"), None)
        if current is None:
            return ""
        identity = self._event_identity_label(current)
        return (
            f'<current_speaker identity="{identity}">\n'
            f"{current.content}\n"
            "</current_speaker>"
        )

    async def _resolve_history_actor(
        self,
        raw_name: str,
        cache: dict[str, "Actor"],
        store: "SqliteIdentityStore",
    ) -> "Actor | None":
        name = self._display_name(raw_name)
        key = self._normalize(name)
        cached = cache.get(key)
        if cached is not None:
            return cached
        qq = self._external_key(raw_name)
        if qq:
            actor = await store.find_or_create_actor(qq, self.name, name)
        else:
            actor = await store.find_unique_actor_by_display_name(self.name, name)
        if actor is not None:
            cache[key] = actor
        return actor

    def _parse_system_reminder(self, content: str) -> dict[str, str | None] | None:
        for match in self.SYSTEM_REMINDER_RE.finditer(content):
            body = match.group(1)
            user = self.USER_ID_RE.search(body)
            if not user:
                continue
            name = self.NICKNAME_RE.search(body)
            group = self.GROUP_NAME_RE.search(body)
            current = self.DATETIME_RE.search(body)
            return {
                "user_id": user.group(1),
                "nickname": name.group(1).strip() if name else user.group(1),
                "group_name": group.group(1).strip() if group else None,
                "datetime": current.group(1).strip() if current else None,
            }
        return None

    def _parse_context_lines(self, context: str) -> list[tuple[str, str, str]]:
        lines: list[tuple[str, str, str]] = []
        for raw in context.strip().split("\n"):
            line = raw.strip()
            match = self.CONTEXT_LINE_RE.match(line)
            if match:
                lines.append((
                    match.group(1).strip(),
                    match.group(2),
                    match.group(3).strip(),
                ))
            elif lines and line:
                name, clock, text = lines[-1]
                lines[-1] = (name, clock, f"{text}\n{line}")
        return lines

    def _clean_current_content(self, content: str) -> str:
        content = self.CONTEXT_BLOCK_RE.sub("", content)
        return self.SYSTEM_REMINDER_RE.sub("", content).strip()

    @staticmethod
    def _current_user_index(messages: list[dict[str, Any]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index
        return None

    @staticmethod
    def _normalize(name: str) -> str:
        return name.strip().casefold()

    def _display_name(self, raw_name: str) -> str:
        match = self.QQ_IN_NAME_RE.match(raw_name.strip())
        return (match.group(1).strip() if match else raw_name.strip()) or "unknown"

    def _external_key(self, raw_name: str) -> str | None:
        match = self.QQ_IN_NAME_RE.match(raw_name.strip())
        return match.group(2) if match else None

    @staticmethod
    def _parse_current_datetime(value: str | None) -> datetime:
        if value:
            cleaned = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
            try:
                parsed = datetime.fromisoformat(cleaned)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                return parsed
            except ValueError:
                pass
        return datetime.now(ZoneInfo("Asia/Shanghai"))

    @staticmethod
    def _combine_clock(reference: datetime, clock: str) -> datetime:
        hour, minute, second = (int(part) for part in clock.split(":"))
        candidate = reference.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if candidate - reference > timedelta(hours=12):
            candidate -= timedelta(days=1)
        elif reference - candidate > timedelta(hours=12):
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _event_identity_label(event: NormalizedEvent) -> str:
        name = event.display_name or "unknown"
        return f"{name} | QQ {event.external_key}" if event.external_key else name
