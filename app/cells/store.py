"""矩阵相关表的持久化层。

与推荐系统共用同一个 SQLite 文件，但表彼此独立，互不影响已有功能。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from ..db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    brand       TEXT,
    facts       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_rule_sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    trained_at  TEXT NOT NULL,
    samples     INTEGER NOT NULL,
    auc         REAL,
    source      TEXT NOT NULL,          -- simulated / api
    weights     TEXT NOT NULL,          -- JSON: {dim: weight}
    cards       TEXT NOT NULL,          -- JSON: 规则卡
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_rule_cell ON cell_rule_sets(model_id, channel_id, version DESC);

CREATE TABLE IF NOT EXISTS variants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    body        TEXT NOT NULL,
    title       TEXT,
    checks      TEXT NOT NULL,          -- JSON: 硬约束校验结果
    score       REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id   INTEGER NOT NULL,
    channel_id   TEXT NOT NULL,
    url          TEXT NOT NULL,
    published_at TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'published'
);

CREATE TABLE IF NOT EXISTS probes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    asset_id   TEXT,
    query      TEXT NOT NULL,
    ran_at     TEXT NOT NULL,
    cited      INTEGER NOT NULL,
    rank       INTEGER,
    phase      TEXT NOT NULL DEFAULT 'after',   -- baseline / before / after
    citations  TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe ON probes(model_id, channel_id, asset_id, phase);

-- 测试问题集：一条记录 = 一个目标物在某个「模型×渠道」下的一批问法
CREATE TABLE IF NOT EXISTS question_sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    params      TEXT NOT NULL,          -- JSON: 引导问答的答案
    groups      TEXT NOT NULL,          -- JSON: 分组问法
    total       INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qset ON question_sets(target_id, model_id, channel_id, id DESC);

-- 提及率基线：投喂之前，用同一批问法测出来的引用情况
CREATE TABLE IF NOT EXISTS baselines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    set_id      INTEGER,
    samples     INTEGER NOT NULL,
    hits        INTEGER NOT NULL,
    hit_rate    REAL NOT NULL,
    avg_rank    REAL,
    queries     TEXT,                   -- JSON: 本次使用的问法
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_base ON baselines(target_id, model_id, channel_id, id DESC);

-- 投喂记录：谁在什么时候把哪一版投到了哪个渠道
CREATE TABLE IF NOT EXISTS feeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    variant_id  INTEGER,
    url         TEXT,
    status      TEXT NOT NULL DEFAULT 'published',   -- scheduled / published / rechecked
    note        TEXT,
    duration_days INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feed ON feeds(target_id, model_id, channel_id, id DESC);

-- 会员套餐：目标物 → 套餐级别，决定第 4 环节能往哪些渠道投放
CREATE TABLE IF NOT EXISTS memberships (
    target_id   TEXT PRIMARY KEY,
    plan        TEXT NOT NULL,          -- trial / standard / premium
    paid_until  TEXT,
    updated_at  TEXT NOT NULL
);

-- 复测快照：一次复测一行，看板直接读最新一行，不去聚合探针
CREATE TABLE IF NOT EXISTS rechecks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    variant_id  INTEGER,
    samples     INTEGER NOT NULL,
    hits        INTEGER NOT NULL,
    hit_rate    REAL NOT NULL,
    avg_rank    REAL,
    delta       REAL,
    z           REAL,
    level       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recheck ON rechecks(target_id, model_id, channel_id, id DESC);
"""

_ready = False


def _migrate(conn) -> None:
    """老库升级：只加列，不动已有数据。"""
    def add_col(table: str, col: str, ddl: str) -> None:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    add_col("cell_rule_sets", "question_set_id", "INTEGER")
    add_col("question_sets", "dimension", "TEXT DEFAULT ''")
    add_col("baselines", "dimension", "TEXT DEFAULT ''")
    add_col("variants", "status", "TEXT DEFAULT 'approved'")
    add_col("variants", "updated_at", "TEXT")
    add_col("feeds", "duration_days", "INTEGER")
    add_col("rechecks", "before_hit_rate", "REAL")
    add_col("rechecks", "before_samples", "INTEGER")
    add_col("rechecks", "before_hits", "INTEGER")
    add_col("rechecks", "feed_id", "INTEGER")
    add_col("memberships", "member_name", "TEXT DEFAULT ''")
    add_col("memberships", "note", "TEXT")


def init_tables() -> None:
    global _ready
    if _ready:
        return
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    _ready = True


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------- rule sets ----
def next_version(model_id: str, channel_id: str) -> int:
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(version) FROM cell_rule_sets WHERE model_id=? AND channel_id=?",
            (model_id, channel_id),
        ).fetchone()
    return int(row[0] or 0) + 1


def save_rule_set(model_id: str, channel_id: str, samples: int, auc: float | None,
                  weights: dict[str, float], cards: list[dict], source: str,
                  notes: str = "", question_set_id: int | None = None) -> int:
    init_tables()
    version = next_version(model_id, channel_id)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO cell_rule_sets (model_id,channel_id,version,trained_at,samples,auc,"
            "source,weights,cards,notes,question_set_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (model_id, channel_id, version, _now(), samples, auc, source,
             json.dumps(weights, ensure_ascii=False), json.dumps(cards, ensure_ascii=False), notes,
             question_set_id),
        )
        return int(cur.lastrowid)


def latest_rule_set(model_id: str, channel_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM cell_rule_sets WHERE model_id=? AND channel_id=? "
            "ORDER BY version DESC LIMIT 1",
            (model_id, channel_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["weights"] = json.loads(d["weights"])
    d["cards"] = json.loads(d["cards"])
    return d


def coverage() -> dict[str, dict]:
    """返回每格最新规则集的摘要，供矩阵视图渲染。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT model_id, channel_id, version, trained_at, samples, auc, source "
            "FROM cell_rule_sets r WHERE version = ("
            "  SELECT MAX(version) FROM cell_rule_sets "
            "  WHERE model_id=r.model_id AND channel_id=r.channel_id)"
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[f"{r['model_id']}::{r['channel_id']}"] = {
            "version": r["version"], "trained_at": r["trained_at"],
            "samples": r["samples"], "auc": r["auc"], "source": r["source"],
        }
    return out


# --------------------------------------------------------------- assets ----
def upsert_asset(asset_id: str, title: str, body: str, brand: str = "",
                 facts: dict | None = None) -> None:
    init_tables()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO assets (asset_id,title,body,brand,facts,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (asset_id, title, body, brand, json.dumps(facts or {}, ensure_ascii=False), _now()),
        )


def get_asset(asset_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["facts"] = json.loads(d.get("facts") or "{}")
    return d


def list_assets() -> list[dict]:
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT asset_id,title,brand,created_at,length(body) AS chars FROM assets "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- variants ----
def save_variant(asset_id: str, model_id: str, channel_id: str, body: str,
                 title: str, checks: dict, score: float | None,
                 status: str = "draft") -> int:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO variants (asset_id,model_id,channel_id,body,title,checks,score,"
            "created_at,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (asset_id, model_id, channel_id, body, title,
             json.dumps(checks, ensure_ascii=False), score, _now(), status, _now()),
        )
        return int(cur.lastrowid)


def update_variant(variant_id: int, title: str, body: str, checks: dict,
                   score: float | None) -> dict | None:
    """人工再次编辑：重算分数并重置为待审核（改过内容必须重新过审）。"""
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE variants SET title=?, body=?, checks=?, score=?, status='draft', "
            "updated_at=? WHERE id=?",
            (title, body, json.dumps(checks, ensure_ascii=False), score, _now(), variant_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    d = dict(row)
    d["checks"] = json.loads(d["checks"])
    return d


def approve_variant(variant_id: int) -> dict | None:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE variants SET status='approved', updated_at=? WHERE id=?",
            (_now(), variant_id))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    d = dict(row)
    d["checks"] = json.loads(d["checks"])
    return d


def latest_variant(asset_id: str, model_id: str, channel_id: str,
                   status: str | None = None) -> dict | None:
    init_tables()
    sql = "SELECT * FROM variants WHERE asset_id=? AND model_id=? AND channel_id=?"
    args: list[Any] = [asset_id, model_id, channel_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT 1"
    with connect() as conn:
        row = conn.execute(sql, args).fetchone()
    if not row:
        return None
    d = dict(row)
    d["checks"] = json.loads(d["checks"])
    return d


def get_variant(variant_id: int) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["checks"] = json.loads(d["checks"])
    return d


def list_variants(asset_id: str | None = None, limit: int = 30) -> list[dict]:
    init_tables()
    with connect() as conn:
        if asset_id:
            rows = conn.execute(
                "SELECT id,asset_id,model_id,channel_id,title,score,created_at,status,updated_at "
                "FROM variants WHERE asset_id=? ORDER BY id DESC LIMIT ?", (asset_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,asset_id,model_id,channel_id,title,score,created_at,status,updated_at "
                "FROM variants ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------- publications ----
def save_publication(variant_id: int, channel_id: str, url: str,
                     status: str = "published") -> int:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO publications (variant_id,channel_id,url,published_at,status)"
            " VALUES (?,?,?,?,?)",
            (variant_id, channel_id, url, _now(), status),
        )
        return int(cur.lastrowid)


def list_publications(limit: int = 50) -> list[dict]:
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.*, v.title, v.model_id FROM publications p "
            "LEFT JOIN variants v ON v.id=p.variant_id ORDER BY p.id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- probes --
def save_probe(model_id: str, channel_id: str, asset_id: str | None, query: str,
               cited: bool, rank: int | None, phase: str = "after",
               citations: list[dict] | None = None) -> None:
    init_tables()
    with connect() as conn:
        conn.execute(
            "INSERT INTO probes (model_id,channel_id,asset_id,query,ran_at,cited,rank,phase,citations)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (model_id, channel_id, asset_id, query, _now(), int(cited), rank, phase,
             json.dumps(citations or [], ensure_ascii=False)),
        )


def probe_summary(model_id: str, channel_id: str, asset_id: str | None = None) -> dict:
    init_tables()
    sql = ("SELECT phase, COUNT(*) n, SUM(cited) hits, AVG(CASE WHEN cited=1 THEN rank END) avg_rank "
           "FROM probes WHERE model_id=? AND channel_id=?")
    args: list[Any] = [model_id, channel_id]
    if asset_id:
        sql += " AND asset_id=?"
        args.append(asset_id)
    sql += " GROUP BY phase"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["phase"]] = {
            "samples": r["n"],
            "hit_rate": round((r["hits"] or 0) / max(r["n"], 1), 3),
            "avg_rank": round(r["avg_rank"], 2) if r["avg_rank"] else None,
        }
    return out


def probe_runs(model_id: str, channel_id: str, asset_id: str) -> list[dict]:
    """按"天"汇总探针结果，形成时间线（同一天多次跑会合并）。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT substr(ran_at,1,10) AS d, phase, COUNT(*) n, SUM(cited) hits, "
            "AVG(CASE WHEN cited=1 THEN rank END) avg_rank FROM probes "
            "WHERE model_id=? AND channel_id=? AND asset_id=? "
            "GROUP BY d, phase ORDER BY d", (model_id, channel_id, asset_id)).fetchall()
    return [{"date": r["d"], "phase": r["phase"], "samples": r["n"],
             "hits": r["hits"] or 0, "hit_rate": round((r["hits"] or 0) / max(r["n"], 1), 3),
             "avg_rank": round(r["avg_rank"], 2) if r["avg_rank"] else None} for r in rows]


# --------------------------------------------------------- question sets --
def save_question_set(target_id: str, model_id: str, channel_id: str,
                      params: dict, groups: list[dict], total: int,
                      dimension: str = "") -> int:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO question_sets (target_id,model_id,channel_id,params,groups,total,created_at,dimension)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (target_id, model_id, channel_id, json.dumps(params, ensure_ascii=False),
             json.dumps(groups, ensure_ascii=False), total, _now(), dimension),
        )
        return int(cur.lastrowid)


def latest_question_set(target_id: str, model_id: str, channel_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM question_sets WHERE target_id=? AND model_id=? AND channel_id=? "
            "ORDER BY id DESC LIMIT 1", (target_id, model_id, channel_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    d["groups"] = json.loads(d["groups"])
    d["questions"] = [q["text"] for g in d["groups"] for q in g["questions"]]
    return d


def get_question_set(set_id: int) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM question_sets WHERE id=?", (set_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    d["groups"] = json.loads(d["groups"])
    d["questions"] = [q["text"] for g in d["groups"] for q in g["questions"]]
    return d


def latest_question_set_any(model_id: str, channel_id: str) -> dict | None:
    """该格最近一条问题集（不限定目标物），供全局学规则时复用。"""
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM question_sets WHERE model_id=? AND channel_id=? "
            "ORDER BY id DESC LIMIT 1", (model_id, channel_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    d["groups"] = json.loads(d["groups"])
    d["questions"] = [q["text"] for g in d["groups"] for q in g["questions"]]
    return d


def list_question_sets(target_id: str | None = None, limit: int = 20) -> list[dict]:
    init_tables()
    with connect() as conn:
        if target_id:
            rows = conn.execute(
                "SELECT id,target_id,model_id,channel_id,total,created_at,dimension FROM question_sets "
                "WHERE target_id=? ORDER BY id DESC LIMIT ?", (target_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,target_id,model_id,channel_id,total,created_at,dimension FROM question_sets "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def latest_dimension_sets(target_id: str) -> list[dict]:
    """每个维度最近一套问题集（维度集不绑模型与渠道：model/channel 存 ''）。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM question_sets q WHERE q.target_id=? AND q.dimension!='' AND q.id = ("
            "  SELECT MAX(id) FROM question_sets WHERE target_id=q.target_id AND dimension=q.dimension)"
            " ORDER BY q.id", (target_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["groups"] = json.loads(d["groups"])
        d["questions"] = [q["text"] for g in d["groups"] for q in g["questions"]]
        out.append(d)
    return out


def dimension_questions(target_id: str) -> list[str]:
    """目标物全部维度问题集的问法并集（去重保序）。"""
    seen, out = set(), []
    for s in latest_dimension_sets(target_id):
        for q in s["questions"]:
            if q not in seen:
                seen.add(q)
                out.append(q)
    return out


# ------------------------------------------------------------ baselines ---
def save_baseline(target_id: str, model_id: str, channel_id: str, set_id: int | None,
                  samples: int, hits: int, hit_rate: float, avg_rank: float | None,
                  queries: list[str], dimension: str = "") -> int:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO baselines (target_id,model_id,channel_id,set_id,samples,hits,hit_rate,"
            "avg_rank,queries,created_at,dimension) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (target_id, model_id, channel_id, set_id, samples, hits, hit_rate, avg_rank,
             json.dumps(queries, ensure_ascii=False), _now(), dimension),
        )
        return int(cur.lastrowid)


def latest_baseline(target_id: str, model_id: str, channel_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM baselines WHERE target_id=? AND model_id=? AND channel_id=? "
            "ORDER BY id DESC LIMIT 1", (target_id, model_id, channel_id)).fetchone()
    return dict(row) if row else None


def baseline_targets() -> int:
    """已经有基线的目标物数量（看板顶部的覆盖度）。"""
    init_tables()
    with connect() as conn:
        return conn.execute("SELECT COUNT(DISTINCT target_id) FROM baselines").fetchone()[0]


def baseline_map(target_id: str, model_id: str | None = None) -> dict[str, dict]:
    init_tables()
    sql = ("SELECT b.* FROM baselines b WHERE b.target_id=? AND b.id = ("
           "  SELECT MAX(id) FROM baselines WHERE target_id=b.target_id "
           "  AND model_id=b.model_id AND channel_id=b.channel_id"
           "  AND (dimension=b.dimension OR (dimension='' AND b.dimension='')))")
    args: list[Any] = [target_id]
    if model_id:
        sql += " AND b.model_id=?"
        args.append(model_id)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {f"{r['model_id']}::{r['channel_id']}::{r['dimension'] or ''}": dict(r) for r in rows}


def baseline_matrix(target_id: str) -> dict[str, dict]:
    """维度基线矩阵：key = model::dimension（channel 维基线的 channel_id=''）。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT b.* FROM baselines b WHERE b.target_id=? AND b.channel_id='' AND b.dimension!='' "
            "AND b.id = (SELECT MAX(id) FROM baselines WHERE target_id=b.target_id "
            "AND model_id=b.model_id AND dimension=b.dimension AND channel_id='')",
            (target_id,)).fetchall()
    return {f"{r['model_id']}::{r['dimension']}": dict(r) for r in rows}


# ---------------------------------------------------------------- feeds ---
def save_feed(target_id: str, model_id: str, channel_id: str, variant_id: int | None,
              url: str, status: str = "published", note: str = "",
              duration_days: int | None = None) -> int:
    init_tables()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO feeds (target_id,model_id,channel_id,variant_id,url,status,note,"
            "duration_days,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (target_id, model_id, channel_id, variant_id, url, status, note,
             duration_days, _now()),
        )
        return int(cur.lastrowid)


def feed_map(target_id: str, model_id: str) -> dict[str, dict]:
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT f.* FROM feeds f WHERE f.target_id=? AND f.model_id=? AND f.id = ("
            "  SELECT MAX(id) FROM feeds WHERE target_id=f.target_id AND model_id=f.model_id "
            "  AND channel_id=f.channel_id)", (target_id, model_id)).fetchall()
    return {r["channel_id"]: dict(r) for r in rows}


def list_feeds(target_id: str | None = None, limit: int = 50) -> list[dict]:
    init_tables()
    with connect() as conn:
        if target_id:
            rows = conn.execute(
                "SELECT * FROM feeds WHERE target_id=? ORDER BY id DESC LIMIT ?",
                (target_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM feeds ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_recheck(target_id: str, model_id: str, channel_id: str, variant_id: int | None,
                 samples: int, hits: int, hit_rate: float, avg_rank: float | None,
                 delta: float, z: float, level: str,
                 before_samples: int = 0, before_hits: int = 0,
                 before_hit_rate: float = 0.0) -> int:
    init_tables()
    with connect() as conn:
        # 锚定当前最新一次登记：登记一旦被修改（产生新记录），锚定旧记录的复测全部失效
        row = conn.execute(
            "SELECT id FROM feeds WHERE target_id=? AND model_id=? AND channel_id=? "
            "ORDER BY id DESC LIMIT 1", (target_id, model_id, channel_id)).fetchone()
        feed_id = int(row["id"]) if row else None
        cur = conn.execute(
            "INSERT INTO rechecks (target_id,model_id,channel_id,variant_id,samples,hits,"
            "hit_rate,avg_rank,delta,z,level,created_at,before_samples,before_hits,"
            "before_hit_rate,feed_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (target_id, model_id, channel_id, variant_id, samples, hits, hit_rate,
             avg_rank, delta, z, level, _now(),
             before_samples, before_hits, before_hit_rate, feed_id),
        )
        return int(cur.lastrowid)


def latest_recheck(target_id: str, model_id: str, channel_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM rechecks WHERE target_id=? AND model_id=? AND channel_id=? "
            "ORDER BY id DESC LIMIT 1", (target_id, model_id, channel_id)).fetchone()
    return dict(row) if row else None


def list_rechecks(target_id: str, model_id: str, channel_id: str) -> list[dict]:
    """该格全部复测，按时间升序：第 1 行 = 复测1（约定投喂后 15 天），
    第 2 行 = 复测2（约定 30 天），以此类推。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rechecks WHERE target_id=? AND model_id=? AND channel_id=? "
            "ORDER BY id ASC", (target_id, model_id, channel_id)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------- memberships ---
def set_membership(target_id: str, plan: str, paid_until: str | None = None,
                   member_name: str | None = None, note: str | None = None) -> None:
    """写入会员：会员名（企业）只在传入时更新，避免改套餐时把企业名冲掉。"""
    init_tables()
    with connect() as conn:
        conn.execute(
            "INSERT INTO memberships (target_id,plan,paid_until,member_name,note,updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(target_id) DO UPDATE SET plan=excluded.plan, "
            "paid_until=excluded.paid_until, "
            "member_name=COALESCE(NULLIF(excluded.member_name,''), memberships.member_name), "
            "note=COALESCE(excluded.note, memberships.note), "
            "updated_at=excluded.updated_at",
            (target_id, plan, paid_until, member_name or "", note, _now()),
        )


def get_membership(target_id: str) -> dict | None:
    init_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM memberships WHERE target_id=?",
                           (target_id,)).fetchone()
    return dict(row) if row else None


def list_memberships() -> list[dict]:
    """全部会员（企业）及其套餐，会员管理页用。"""
    init_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.*, i.title, i.brand, i.category, i.promote_type, i.promote_name, "
            "i.member_name AS item_member_name, i.gqs "
            "FROM memberships m JOIN items i ON i.item_id=m.target_id "
            "WHERE i.item_id LIKE 'E%' ORDER BY CASE m.plan "
            "WHEN 'premium' THEN 0 WHEN 'standard' THEN 1 ELSE 2 END, m.target_id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["member_name"] = d.get("member_name") or d.pop("item_member_name") or d["brand"]
        d.pop("item_member_name", None)
        out.append(d)
    return out


def progress(target_id: str, model_id: str | None = None) -> dict:
    """六步进度：问题集 / 基线 / 规则 / 改写 / 投喂 / 复测。"""
    init_tables()
    mid = " AND model_id=?" if model_id else ""
    args: list[Any] = [target_id] + ([model_id] if model_id else [])
    with connect() as conn:
        sets = conn.execute(
            "SELECT COUNT(DISTINCT dimension) FROM question_sets "
            "WHERE target_id=? AND dimension!=''", (target_id,)).fetchone()[0]
        base = conn.execute(
            "SELECT COUNT(DISTINCT model_id) FROM baselines "
            "WHERE target_id=? AND channel_id='' AND dimension!=''", (target_id,)
        ).fetchone()[0]
        variants = conn.execute(
            "SELECT COUNT(DISTINCT channel_id) FROM variants WHERE asset_id=? AND status='approved'"
            + mid, args).fetchone()[0]
        # 投喂与复测是渠道级行为：无论登记时针对哪个模型，进度条都应点亮；
        # 按 ctx.model 过滤会导致"登记了但进度不亮"的错觉。
        feeds = conn.execute(
            "SELECT COUNT(DISTINCT channel_id) FROM feeds WHERE target_id=?",
            (target_id,)).fetchone()[0]
        after = conn.execute(
            "SELECT COUNT(DISTINCT channel_id) FROM rechecks WHERE target_id=?",
            (target_id,)).fetchone()[0]
        learned = conn.execute(
            "SELECT COUNT(DISTINCT model_id) FROM cell_rule_sets").fetchone()[0]
        n_models = len(registry_models())
    return {"question_set_dims": sets, "baseline_models": base,
            "learned_models": learned, "models_total": n_models,
            "approved_channels": variants, "feed_channels": feeds,
            "recheck_channels": after}


def registry_models() -> list[str]:
    """避免循环 import，运行时取模型清单。"""
    from .registry import MODELS
    return [m.id for m in MODELS]
