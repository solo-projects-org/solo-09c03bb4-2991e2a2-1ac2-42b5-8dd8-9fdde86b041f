"""SQLite 连接管理与建表。

表结构
------
lamps : 灯明细（改报只读写这张表）
    - revision 为乐观锁/修订号，每次成功改报 +1
    - lamp_id + revision 唯一，配合 (lamp_id, content_hash) 实现原样重送幂等
model_curve_meta / model_curve_rows : KM 汇总（查曲线只读这两张表）
    - 每次改报在同一个 IMMEDIATE 事务内随明细一起替换重算
"""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS lamps (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lamp_id            TEXT    NOT NULL,
    revision           INTEGER NOT NULL,
    model              TEXT    NOT NULL,
    entry_age_days     INTEGER NOT NULL,
    exit_age_days      INTEGER NOT NULL,
    outcome            TEXT    NOT NULL CHECK (outcome IN ('first_failure', 'last_known_working')),
    content_hash       TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (lamp_id, revision)
);
-- 说明：历史修订全部保留用于追溯，但只有每灯的最新修订参与汇总统计
-- （services._rebuild_model 用窗口式子查询取 max(revision)）。

CREATE TABLE IF NOT EXISTS model_curve_meta (
    model          TEXT PRIMARY KEY,
    lamp_count     INTEGER NOT NULL,
    failure_count  INTEGER NOT NULL,
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS model_curve_rows (
    model          TEXT    NOT NULL,
    t_days         INTEGER NOT NULL,
    at_risk        INTEGER NOT NULL,
    failures       INTEGER NOT NULL,
    survival       REAL    NOT NULL,
    PRIMARY KEY (model, t_days)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL 让写事务与只读查询互不阻塞；外键/约束保持默认开启
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(immediate: bool = False):
    """事务上下文。

    immediate=True 时以 BEGIN IMMEDIATE 开启，立即取得 RESERVED 写锁，
    使并发改报按实际拿到写锁（即提交）的顺序串行判定修订号，
    避免两个并发事务同时读到同一旧 revision 后双双通过校验。
    写失败时回滚，已提交的旧汇总保持可读。
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
