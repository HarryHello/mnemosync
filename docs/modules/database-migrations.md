# 数据库迁移 | Database Migrations

> **模块路径**: `src/persistence/migrations.py`

---

## 1. 概述

Mnemosync 用 `MigrationRunner` 做幂等 schema 迁移, 替代手写 `try/except ALTER TABLE` 模式。每个 SQLite store 在 `_init_schema` 中调用 runner, 已执行的迁移记录到 `_migrations` 表, 重启后自动跳过。

核心组件:

| 组件 | 说明 |
|------|------|
| `MigrationRunner` | 读取 `_migrations` 表, 按顺序执行未应用的迁移 |
| `add_column_if_missing()` | 幂等列添加: `ALTER TABLE ADD COLUMN`, 列已存在时静默跳过 |
| `_migrations` 表 | 跟踪已执行迁移名 (name + applied_at) |

---

## 2. 用法

每个 store 定义命名迁移列表, 在 `_init_schema` 末尾执行:

```python
from src.persistence.migrations import MigrationRunner, add_column_if_missing

class MyStore(SqliteStore):
    _MIGRATIONS: list[tuple[str, Callable]] = [
        ("001_add_column_x", add_column_if_missing("my_table", "x", "TEXT")),
        ("002_migrate_data", _custom_migrate_fn),
    ]

    @staticmethod
    async def _init_schema(db: aiosqlite.Connection) -> None:
        await db.execute("CREATE TABLE IF NOT EXISTS ...")
        await MigrationRunner(MyStore._MIGRATIONS).apply(db)
```

---

## 3. 迁移函数签名

每个迁移函数接收 `aiosqlite.Connection`, 返回 `Awaitable[None]`:

```python
MigrationFn = Callable[[aiosqlite.Connection], Awaitable[None]]
```

`add_column_if_missing(table, column, col_type)` 是内置便捷函数。自定义迁移可直接写 `async def` 函数, 在其中执行任意 DDL/DML。

---

## 4. 执行保证

- **幂等**: `_migrations` 表记录已执行迁移名, 重启后跳过
- **顺序执行**: 按列表顺序逐条执行
- **事务回滚**: 单条迁移失败时 `rollback()`, 不影响已成功的迁移
- **日志**: 每条迁移执行前 `logger.info`, 失败时 `logger.error`
