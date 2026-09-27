"""SQLite lưu tài khoản, job và từng item để có thể dừng/chạy tiếp bất cứ lúc nào."""
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    refresh_token TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,                 -- 'public' | 'account'
    source_account_id INTEGER,
    dest_account_id INTEGER NOT NULL,
    inputs TEXT NOT NULL,               -- JSON danh sách link/ID
    whole_drive INTEGER NOT NULL DEFAULT 0,
    dest_parent_id TEXT,                -- thư mục cha ở đích (NULL = My Drive)
    dest_root_name TEXT,                -- tên thư mục gốc sẽ tạo ở đích
    dest_root_id TEXT,                  -- ID thư mục gốc đã tạo
    auto_start INTEGER NOT NULL DEFAULT 1,
    roots_ready INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    status_message TEXT,
    wait_until REAL,
    dest_free_bytes INTEGER,
    dest_limit_bytes INTEGER,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    parent_item_id INTEGER NOT NULL DEFAULT 0,
    src_id TEXT NOT NULL,
    resource_key TEXT,
    name TEXT NOT NULL,
    mime TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    is_folder INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    listed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending|in_progress|done|failed|skipped
    maybe_done INTEGER NOT NULL DEFAULT 0,
    dest_id TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at REAL,
    UNIQUE(job_id, parent_item_id, src_id)
);
CREATE INDEX IF NOT EXISTS idx_items_job_status ON items(job_id, is_folder, status);
CREATE INDEX IF NOT EXISTS idx_items_job_listed ON items(job_id, is_folder, listed);

CREATE TABLE IF NOT EXISTS shared_perms (
    job_id INTEGER NOT NULL,
    src_id TEXT NOT NULL,
    perm_id TEXT NOT NULL,
    PRIMARY KEY(job_id, src_id)
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=60, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    config.ensure_dirs()
    with _write_lock:
        conn().executescript(SCHEMA)
        conn().commit()


@contextmanager
def tx():
    with _write_lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql: str, params=()):
    return conn().execute(sql, params).fetchall()


def q1(sql: str, params=()):
    return conn().execute(sql, params).fetchone()


def execute(sql: str, params=()):
    with tx() as c:
        return c.execute(sql, params)


def now() -> float:
    return time.time()


# ---------- accounts ----------

def upsert_account(email: str, display_name: str, refresh_token: str) -> int:
    with tx() as c:
        row = c.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
        if row:
            c.execute("UPDATE accounts SET refresh_token=?, display_name=? WHERE id=?",
                      (refresh_token, display_name, row["id"]))
            return row["id"]
        cur = c.execute(
            "INSERT INTO accounts(email, display_name, refresh_token, created_at) VALUES (?,?,?,?)",
            (email, display_name, refresh_token, now()))
        return cur.lastrowid


def get_account(account_id: int):
    return q1("SELECT * FROM accounts WHERE id=?", (account_id,))


def list_accounts():
    return q("SELECT id, email, display_name, created_at FROM accounts ORDER BY id")


def delete_account(account_id: int) -> None:
    execute("DELETE FROM accounts WHERE id=?", (account_id,))


# ---------- jobs ----------

def get_job(job_id: int):
    return q1("SELECT * FROM jobs WHERE id=?", (job_id,))


def set_job(job_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def job_stats(job_id: int) -> dict:
    rows = q(
        """SELECT is_folder, status, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes
           FROM items WHERE job_id=? GROUP BY is_folder, status""", (job_id,))
    s = {"files_total": 0, "folders_total": 0, "bytes_total": 0,
         "files_done": 0, "bytes_done": 0, "files_failed": 0, "files_skipped": 0,
         "files_pending": 0, "folders_done": 0, "folders_failed": 0}
    for r in rows:
        if r["is_folder"]:
            s["folders_total"] += r["n"]
            if r["status"] == "done":
                s["folders_done"] += r["n"]
            elif r["status"] == "failed":
                s["folders_failed"] += r["n"]
            continue
        s["files_total"] += r["n"]
        s["bytes_total"] += r["bytes"]
        if r["status"] == "done":
            s["files_done"] += r["n"]
            s["bytes_done"] += r["bytes"]
        elif r["status"] == "failed":
            s["files_failed"] += r["n"]
        elif r["status"] == "skipped":
            s["files_skipped"] += r["n"]
        else:
            s["files_pending"] += r["n"]
    return s
