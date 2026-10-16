# -*- coding: utf-8 -*-
"""Interactive, persistent vertical slice of the world simulation."""
import argparse
import json
import os
import sqlite3
import sys

from engine import scheduler, sim, worldgen, writing
from engine.causality import Causality
from engine.guardrails import Guardrail
from engine.store import Store
from run_p0 import load_ids

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(ROOT, "db", "play.db")


def open_world(path):
    if not os.path.isfile(path):
        raise ValueError("存档不存在；先用 init 创建世界")
    store = Store(path)
    try:
        if store.scalar("SELECT COUNT(*) FROM world") != 1:
            raise ValueError("存档没有世界；请另选存档")
        guard = Guardrail(store)
        guard.sync_horizon()
        cx = Causality(store, guard)
        valid, _, _ = cx.verify_chain()
        if not valid or not cx.verify_anchor()["ok"]:
            raise ValueError("历史校验失败，已拒绝继续操作")
        writing.ensure_canon_protection(store)
        return store, guard, cx
    except Exception:
        store.con.close()
        raise


def create(path, seed, theme, name):
    if os.path.exists(path):
        raise ValueError("存档已存在；init 不会覆盖历史，请换一个 --db 路径")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf-8") as f:
        schema = f.read()
    con = sqlite3.connect(path)
    try:
        con.executescript(schema)
        con.commit()
    finally:
        con.close()
    store = Store(path)
    try:
        worldgen.generate(store, seed, theme, name, verbose=False)
        scheduler.schedule(store, theme)
        guard = Guardrail(store)
        cx = Causality(store, guard)
        cx.emit(0, "genesis", f"世界「{store.scalar('SELECT name FROM world WHERE id=1')}」建立",
                place_kind="settlement", place_id=load_ids(store)["settlement_id"])
        store.commit()
        print("世界已建立：", path)
    finally:
        store.close()


def snapshot(store):
    w = store.one("SELECT name, seed, tick, theme FROM world WHERE id=1")
    s = store.one("SELECT id, name, population, morale, order_value, housing FROM settlement LIMIT 1")
    r = store.one("SELECT name, forest, game_density FROM region LIMIT 1")
    stocks = {x["resource_type"]: round(x["amount"], 1) for x in store.all(
        "SELECT resource_type, amount FROM stock WHERE owner_kind='settlement' AND owner_id=?", (s["id"],))}
    reserve = store.scalar("SELECT amount FROM stock WHERE owner_kind='faction' AND resource_type='grain'", default=0)
    return {"世界": w["name"], "种子": w["seed"], "题材": w["theme"], "tick": w["tick"],
            "第几日": w["tick"] // 24, "聚落": s["name"], "聚落id": s["id"],
            "人口": s["population"], "住房": s["housing"],
            "民心": round(s["morale"], 3), "秩序": round(s["order_value"], 3),
            "林木": round(r["forest"], 3), "猎物": round(r["game_density"], 3),
            "聚落库存": stocks, "势力储粮": round(reserve or 0, 1),
            "事件数": store.scalar("SELECT COUNT(*) FROM event")}


def intervene(store, cx, action, amount):
    if not 0 < amount <= 100 or not amount.is_integer():
        raise ValueError("数量须为 1~100 的整数")
    amount = int(amount)
    ids = load_ids(store)
    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    sid = ids["settlement_id"]
    if action == "relief":
        source = store.one("SELECT id, amount FROM stock WHERE owner_kind='faction' AND owner_id=? "
                           "AND resource_type='grain'", (ids["villager_fid"],))
        target = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                           "AND resource_type='grain'", (sid,))
        if not source or not target or source["amount"] < amount:
            raise ValueError("势力储粮不足；不能凭空生成粮食")
        event = cx.emit(tick, "author_relief", f"作者令村民从势力仓拨粮 {amount} 至聚落",
                        effects={"source_grain": [source["amount"], source["amount"] - amount],
                                 "settlement_grain": [target["amount"], target["amount"] + amount]},
                        place_kind="settlement", place_id=sid, actor_kind="author")
        if event is None:
            raise ValueError("事件被规则护栏拒绝；状态未修改")
        store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                          (amount, tick, source["id"]))
        store.con.execute("UPDATE stock SET amount=amount+?, updated_tick=? WHERE id=?",
                          (amount, tick, target["id"]))
    else:
        wood = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                         "AND resource_type='wood'", (sid,))
        region = store.one("SELECT id, forest FROM region LIMIT 1")
        gain = min(0.95 - region["forest"], amount * 0.002)
        if not wood or wood["amount"] < amount or gain <= 0:
            raise ValueError("薪柴不足或林木已达到上限")
        event = cx.emit(tick, "author_reforest", f"作者组织植树，耗薪柴 {amount}，林木恢复 {gain:.3f}",
                        effects={"wood": [wood["amount"], wood["amount"] - amount],
                                 "forest": [region["forest"], region["forest"] + gain]},
                        place_kind="region", place_id=region["id"], actor_kind="author")
        if event is None:
            raise ValueError("事件被规则护栏拒绝；状态未修改")
        store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                          (amount, tick, wood["id"]))
        store.con.execute("UPDATE region SET forest=forest+?, updated_tick=? WHERE id=?",
                          (gain, tick, region["id"]))
    store.commit()
    return event


def main():
    ap = argparse.ArgumentParser(description="灵途自生：观察、干预并继续一个持久世界")
    ap.add_argument("--db", default=DEFAULT_DB, help="独立存档路径；默认 db/play.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="建立新世界，不覆盖既有存档")
    init.add_argument("--seed", type=int, default=20261015)
    init.add_argument("--theme", default="lishi")
    init.add_argument("--name")
    step = sub.add_parser("step", help="向前推演")
    step.add_argument("--days", type=int, default=10)
    sub.add_parser("status", help="查看当前世界")
    sub.add_parser("people", help="查看村民与目标")
    events = sub.add_parser("events", help="查看最近的不可变事件")
    events.add_argument("--limit", type=int, default=15)
    cause = sub.add_parser("why", help="追溯一条事件的因果链")
    cause.add_argument("id", type=int)
    act = sub.add_parser("intervene", help="有代价的作者干预")
    act.add_argument("action", choices=("relief", "reforest"), help="拨粮或植树")
    act.add_argument("amount", type=float, help="拨粮数量或植树所耗薪柴，1~100")
    inj = sub.add_parser("inject", help="上帝面板：凭空注入一条自定义事件（只追加、不改写）")
    inj.add_argument("summary", help="事件描述，1~240 字")
    inj.add_argument("--kind", default="author_event", help="事件类型标识，默认 author_event")
    inj.add_argument("--place", dest="place_kind", default="settlement",
                     choices=("none", "settlement", "region", "faction"), help="地点类型")
    inj.add_argument("--place-id", type=int, default=None, help="地点 id，缺省为当前聚落")
    inj.add_argument("--visibility", default="public", choices=("public", "private", "secret"))
    seal = sub.add_parser("seal", help="把当前世界状态封印进不可改写历史")
    seal.add_argument("--label", default="", help="封印标签（可选）")
    sub.add_parser("state", help="核验当前状态是否与最近一次封印一致")
    rec = sub.add_parser("reconstruct", help="从最近（或指定）状态存档点重建并查看漂移")
    rec.add_argument("--id", type=int, default=None, help="状态存档点编号，缺省取最近一个")
    a = ap.parse_args()
    if a.cmd == "init":
        create(a.db, a.seed, a.theme, a.name)
        return
    store, guard, cx = open_world(a.db)
    try:
        if a.cmd == "step":
            if not 1 <= a.days <= 365:
                raise ValueError("每次推演天数须为 1~365")
            ids = load_ids(store)
            w = store.one("SELECT seed FROM world WHERE id=1")
            sim.run(store, cx, guard, ids, a.days, w["seed"], verbose=False)
            print(json.dumps(snapshot(store), ensure_ascii=False, indent=2))
        elif a.cmd == "status":
            print(json.dumps(snapshot(store), ensure_ascii=False, indent=2))
        elif a.cmd == "people":
            for p in store.all("SELECT id, name, age, occupation, goal_stack FROM character "
                               "WHERE status='alive' ORDER BY id"):
                print(f"#{p['id']} {p['name']} {p['age']}岁 {p['occupation']} · {json.loads(p['goal_stack'])[0]}")
        elif a.cmd == "events":
            if not 1 <= a.limit <= 100:
                raise ValueError("--limit 须为 1~100")
            for e in store.all("SELECT id, tick, kind, summary FROM event ORDER BY id DESC LIMIT ?", (a.limit,)):
                print(f"#{e['id']} 第{e['tick']//24}日 [{e['kind']}] {e['summary']}")
        elif a.cmd == "why":
            chain = cx.chain(a.id)
            if not chain:
                raise ValueError("未找到该事件")
            for e in chain:
                print(f"#{e['id']} 第{e['tick']//24}日 {e['summary']}")
        elif a.cmd == "intervene":
            eid = intervene(store, cx, a.action, a.amount)
            print(f"干预已记入不可改写历史：事件 #{eid}")
            print(json.dumps(snapshot(store), ensure_ascii=False, indent=2))
        elif a.cmd == "inject":
            from engine import intervention
            result = intervention.inject_event(store, cx, {
                "summary": a.summary, "kind": a.kind, "place_kind": a.place_kind,
                "place_id": a.place_id, "visibility": a.visibility})
            print(f"已注入事件 #{result['event_id']}：{result['summary']}")
        elif a.cmd == "seal":
            from engine import projection
            r = projection.seal_state(store, cx, a.label)
            print(f"已封印世界状态：事件 #{r['event_id']}，{len(r['tables'])} 张状态表，指纹 {r['digest'][:12]}…")
        elif a.cmd == "state":
            from engine import projection
            v = projection.verify_state(store, cx)
            if not v["sealed"]:
                print("尚未封印过状态。运行 `play.py seal` 可把当前状态写进不可改写历史。")
            else:
                print(f"最近封印：事件 #{v['seal_event_id']}（第 {v['seal_tick'] // 24} 日）")
                print("状态一致：没被动过" if v["match"] else f"状态漂移的表：{v['drifted']}")
                print(f"哈希链 {v.get('chain_ok')} / 锚点 {v.get('anchor_ok')}")
        elif a.cmd == "reconstruct":
            from engine import projection
            at = projection.state_at(store, a.id)
            if not at.get("checkpoint"):
                print("尚无状态存档点。运行 `play.py seal` 可把当前状态存成存档点。")
            else:
                print(f"存档点 #{at['checkpoint']}（第 {at['tick'] // 24} 日）"
                      f"· 快照自校验 {at['verified']} · 事件见证 {at['attested_by_event']}")
                print("重建的各表行数：" + "、".join(
                    f"{n} {len(t['rows'])}" for n, t in at["tables"].items()))
                s = projection.since(store, a.id)
                print(f"自该点以来事件 {s['events_since']} 条；漂移的表：{s['drifted']}")
    except Exception:
        store.con.rollback()
        raise
    finally:
        store.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.Error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
