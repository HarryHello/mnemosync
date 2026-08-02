"""将旧版合并 conversation_turns 重载为逐说话者结构化事件.

默认仅预览；传 ``--apply`` 后先创建 SQLite 在线备份，再在一个事务中归档、
插入规范化事件并移除活动表里的 legacy 行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.persistence.conversation_store import ConversationEvent, build_event_fingerprint

SYSTEM_REMINDER_RE = re.compile(r"<system_reminder>(.*?)</system_reminder>", re.DOTALL)
USER_ID_RE = re.compile(r"User ID:\s*(\d+)")
NICKNAME_RE = re.compile(r"Nickname:\s*(.+)")
GROUP_NAME_RE = re.compile(r"Group name:\s*(.+)")
DATETIME_RE = re.compile(r"Current datetime:\s*(.+)")
CONTEXT_BLOCK_RE = re.compile(
    r"You are in a group chat.*?--- BEGIN CONTEXT---(.*?)--- END CONTEXT\s*---",
    re.DOTALL,
)
CONTEXT_LINE_RE = re.compile(r"\[(.+?)/(\d{2}:\d{2}:\d{2})\]:\s*(.*)")
MERGED_LINE_RE = re.compile(r"^(.+?)\s+\((\d+)\):\s*(.*)$")


class IdentityIndex:
    def __init__(self, db_path: Path) -> None:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        actors = connection.execute(
            "SELECT id, external_key, frontend, display_name FROM actors"
        ).fetchall()
        memberships = {
            row["actor_id"]: row["group_id"]
            for row in connection.execute(
                "SELECT actor_id, group_id FROM actor_group_memberships"
            ).fetchall()
        }
        connection.close()

        self.by_id = {row["id"]: dict(row) for row in actors}
        self.by_external = {
            (row["frontend"], row["external_key"]): dict(row) for row in actors
        }
        by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in actors:
            key = (row["frontend"], self.normalize(row["display_name"] or ""))
            by_name.setdefault(key, []).append(dict(row))
        self.by_name = {
            key: rows[0] for key, rows in by_name.items() if len(rows) == 1
        }
        self.memberships = memberships

    @staticmethod
    def normalize(name: str) -> str:
        return name.strip().casefold()

    def actor_by_external(self, external_key: str) -> dict[str, Any] | None:
        return self.by_external.get(("astrbot", external_key))

    def actor_by_name(self, display_name: str) -> dict[str, Any] | None:
        return self.by_name.get(("astrbot", self.normalize(display_name)))

    def actor_by_id(self, actor_id: str | None) -> dict[str, Any] | None:
        return self.by_id.get(actor_id or "")

    def effective_user_id(self, actor: dict[str, Any] | None) -> str | None:
        if actor is None:
            return None
        return self.memberships.get(actor["id"], actor["id"])


def parse_datetime(value: str | None, fallback: datetime) -> datetime:
    if value:
        cleaned = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
        try:
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed
        except ValueError:
            pass
    return fallback


def combine_clock(reference: datetime, clock: str) -> datetime:
    hour, minute, second = (int(part) for part in clock.split(":"))
    candidate = reference.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if candidate - reference > timedelta(hours=12):
        candidate -= timedelta(days=1)
    elif reference - candidate > timedelta(hours=12):
        candidate += timedelta(days=1)
    return candidate


def parse_context_lines(context: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for raw in context.strip().splitlines():
        line = raw.strip()
        match = CONTEXT_LINE_RE.match(line)
        if match:
            result.append((match.group(1).strip(), match.group(2), match.group(3).strip()))
        elif result and line:
            name, clock, content = result[-1]
            result[-1] = (name, clock, f"{content}\n{line}")
    return result


def token_count(content: str) -> int:
    return len(content) // 2 + 8


def make_event(
    *,
    role: str,
    content: str,
    ts: datetime,
    origin: str,
    request_id: str,
    actor: dict[str, Any] | None,
    identities: IdentityIndex,
    space_id: str | None,
    display_name: str | None = None,
    external_key: str | None = None,
    source_frontend: str | None = None,
    fingerprint_salt: str | None = None,
) -> ConversationEvent:
    event = ConversationEvent(
        role=role,
        content=content.strip(),
        token_count=token_count(content),
        source_frontend=source_frontend or (actor["frontend"] if actor else "astrbot"),
        ts=ts,
        actor_id=actor["id"] if actor else None,
        effective_user_id=identities.effective_user_id(actor),
        display_name_snapshot=(actor["display_name"] if actor else display_name),
        external_key_snapshot=(actor["external_key"] if actor else external_key),
        space_id=space_id,
        origin=origin,
        observed_at=ts,
        request_id=request_id,
    )
    if fingerprint_salt:
        material = f"{fingerprint_salt}\x1f{role}\x1f{content}"
        event.event_fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
    else:
        event.event_fingerprint = build_event_fingerprint(event)
    return event


def parse_raw_astrbot(
    row: sqlite3.Row,
    identities: IdentityIndex,
) -> tuple[list[ConversationEvent], dict[str, Any]]:
    content = row["content"]
    fallback = datetime.fromisoformat(row["ts"])
    user_id: str | None = None
    nickname: str | None = None
    group_name: str | None = row["space_id"]
    current_raw: str | None = None

    for reminder in SYSTEM_REMINDER_RE.finditer(content):
        body = reminder.group(1)
        user = USER_ID_RE.search(body)
        if not user:
            continue
        user_id = user.group(1)
        name = NICKNAME_RE.search(body)
        group = GROUP_NAME_RE.search(body)
        current = DATETIME_RE.search(body)
        nickname = name.group(1).strip() if name else user_id
        group_name = group.group(1).strip() if group else group_name
        current_raw = current.group(1).strip() if current else None
        break

    actor = identities.actor_by_external(user_id or "") or identities.actor_by_id(row["actor_id"])
    reference = parse_datetime(current_raw, fallback)
    request_id = f"legacy-{row['id']}"
    events: list[ConversationEvent] = []
    context_match = CONTEXT_BLOCK_RE.search(content)
    if context_match:
        for name, clock, text in parse_context_lines(context_match.group(1)):
            history_actor = identities.actor_by_name(name)
            events.append(make_event(
                role="user",
                content=text,
                ts=combine_clock(reference, clock),
                origin="history_snapshot",
                request_id=request_id,
                actor=history_actor,
                identities=identities,
                space_id=group_name,
                display_name=name,
            ))

    cleaned = CONTEXT_BLOCK_RE.sub("", content)
    cleaned = SYSTEM_REMINDER_RE.sub("", cleaned).strip()
    if cleaned:
        events.append(make_event(
            role="user",
            content=cleaned,
            ts=reference,
            origin="current",
            request_id=request_id,
            actor=actor,
            identities=identities,
            space_id=group_name,
            display_name=nickname,
            external_key=user_id,
        ))

    context = {
        "request_id": request_id,
        "space_id": group_name,
        "effective_user_id": identities.effective_user_id(actor),
    }
    return events, context


def parse_merged(
    row: sqlite3.Row,
    identities: IdentityIndex,
) -> tuple[list[ConversationEvent], dict[str, Any]] | None:
    parsed: list[tuple[str, str, str]] = []
    for raw in row["content"].splitlines():
        match = MERGED_LINE_RE.match(raw.strip())
        if match:
            parsed.append((match.group(1).strip(), match.group(2), match.group(3).strip()))
        elif parsed and raw.strip():
            name, external_key, content = parsed[-1]
            parsed[-1] = (name, external_key, f"{content}\n{raw.strip()}")
        elif raw.strip():
            return None
    if not parsed:
        return None

    base = datetime.fromisoformat(row["ts"])
    request_id = f"legacy-{row['id']}"
    events: list[ConversationEvent] = []
    for index, (name, external_key, content) in enumerate(parsed):
        actor = identities.actor_by_external(external_key)
        is_current = index == len(parsed) - 1
        # 旧合并文本没有平台时钟；用记录时间附近的秒级顺序保序。
        event_ts = base - timedelta(seconds=len(parsed) - index - 1)
        event = make_event(
            role="user",
            content=content,
            ts=event_ts,
            origin="current" if is_current else "history_snapshot",
            request_id=request_id,
            actor=actor,
            identities=identities,
            space_id=row["space_id"],
            display_name=name,
            external_key=external_key,
        )
        if not is_current:
            # 旧合并快照缺少事件时间；跨快照按身份+内容去重，避免重复历史膨胀。
            event.event_fingerprint = hashlib.sha256(
                f"legacy-history\x1f{row['space_id'] or ''}\x1f{external_key}\x1f{content}".encode()
            ).hexdigest()
        events.append(event)

    current_actor = identities.actor_by_external(parsed[-1][1])
    context = {
        "request_id": request_id,
        "space_id": row["space_id"],
        "effective_user_id": identities.effective_user_id(current_actor),
    }
    return events, context


def parse_plain(
    row: sqlite3.Row,
    identities: IdentityIndex,
) -> tuple[list[ConversationEvent], dict[str, Any]]:
    actor = identities.actor_by_id(row["actor_id"])
    request_id = f"legacy-{row['id']}"
    event = make_event(
        role="user",
        content=row["content"],
        ts=datetime.fromisoformat(row["ts"]),
        origin="current",
        request_id=request_id,
        actor=actor,
        identities=identities,
        space_id=row["space_id"],
        source_frontend=(actor["frontend"] if actor else row["source_frontend"]),
        fingerprint_salt=f"legacy-current-{row['id']}",
    )
    return [event], {
        "request_id": request_id,
        "space_id": row["space_id"],
        "effective_user_id": identities.effective_user_id(actor),
    }


def normalize_rows(
    rows: list[sqlite3.Row],
    identities: IdentityIndex,
) -> tuple[list[ConversationEvent], Counter[str], list[int]]:
    events: list[ConversationEvent] = []
    stats: Counter[str] = Counter()
    legacy_ids: list[int] = []
    previous_context: dict[str, Any] | None = None

    for row in rows:
        legacy_ids.append(row["id"])
        if row["role"] == "assistant":
            context = previous_context or {
                "request_id": f"legacy-{row['id']}",
                "space_id": row["space_id"],
                "effective_user_id": None,
            }
            events.append(ConversationEvent(
                role="assistant",
                content=row["content"],
                token_count=token_count(row["content"]),
                source_frontend="mnemosync",
                ts=datetime.fromisoformat(row["ts"]),
                effective_user_id=context["effective_user_id"],
                space_id=context["space_id"],
                origin="assistant",
                event_fingerprint=hashlib.sha256(
                    f"legacy-assistant-{row['id']}".encode()
                ).hexdigest(),
                observed_at=datetime.fromisoformat(row["ts"]),
                request_id=context["request_id"],
            ))
            stats["assistant"] += 1
            continue

        if SYSTEM_REMINDER_RE.search(row["content"]):
            parsed, previous_context = parse_raw_astrbot(row, identities)
            stats["raw_astrbot"] += 1
        else:
            merged = parse_merged(row, identities)
            if merged:
                parsed, previous_context = merged
                stats["merged"] += 1
            else:
                parsed, previous_context = parse_plain(row, identities)
                stats["plain"] += 1
        events.extend(parsed)

    stats["events"] = len(events)
    stats["unresolved"] = sum(
        1 for event in events if event.role == "user" and event.actor_id is None
    )
    return events, stats, legacy_ids


def backup_database(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"conversation-before-legacy-reload-{stamp}.db"
    source_db = sqlite3.connect(source)
    destination_db = sqlite3.connect(destination)
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()
    return destination


def apply_migration(
    db_path: Path,
    rows: list[sqlite3.Row],
    events: list[ConversationEvent],
    legacy_ids: list[int],
) -> tuple[int, int]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    inserted = 0
    duplicates = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns_legacy_archive (
                original_id INTEGER PRIMARY KEY,
                archived_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
        """)
        archived_at = datetime.now(UTC).isoformat()
        for row in rows:
            connection.execute(
                "INSERT OR REPLACE INTO conversation_turns_legacy_archive "
                "(original_id, archived_at, row_json) VALUES (?, ?, ?)",
                (row["id"], archived_at, json.dumps(dict(row), ensure_ascii=False)),
            )

        space_state: dict[str, tuple[int, str | None]] = {}
        for event in events:
            ts = (event.ts or datetime.now(UTC)).isoformat()
            observed_at = (event.observed_at or event.ts or datetime.now(UTC)).isoformat()
            sequence: int | None = None
            late_arrival = 0
            if event.space_id:
                state = space_state.get(event.space_id)
                if state is None:
                    state_row = connection.execute(
                        "SELECT COALESCE(MAX(committed_sequence), -1) + 1, MAX(ts) "
                        "FROM conversation_turns WHERE space_id = ? AND origin != 'legacy'",
                        (event.space_id,),
                    ).fetchone()
                    state = (int(state_row[0]), state_row[1])
                sequence, latest_ts = state
                late_arrival = int(bool(latest_ts and ts < latest_ts))

            cursor = connection.execute(
                "INSERT OR IGNORE INTO conversation_turns "
                "(role, content, ts, token_count, source_frontend, actor_id, space_id, "
                "external_event_id, committed_sequence, late_arrival, effective_user_id, "
                "display_name_snapshot, external_key_snapshot, origin, event_fingerprint, "
                "observed_at, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.role, event.content, ts, event.token_count,
                    event.source_frontend, event.actor_id, event.space_id,
                    event.external_event_id, sequence, late_arrival,
                    event.effective_user_id, event.display_name_snapshot,
                    event.external_key_snapshot, event.origin,
                    event.event_fingerprint, observed_at, event.request_id,
                ),
            )
            if cursor.rowcount == 0:
                duplicates += 1
                continue
            inserted += 1
            if event.space_id and sequence is not None:
                _, latest_ts = space_state.get(event.space_id, (sequence, None))
                newest = max(latest_ts, ts) if latest_ts else ts
                space_state[event.space_id] = (sequence + 1, newest)

        placeholders = ",".join("?" for _ in legacy_ids)
        connection.execute(
            f"DELETE FROM conversation_turns WHERE id IN ({placeholders})",
            legacy_ids,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted, duplicates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation-db", type=Path, default=Path("data/conversation.db"))
    parser.add_argument("--identity-db", type=Path, default=Path("data/identity.db"))
    parser.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(args.conversation_db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM conversation_turns WHERE COALESCE(origin, 'legacy') = 'legacy' "
        "ORDER BY id ASC"
    ).fetchall()
    connection.close()
    if not rows:
        print("没有 legacy 记录需要重载。")
        return

    identities = IdentityIndex(args.identity_db)
    events, stats, legacy_ids = normalize_rows(rows, identities)
    unique_fingerprints = {event.event_fingerprint for event in events}
    estimated_duplicates = len(events) - len(unique_fingerprints)

    print(f"legacy rows: {len(rows)}")
    print(f"normalized events: {len(events)}")
    print(f"estimated duplicate events: {estimated_duplicates}")
    print(f"unresolved user events: {stats['unresolved']}")
    print(
        "sources: "
        f"raw_astrbot={stats['raw_astrbot']}, merged={stats['merged']}, "
        f"plain={stats['plain']}, assistant={stats['assistant']}"
    )

    if not args.apply:
        print("预览完成；传 --apply 执行备份和重载。")
        return

    backup_path = backup_database(args.conversation_db, args.backup_dir)
    inserted, duplicates = apply_migration(
        args.conversation_db, rows, events, legacy_ids
    )
    print(f"backup: {backup_path}")
    print(f"inserted: {inserted}")
    print(f"duplicates skipped: {duplicates}")
    print(f"archived legacy rows: {len(legacy_ids)}")


if __name__ == "__main__":
    main()
