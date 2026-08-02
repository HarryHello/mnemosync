"""从 HTTP 日志恢复旧版合并群聊记录中的精确 AstrBot 时间戳."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.persistence.conversation_store import ConversationEvent, build_event_fingerprint

CONTEXT_RE = re.compile(
    r"You are in a group chat.*?--- BEGIN CONTEXT---(.*?)--- END CONTEXT\s*---",
    re.DOTALL,
)
CONTEXT_LINE_RE = re.compile(r"\[(.+?)/(\d{2}:\d{2}:\d{2})\]:\s*(.*)")
SYSTEM_REMINDER_RE = re.compile(r"<system_reminder>(.*?)</system_reminder>", re.DOTALL)
USER_ID_RE = re.compile(r"User ID:\s*(\d+)")
NICKNAME_RE = re.compile(r"Nickname:\s*(.+)")
GROUP_RE = re.compile(r"Group name:\s*(.+)")
DATETIME_RE = re.compile(r"Current datetime:\s*(.+)")


def _user_content(request_body: str) -> str:
    data = json.loads(request_body)
    users = [message for message in data.get("messages", []) if message.get("role") == "user"]
    if not users:
        return ""
    content = users[-1].get("content", "")
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def _parse_reference(value: str | None, fallback: datetime) -> datetime:
    if value:
        cleaned = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
        try:
            parsed = datetime.fromisoformat(cleaned)
            return parsed.replace(tzinfo=parsed.tzinfo or ZoneInfo("Asia/Shanghai"))
        except ValueError:
            pass
    return fallback


def _combine_clock(reference: datetime, clock: str) -> datetime:
    hour, minute, second = (int(part) for part in clock.split(":"))
    candidate = reference.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if candidate - reference > timedelta(hours=12):
        candidate -= timedelta(days=1)
    elif reference - candidate > timedelta(hours=12):
        candidate += timedelta(days=1)
    return candidate


def _context_lines(context: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for raw in context.strip().splitlines():
        line = raw.strip()
        match = CONTEXT_LINE_RE.match(line)
        if match:
            result.append((match.group(1).strip(), match.group(2), match.group(3).strip()))
        elif result and line:
            name, clock, text = result[-1]
            result[-1] = (name, clock, f"{text}\n{line}")
    return result


def _metadata(content: str, fallback: datetime) -> tuple[str | None, str | None, str | None, datetime]:
    user_id = nickname = group_name = current_raw = None
    for match in SYSTEM_REMINDER_RE.finditer(content):
        body = match.group(1)
        user = USER_ID_RE.search(body)
        if not user:
            continue
        user_id = user.group(1)
        name = NICKNAME_RE.search(body)
        group = GROUP_RE.search(body)
        current = DATETIME_RE.search(body)
        nickname = name.group(1).strip() if name else user_id
        group_name = group.group(1).strip() if group else None
        current_raw = current.group(1).strip() if current else None
        break
    return user_id, nickname, group_name, _parse_reference(current_raw, fallback)


def _events_from_log(
    request_body: str,
    request_id: str,
    fallback: datetime,
    by_external: dict[str, dict],
    by_name: dict[str, dict],
    memberships: dict[str, str],
) -> list[ConversationEvent]:
    content = _user_content(request_body)
    context_match = CONTEXT_RE.search(content)
    if not context_match:
        return []
    user_id, nickname, space_id, reference = _metadata(content, fallback)
    events: list[ConversationEvent] = []

    history_timestamps: list[datetime] = []
    for name, clock, text in _context_lines(context_match.group(1)):
        actor = by_name.get(name.strip().casefold())
        event_ts = _combine_clock(reference, clock)
        history_timestamps.append(event_ts)
        event = ConversationEvent(
            role="user", content=text, token_count=len(text) // 2 + 8,
            source_frontend="astrbot", ts=event_ts,
            actor_id=actor["id"] if actor else None,
            effective_user_id=memberships.get(actor["id"], actor["id"]) if actor else None,
            display_name_snapshot=actor["display_name"] if actor else name,
            external_key_snapshot=actor["external_key"] if actor else None,
            space_id=space_id, origin="history_snapshot",
            observed_at=fallback, request_id=request_id,
        )
        event.event_fingerprint = build_event_fingerprint(event)
        events.append(event)

    cleaned = CONTEXT_RE.sub("", content)
    cleaned = SYSTEM_REMINDER_RE.sub("", cleaned).strip()
    if cleaned:
        actor = by_external.get(user_id or "")
        current_time = reference
        same_minute = [
            timestamp for timestamp in history_timestamps
            if timestamp.replace(second=0, microsecond=0)
            == reference.replace(second=0, microsecond=0)
        ]
        if reference.second == 0 and same_minute:
            current_time = max(same_minute) + timedelta(microseconds=1)
        event = ConversationEvent(
            role="user", content=cleaned, token_count=len(cleaned) // 2 + 8,
            source_frontend="astrbot", ts=current_time,
            actor_id=actor["id"] if actor else None,
            effective_user_id=memberships.get(actor["id"], actor["id"]) if actor else None,
            display_name_snapshot=actor["display_name"] if actor else nickname,
            external_key_snapshot=user_id, space_id=space_id, origin="current",
            observed_at=fallback, request_id=request_id,
        )
        # 当前事件不与快照去重；原请求可能没有平台 message id。
        events.append(event)
    return events


def _load_identity(db_path: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    actors = [dict(row) for row in connection.execute(
        "SELECT id, external_key, frontend, display_name FROM actors"
    ).fetchall()]
    memberships = {
        row["actor_id"]: row["group_id"]
        for row in connection.execute(
            "SELECT actor_id, group_id FROM actor_group_memberships"
        ).fetchall()
    }
    connection.close()
    by_external = {actor["external_key"]: actor for actor in actors}
    by_name = {
        actor["display_name"].strip().casefold(): actor
        for actor in actors if actor["display_name"]
    }
    return by_external, by_name, memberships


def _match_groups(conversation_db: Path, http_db: Path) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    conversation = sqlite3.connect(conversation_db)
    conversation.row_factory = sqlite3.Row
    groups = conversation.execute(
        "SELECT request_id, MAX(ts) AS current_ts FROM conversation_turns "
        "WHERE request_id IN ('legacy-57','legacy-59','legacy-63','legacy-67','legacy-69') "
        "GROUP BY request_id ORDER BY current_ts"
    ).fetchall()
    conversation.close()

    http = sqlite3.connect(http_db)
    http.row_factory = sqlite3.Row
    logs = http.execute(
        "SELECT id, created_at, request_body FROM http_logs "
        "WHERE path='/v1/chat/completions' AND client_ip='100.99.179.7' ORDER BY created_at"
    ).fetchall()
    http.close()

    matched: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    for group in groups:
        target = datetime.fromisoformat(group["current_ts"]).astimezone(UTC)
        candidates = []
        for log in logs:
            content = _user_content(log["request_body"])
            if not CONTEXT_RE.search(content):
                continue
            log_ts = datetime.fromisoformat(log["created_at"]).replace(tzinfo=UTC)
            candidates.append((abs((log_ts - target).total_seconds()), log))
        if candidates:
            distance, log = min(candidates, key=lambda item: item[0])
            if distance <= 180:
                matched.append((group, log))
    return matched


def _insert_event(connection: sqlite3.Connection, event: ConversationEvent) -> int:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO conversation_turns "
        "(role,content,ts,token_count,source_frontend,actor_id,space_id,external_event_id,"
        "committed_sequence,late_arrival,effective_user_id,display_name_snapshot,"
        "external_key_snapshot,origin,event_fingerprint,observed_at,request_id) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,0,?,?,?,?,?,?,?)",
        (
            event.role, event.content, event.ts.isoformat(), event.token_count,
            event.source_frontend, event.actor_id, event.space_id, event.external_event_id,
            event.effective_user_id, event.display_name_snapshot,
            event.external_key_snapshot, event.origin, event.event_fingerprint,
            event.observed_at.isoformat(), event.request_id,
        ),
    )
    return cursor.rowcount or 0


def _resequence_space(connection: sqlite3.Connection, space_id: str) -> None:
    rows = connection.execute(
        "SELECT id, ts FROM conversation_turns WHERE space_id=? "
        "ORDER BY julianday(ts) ASC, id ASC",
        (space_id,),
    ).fetchall()
    for sequence, row in enumerate(rows):
        connection.execute(
            "UPDATE conversation_turns SET committed_sequence=?, late_arrival=0 WHERE id=?",
            (sequence, row["id"]),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conversation_db = Path("data/conversation.db")
    identity_db = Path("data/identity.db")
    http_db = Path("data/http_logs.db")

    by_external, by_name, memberships = _load_identity(identity_db)
    matches = _match_groups(conversation_db, http_db)
    reconstructed: list[tuple[str, list[ConversationEvent], int]] = []
    for group, log in matches:
        observed = datetime.fromisoformat(log["created_at"]).replace(tzinfo=UTC)
        events = _events_from_log(
            log["request_body"], group["request_id"], observed,
            by_external, by_name, memberships,
        )
        reconstructed.append((group["request_id"], events, log["id"]))
        print(f"\n{group['request_id']} <- HTTP #{log['id']}")
        for event in events:
            print(
                f"  {event.origin:17} {event.ts.isoformat()} "
                f"{event.display_name_snapshot or '未知'}: {event.content[:80]}"
            )

    print(f"\n可精确重建 {len(reconstructed)} 组")
    if not args.apply:
        print("传 --apply 执行")
        return

    connection = sqlite3.connect(conversation_db)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        inserted = removed = 0
        spaces: set[str] = set()
        for request_id, events, _ in reconstructed:
            # 只替换用户事件，保留已迁移的 assistant 回复。
            cursor = connection.execute(
                "DELETE FROM conversation_turns WHERE request_id=? AND role='user'",
                (request_id,),
            )
            removed += cursor.rowcount or 0
            for event in events:
                inserted += _insert_event(connection, event)
                if event.space_id:
                    spaces.add(event.space_id)
        for space_id in spaces:
            _resequence_space(connection, space_id)
        connection.commit()
        print(f"删除旧用户事件 {removed} 条，插入精确时间事件 {inserted} 条")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
