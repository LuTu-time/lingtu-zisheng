# -*- coding: utf-8 -*-
"""
《灵途自生》建库脚本生成器
从 data/schema.json 生成 schema/schema.sql，并可直接初始化数据库文件。

用法:
  python tools/build_schema.py            # 生成 schema/schema.sql
  python tools/build_schema.py --init     # 生成 sql 并创建 db/world.db
  python tools/build_schema.py --init --force   # 覆盖已存在的库
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_PATH = os.path.join(ROOT, "schema", "schema.sql")
DB_PATH = os.path.join(ROOT, "db", "world.db")


def load(name):
    with open(os.path.join(ROOT, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


def topo_order(tables):
    """按外键依赖排序，保证被引用的表先建。"""
    names = [t["name"] for t in tables]
    deps = {}
    for t in tables:
        d = set()
        for c in t["columns"]:
            for m in re.finditer(r"REFERENCES\s+(\w+)\s*\(", c.get("c", "")):
                ref = m.group(1)
                if ref != t["name"] and ref in names:
                    d.add(ref)
        deps[t["name"]] = d

    ordered, placed = [], set()
    while len(ordered) < len(names):
        progressed = False
        for n in names:
            if n in placed:
                continue
            if deps[n] <= placed:
                ordered.append(n)
                placed.add(n)
                progressed = True
        if not progressed:                      # 存在环，按原序兜底
            for n in names:
                if n not in placed:
                    ordered.append(n)
                    placed.add(n)
            break
    by_name = {t["name"]: t for t in tables}
    return [by_name[n] for n in ordered]


def gen_sql(schema):
    tables = topo_order(schema["tables"])
    L = []
    L.append("-- ============================================================")
    L.append("-- 灵途自生 · 世界推演引擎 — SQLite 物理模型")
    L.append(f"-- 版本 {schema['meta']['version']}　生成于 {datetime.now():%Y-%m-%d %H:%M:%S}")
    L.append("-- 由 tools/build_schema.py 从 data/schema.json 自动生成，请勿手改。")
    L.append("-- ============================================================")
    L.append("")
    L.append("-- 建表期间关闭外键检查：部分表存在相互引用（character↔event），")
    L.append("-- SQLite 允许前向引用，但关闭检查可避免建表顺序带来的意外。")
    L.append("PRAGMA foreign_keys = OFF;")
    L.append("PRAGMA journal_mode = WAL;")
    L.append("")

    n_idx = 0
    n_imm = 0
    for t in tables:
        L.append(f"-- [{t['module']}] {t['cn']}：{t['comment']}")
        L.append(f'CREATE TABLE IF NOT EXISTS "{t["name"]}" (')
        cols = []
        last = len(t["columns"]) - 1
        for i, c in enumerate(t["columns"]):
            line = f'  "{c["n"]}" {c["t"]}'
            if c.get("c"):
                line += f' {c["c"]}'
            if i < last:
                line += ","
            if c.get("d"):
                line += f'  -- {c["d"]}'
            cols.append(line)
        L.append("\n".join(cols))
        L.append(");")
        for ix in t.get("indexes", []):
            kind = "UNIQUE INDEX" if ix.get("unique") else "INDEX"
            cl = ", ".join(f'"{x}"' for x in ix["cols"])
            L.append(f'CREATE {kind} IF NOT EXISTS "{ix["name"]}" ON "{t["name"]}" ({cl});')
            n_idx += 1
        if t.get("immutable"):
            n_imm += 1
            L.append("")
            L.append(f"-- 历史不可改写（{t['cn']}）")
            L.append(f"-- {t.get('immutable_reason', '已发生的事实不可修改。')}")
            L.append("-- 这两条触发器是硬约束：任何 UPDATE / DELETE 都会被数据库直接拒绝，")
            L.append("-- 无论它来自应用层、脚本，还是有人拿 sqlite3 手敲命令。")
            name = t["name"]
            for op, verb in (("update", "修改"), ("delete", "抹除")):
                L.append(f'CREATE TRIGGER IF NOT EXISTS "trg_{name}_no_{op}"')
                L.append(f'BEFORE {"UPDATE" if op == "update" else "DELETE"} ON "{name}"')
                L.append("BEGIN")
                L.append(f"  SELECT RAISE(ABORT, '历史不可{verb}：{t['cn']}是已发生的事实。"
                         f"要改变过去，请在历史点上另开一条时间线。');")
                L.append("END;")
        if t.get("protected_when"):
            name = t["name"]
            for op in ("update", "delete"):
                L.append(f'CREATE TRIGGER IF NOT EXISTS "trg_{name}_protect_{op}"')
                L.append(f'BEFORE {op.upper()} ON "{name}" WHEN {t["protected_when"]}')
                L.append("BEGIN")
                L.append("  SELECT RAISE(ABORT, '已确认设定不可改写；请追加新设定说明。');")
                L.append("END;")
        L.append("")

    L.append("PRAGMA foreign_keys = ON;")
    L.append("")
    return "\n".join(L), len(tables), n_idx, n_imm


def init_db(sql, force=False):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        if not force:
            return f"数据库已存在，未覆盖：{DB_PATH}（如需重建请加 --force）"
        os.remove(DB_PATH)
        for suffix in ("-wal", "-shm"):
            p = DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
    con = sqlite3.connect(DB_PATH)
    con.executescript(sql)
    con.commit()
    n = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    ok = "外键校验通过" if not fk else f"外键异常 {len(fk)} 处"
    return f"已创建：{DB_PATH}　表 {n} 张　{ok}"


def main():
    schema = load("schema.json")
    sql, nt, ni, nim = gen_sql(schema)
    os.makedirs(os.path.dirname(SQL_PATH), exist_ok=True)
    with open(SQL_PATH, "w", encoding="utf-8") as f:
        f.write(sql)
    print(f"OK  {SQL_PATH}")
    print(f"    表 {nt} 张 / 索引 {ni} 个 / 不可变表 {nim} 张 / 拒绝改写触发器 {nim * 2} 条")
    print(f"    SQL {len(sql)} 字节")

    if "--init" in sys.argv:
        print("    " + init_db(sql, force=("--force" in sys.argv)))
    sys.exit(0)


if __name__ == "__main__":
    main()
