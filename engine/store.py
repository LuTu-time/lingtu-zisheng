# -*- coding: utf-8 -*-
"""
持久化层：SQLite 的薄封装。

两条铁律：
  1. 所有状态变更都经此落库，保证「关掉程序重开，世界还是原来的样子」。
  2. **已发生的事实不可改写。** 标记为 immutable 的表（目前是 event）
     拒绝一切 update / delete，无论是谁调的。

第 2 条在数据库层还有一道触发器兜底 —— 即使有人绕开这个类、
直接拿 sqlite3 连上来敲 SQL，也一样会被拒绝。两道防线是刻意的冗余：
一道防手滑，一道防手快。
"""
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_JSON = os.path.join(os.path.dirname(_HERE), "data", "schema.json")


def _load_immutable():
    """不可变表清单从 data/schema.json 读取 —— 单一数据源，不在这里重复维护。"""
    try:
        with open(_SCHEMA_JSON, encoding="utf-8") as f:
            s = json.load(f)
        return {t["name"] for t in s["tables"] if t.get("immutable")}
    except Exception:
        return {"event"}


IMMUTABLE_TABLES = _load_immutable()


class HistoryRewriteAttempt(Exception):
    """企图改写已经发生的历史。这不是 bug，是必须被拒绝的操作。"""


class Store:
    def __init__(self, path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.tamper_log = []          # 被拦下的改写企图，作为「规则在起作用」的证据

    # ---- 查询 ----
    def one(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        return self.con.execute(sql, args).fetchall()

    def scalar(self, sql, args=(), default=None):
        row = self.con.execute(sql, args).fetchone()
        return row[0] if row else default

    # ---- 守卫 ----
    def _refuse(self, op, table, detail):
        msg = (f"历史不可改写：不能 {op} 「{table}」上的记录。{detail}"
               f"　要改变过去，请在历史点上另开一条时间线。")
        self.tamper_log.append({"op": op, "table": table, "detail": detail})
        raise HistoryRewriteAttempt(msg)

    # ---- 写入（只允许追加）----
    def insert(self, table, data):
        cols = ", ".join(f'"{k}"' for k in data)
        ph = ", ".join("?" for _ in data)
        cur = self.con.execute(
            f'INSERT INTO "{table}" ({cols}) VALUES ({ph})', tuple(data.values()))
        return cur.lastrowid

    def update(self, table, row_id, data):
        if table in IMMUTABLE_TABLES:
            self._refuse("修改", table, f"（row id={row_id}）")
        sets = ", ".join(f'"{k}" = ?' for k in data)
        self.con.execute(
            f'UPDATE "{table}" SET {sets} WHERE id = ?',
            tuple(data.values()) + (row_id,))

    def bump(self, table, row_id, col, delta, tick=None):
        """数值增量更新，避免应用层反复读改写。"""
        if table in IMMUTABLE_TABLES:
            self._refuse("修改", table, f"（{col} 增量 {delta:+.2f}）")
        sql = f'UPDATE "{table}" SET "{col}" = "{col}" + ?'
        args = [delta]
        if tick is not None:
            sql += ', "updated_tick" = ?'
            args.append(tick)
        sql += " WHERE id = ?"
        args.append(row_id)
        self.con.execute(sql, args)

    def delete(self, table, row_id):
        """删除同样是对历史的改写 —— 抹掉一条记录和篡改它一样不可接受。"""
        if table in IMMUTABLE_TABLES:
            self._refuse("删除", table, f"（row id={row_id}）")
        self.con.execute(f'DELETE FROM "{table}" WHERE id = ?', (row_id,))

    def commit(self):
        self.con.commit()

    def close(self):
        self.con.commit()
        self.con.close()
