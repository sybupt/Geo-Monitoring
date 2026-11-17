"""SQLite 存取层。所有表结构集中在此，避免上层直接拼 SQL。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    brand       TEXT NOT NULL,
    price       REAL NOT NULL,
    text        TEXT NOT NULL,
    gqs         REAL NOT NULL,
    geo_json    TEXT NOT NULL,
    popularity  REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    member_name   TEXT NOT NULL DEFAULT '',
    promote_type  TEXT NOT NULL DEFAULT 'brand',
    promote_name  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    persona     TEXT NOT NULL,
    price_band  TEXT NOT NULL,
    preferred   TEXT NOT NULL,
    brands      TEXT NOT NULL DEFAULT '[]',
    activity    REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS interactions (
    user_id   TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    event     TEXT NOT NULL DEFAULT 'click',
    ts        INTEGER NOT NULL,
    label     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, item_id, ts)
);

CREATE TABLE IF NOT EXISTS rewrites (
    item_id    TEXT PRIMARY KEY,
    before     TEXT NOT NULL,
    after      TEXT NOT NULL,
    before_gqs REAL NOT NULL,
    after_gqs  REAL NOT NULL,
    payload    TEXT NOT NULL,
    engine     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    name       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inter_user ON interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_inter_item ON interactions(item_id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    with connect() as conn:
        if reset:
            for t in ("items", "users", "interactions", "rewrites", "metrics"):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(SCHEMA)
        migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """老库补列：items 增加「所属企业 / 推广对象」两组字段。

    SQLite 的 ALTER TABLE ADD COLUMN 只加不改，历史数据默认成
    promote_type='brand'、空企业名，读取时由上层回退，不会破坏已有数据。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for col, ddl in {
        "member_name": "TEXT NOT NULL DEFAULT ''",
        "promote_type": "TEXT NOT NULL DEFAULT 'brand'",
        "promote_name": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")


def table_counts() -> dict[str, int]:
    with connect() as conn:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("items", "users", "interactions", "rewrites", "metrics")
        }


def insert_items(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO items (item_id,title,category,brand,price,text,gqs,geo_json,popularity,created_at)"
            " VALUES (:item_id,:title,:category,:brand,:price,:text,:gqs,:geo_json,:popularity,:created_at)",
            list(rows),
        )


def insert_users(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO users (user_id,name,persona,price_band,preferred,brands,activity)"
            " VALUES (:user_id,:name,:persona,:price_band,:preferred,:brands,:activity)",
            list(rows),
        )


def insert_interactions(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO interactions (user_id,item_id,event,ts,label)"
            " VALUES (:user_id,:item_id,:event,:ts,:label)",
            list(rows),
        )


def load_items() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM items").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["geo"] = json.loads(d.pop("geo_json"))
        out.append(d)
    return out


def load_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["preferred"] = json.loads(d["preferred"])
        d["brands"] = json.loads(d.get("brands") or "[]")
        out.append(d)
    return out


def load_interactions(events: tuple[str, ...] = ("click",)) -> list[tuple[str, str, int, int]]:
    """返回 (user_id, item_id, ts, label)。

    event='cold_probe' 是新品上线后的"未来点击"，只在冷启动评测里用，
    绝不进入训练，否则冷启动评估就失效了。
    """
    placeholders = ",".join("?" for _ in events)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT user_id, item_id, ts, label FROM interactions WHERE event IN ({placeholders}) ORDER BY ts",
            events,
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def get_user(user_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["preferred"] = json.loads(d["preferred"])
    d["brands"] = json.loads(d.get("brands") or "[]")
    return d


def get_item(item_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["geo"] = json.loads(d.pop("geo_json"))
    return d


def save_rewrite(item_id: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rewrites (item_id,before,after,before_gqs,after_gqs,payload,engine,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                item_id,
                payload["before"],
                payload["after"],
                payload["before_gqs"],
                payload["after_gqs"],
                json.dumps(payload, ensure_ascii=False),
                payload.get("engine", "rule-based"),
                payload.get("created_at", ""),
            ),
        )


def get_rewrite(item_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT payload FROM rewrites WHERE item_id=?", (item_id,)).fetchone()
    return json.loads(r["payload"]) if r else None


def save_metric(name: str, payload: dict, created_at: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metrics (name,payload,created_at) VALUES (?,?,?)",
            (name, json.dumps(payload, ensure_ascii=False), created_at),
        )


def get_metric(name: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT payload FROM metrics WHERE name=?", (name,)).fetchone()
    return json.loads(r["payload"]) if r else None


def all_metrics() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT name,payload,created_at FROM metrics").fetchall()
    return {r["name"]: {"payload": json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows}
