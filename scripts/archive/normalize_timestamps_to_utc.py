"""将 conversation_turns.ts 统一为 UTC 文本，使 SQLite 字符串排序等价于时间排序。"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_ISO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2}|Z)?$"
)


def _to_utc_iso(value: str) -> str | None:
    match = _ISO_RE.match(value)
    if not match:
        return None
    try:
        dt = datetime.fromisoformat(match.group(1) + (match.group(2) or ""))
        return dt.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        return None


def main() -> None:
    db_path = Path("data/conversation.db")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, ts, observed_at FROM conversation_turns"
    ).fetchall()

    changed_ts = 0
    changed_obs = 0
    for row in rows:
        updates: list[str] = []
        params: list = []
        org_ts = row["ts"]
        if org_ts and "+" in org_ts:
            utc = _to_utc_iso(org_ts)
            if utc and utc != org_ts:
                updates.append("ts = ?")
                params.append(utc)
                changed_ts += 1
        org_obs = row["observed_at"]
        if org_obs and "+" in org_obs:
            utc = _to_utc_iso(org_obs)
            if utc and utc != org_obs:
                updates.append("observed_at = ?")
                params.append(utc)
                changed_obs += 1
        if updates:
            params.append(row["id"])
            connection.execute(
                f"UPDATE conversation_turns SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    connection.commit()
    connection.close()
    print(f"ts:     {changed_ts} 行已修正")
    print(f"observed_at: {changed_obs} 行已修正")


if __name__ == "__main__":
    main()
