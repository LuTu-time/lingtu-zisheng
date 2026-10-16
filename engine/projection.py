# -*- coding: utf-8 -*-
"""状态锚定：把可变状态表也纳入不可篡改的历史见证。

背景：历史不可改写的四重保障原本只覆盖 `event` 表；`stock`/`region`/
`character` 等可变状态仍可被绕过事件直接改写。本模块为此补上一道：

  state_digest()  对一组「世界状态表」做确定性指纹（逐表行数与内容摘要）。
  seal_state()    把当前指纹追加成一条不可改写事件（kind=state_seal）。
                  因为事件受哈希链与锚点保护，状态指纹从此不可被悄悄改掉。
  verify_state()  重算当前指纹，与最近一次封印比对：
                    · 一致  → 状态与封印时逐表相同，没被动过；
                    · 不一致 → 精确指出是哪几张表漂移了（drifted）。

这样「状态为何是今天这样」至少有了可核验的见证：世界若停在某次封印上，
任何对状态表的手工改写都会被查出，并被定位到具体表。

边界（仍然诚实）：这提供的是「状态篡改可检测」，还不是「状态可从头重放重建」
—— 后者要求每一次状态变更都作为事件被完整记录（含 before/after），而当前
生态/社会层的逐日微变更并非都有独立事件。那是一次更大的改造，留待后续。
"""
import hashlib
import json

# 需要纳入指纹的「世界状态表」。顺序固定，缺失的表自动跳过（兼容旧存档）。
STATE_TABLES = (
    "stock", "region", "settlement", "character", "faction", "relation", "memory",
    "grain_loan", "module_instance", "canon_fact", "player_identity",
    "household", "grave", "ledger", "prison", "collectible", "repute",
    "guild", "epidemic",
    "genre_thread", "genre_artifact", "genre_trace", "genre_intel", "genre_omen",
    "standing",
)


def _exists(store, table):
    return bool(store.scalar("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _dump_table(store, table):
    """单表快照：{columns, rows}，按 rowid 稳定排序。"""
    columns = [row["name"] for row in store.all(f"PRAGMA table_info({table})")]
    if not columns:
        return None
    rows = store.all(f"SELECT * FROM {table} ORDER BY rowid")
    return {"columns": columns, "rows": [[row[c] for c in columns] for row in rows]}


def _table_digest(store, table):
    """单表指纹：列名 + 逐行内容（按 rowid 稳定排序）。"""
    payload = _dump_table(store, table)
    if payload is None:
        return None, None
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(payload["rows"]), hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _digest_of_dump(dump):
    """按与 state_digest 完全相同的算法，从一份快照（dump）算出整体指纹。"""
    overall = hashlib.sha256()
    for name in STATE_TABLES:
        if name not in dump:
            continue
        payload = dump[name]
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        overall.update(f"{name}|{len(payload['rows'])}|{hashlib.sha256(blob.encode('utf-8')).hexdigest()}\n"
                       .encode("utf-8"))
    return overall.hexdigest()


def _canon(payload):
    """把一份表快照序列化成可比较的规范串。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dump_state(store):
    """把全部存在的状态表的**完整内容**取出（供状态存档点重建用）。"""
    dump = {}
    for name in STATE_TABLES:
        if not _exists(store, name):
            continue
        payload = _dump_table(store, name)
        if payload is not None:
            dump[name] = payload
    return dump


CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS state_checkpoint (
    id INTEGER PRIMARY KEY,
    seal_event_id INTEGER,
    tick INTEGER NOT NULL,
    digest TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    blob TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_state_checkpoint_tick ON state_checkpoint(tick);
"""


def ensure_schema(store):
    """补装状态存档点表（非破坏性）。注意：该表不参与状态指纹，避免自我指涉。"""
    store.con.executescript(CHECKPOINT_DDL)
    store.commit()


def state_digest(store):
    """对全部存在的状态表做确定性指纹。返回 {digest, tables:{名:{rows,digest}}}。"""
    tables = {}
    overall = hashlib.sha256()
    for name in STATE_TABLES:
        if not _exists(store, name):
            continue
        count, digest = _table_digest(store, name)
        tables[name] = {"rows": count, "digest": digest}
        overall.update(f"{name}|{count}|{digest}\n".encode("utf-8"))
    return {"digest": overall.hexdigest(), "tables": tables}


def seal_state(store, cx, label=""):
    """把当前状态指纹追加为一条不可改写事件（state_seal）。返回 {event_id,digest,tables,label}。"""
    if not isinstance(label, str) or len(label) > 120:
        raise ValueError("封印标签最多 120 字")
    snapshot = state_digest(store)
    counts = {name: info["rows"] for name, info in snapshot["tables"].items()}
    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    event_id = cx.emit(tick, "state_seal",
                       f"状态封印：共 {len(snapshot['tables'])} 张状态表，指纹 {snapshot['digest'][:12]}"
                       + (f"（{label}）" if label.strip() else ""),
                       cause=None,
                       effects={"digest": snapshot["digest"], "tables": snapshot["tables"],
                                "label": label.strip()},
                       actor_kind="author")
    if event_id is None:
        raise ValueError("事件被规则护栏拒绝；状态未封印")
    # 同时落一份完整状态存档点（不在事件链里，因而不影响确定性/事件流）；
    # 它与不可改写事件里的指纹互为见证。
    ensure_schema(store)
    blob = json.dumps(dump_state(store), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    store.con.execute(
        "INSERT INTO state_checkpoint(seal_event_id,tick,digest,label,blob) VALUES(?,?,?,?,?)",
        (event_id, tick, snapshot["digest"], label.strip(), blob))
    store.commit()
    return {"event_id": event_id, "digest": snapshot["digest"],
            "tables": counts, "label": label.strip()}


def _checkpoint(store, checkpoint_id=None):
    if not store.scalar("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_checkpoint'"):
        return None
    if checkpoint_id is None:
        return store.one("SELECT id,seal_event_id,tick,digest,label,blob FROM state_checkpoint "
                         "ORDER BY id DESC LIMIT 1")
    return store.one("SELECT id,seal_event_id,tick,digest,label,blob FROM state_checkpoint WHERE id=?",
                     (checkpoint_id,))


def state_at(store, checkpoint_id=None):
    """从某个状态存档点**精确重建**当时的状态（只读）。

    verified 为真 = 快照内容与记录在不可改写事件里的指纹一致（该点未被改过）。
    """
    cp = _checkpoint(store, checkpoint_id)
    if not cp:
        return {"checkpoint": None, "reason": "尚无状态存档点（先封印一次）"}
    try:
        dump = json.loads(cp["blob"])
    except (ValueError, TypeError):
        return {"checkpoint": cp["id"], "verified": False, "reason": "存档点内容损坏"}
    recomputed = _digest_of_dump(dump)
    event_digest = store.scalar("SELECT effects FROM event WHERE id=?", (cp["seal_event_id"],))
    attested = None
    if event_digest:
        try:
            attested = json.loads(event_digest).get("digest")
        except (ValueError, TypeError):
            attested = None
    return {"checkpoint": cp["id"], "tick": cp["tick"], "label": cp["label"],
            "digest": cp["digest"], "verified": recomputed == cp["digest"],
            "attested_by_event": attested == cp["digest"] if attested else None,
            "tables": dump}


def since(store, checkpoint_id=None):
    """自某个状态存档点以来的漂移明细：哪些表变了、其间发生了多少事件。"""
    cp = _checkpoint(store, checkpoint_id)
    if not cp:
        return {"checkpoint": None, "reason": "尚无状态存档点（先封印一次）"}
    try:
        dump = json.loads(cp["blob"])
    except (ValueError, TypeError):
        return {"checkpoint": cp["id"], "reason": "存档点内容损坏"}
    current = dump_state(store)
    drifted = []
    for name in sorted(set(dump) | set(current)):
        old, new = dump.get(name), current.get(name)
        if (_canon(old) if old is not None else None) != (_canon(new) if new is not None else None):
            drifted.append(name)
    events_since = store.all(
        "SELECT id,tick,kind,summary FROM event WHERE id > ? ORDER BY id", (cp["seal_event_id"],))
    return {"checkpoint": cp["id"], "seal_tick": cp["tick"], "label": cp["label"],
            "drifted": sorted(set(drifted)), "events_since": len(events_since),
            "events": [dict(e) for e in events_since[-50:]]}


def verify_state(store, cx=None):
    """重算当前状态指纹，与最近一次封印比对。"""
    current = state_digest(store)
    row = store.one("SELECT id,tick,effects FROM event WHERE kind='state_seal' ORDER BY id DESC LIMIT 1")
    result = {"sealed": bool(row), "current_digest": current["digest"],
              "tables": {n: i["rows"] for n, i in current["tables"].items()}}
    if cx is not None:
        chain_ok, brk, affected = cx.verify_chain()
        result["chain_ok"] = chain_ok
        result["anchor_ok"] = cx.verify_anchor()["ok"]
    if not row:
        return result
    try:
        sealed = json.loads(row["effects"])
    except (ValueError, TypeError):
        sealed = {}
    sealed_tables = sealed.get("tables", {}) if isinstance(sealed, dict) else {}
    drifted = []
    for name, info in current["tables"].items():
        sealed_info = sealed_tables.get(name)
        if not sealed_info or sealed_info.get("digest") != info["digest"]:
            drifted.append(name)
    for name in sealed_tables:
        if name not in current["tables"]:
            drifted.append(name)
    result.update({
        "seal_event_id": row["id"], "seal_tick": row["tick"],
        "sealed_digest": sealed.get("digest") if isinstance(sealed, dict) else None,
        "drifted": sorted(set(drifted)),
        "match": bool(isinstance(sealed, dict) and sealed.get("digest") == current["digest"]),
    })
    return result
