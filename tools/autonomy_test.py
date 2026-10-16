# -*- coding: utf-8 -*-
"""Autonomous village actions: integration and history checks."""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import sim, worldgen  # noqa: E402
from engine.base import unit  # noqa: E402
from engine.autonomy import settle_autonomy  # noqa: E402
from engine.causality import Causality  # noqa: E402
from engine.guardrails import Guardrail  # noqa: E402
from engine.store import Store  # noqa: E402


DIR = tempfile.mkdtemp(prefix="lingtu-autonomy-", dir="D:/")
with open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf-8") as f:
    SCHEMA = f.read()


def open_world(path, seed=20261015, player=False):
    if not os.path.exists(path):
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.close()
        store = Store(path)
        ids = worldgen.generate(store, seed, verbose=False)
        if player:
            store.con.execute("CREATE TABLE player_identity (id INTEGER PRIMARY KEY, character_id INTEGER)")
            store.con.execute("INSERT INTO player_identity VALUES (1, 1)")
        store.commit()
    else:
        store = Store(path)
        sid = store.scalar("SELECT id FROM settlement LIMIT 1")
        ids = {"settlement_id": sid, "region_id": store.scalar("SELECT region_id FROM settlement WHERE id=?", (sid,)),
               "villager_fid": store.scalar("SELECT faction_id FROM settlement WHERE id=?", (sid,)),
               "beast_fid": store.scalar("SELECT id FROM faction WHERE kind='野兽'")}
    guard = Guardrail(store)
    cx = Causality(store, guard)
    guard.sync_horizon()
    return store, ids, guard, cx


def snapshot(store):
    return tuple(tuple(row) for row in store.all("SELECT * FROM event ORDER BY id")), tuple(
        tuple(row) for row in store.all("SELECT * FROM memory ORDER BY id")), tuple(
        tuple(row) for row in store.all("SELECT * FROM relation ORDER BY id")), tuple(
        tuple(row) for row in store.all("SELECT * FROM character ORDER BY id")), tuple(
        tuple(row) for row in store.all("SELECT * FROM stock ORDER BY id"))


def check_actions(store, cx, player=False):
    events = store.all("SELECT * FROM event WHERE kind LIKE 'autonomy_%' ORDER BY id")
    assert events, "无人操作应自动产生事件"
    for event in events:
        participants = json.loads(event["participants"])
        assert len(participants) == 2 and participants[0]["id"] != participants[1]["id"]
        if player:
            assert all(p["id"] != 1 for p in participants)
        effects = json.loads(event["effects"])
        assert effects["reason"]["goal"] and len(effects["relations"]) == 2
        memories = store.all("SELECT owner_id FROM memory WHERE event_id=? ORDER BY owner_id", (event["id"],))
        assert [r["owner_id"] for r in memories] == sorted(p["id"] for p in participants)
        for change in effects["relations"]:
            assert change["kind"] in ("恩", "怨")
            assert store.one("SELECT 1 FROM relation WHERE from_kind='character' AND from_id=? "
                             "AND to_kind='character' AND to_id=? AND kind=?",
                             (change["from_id"], change["to_id"], change["kind"]))
        if event["kind"] == "autonomy_help":
            assert effects["grain"][0] - effects["grain"][1] == 0.5
            assert effects["target_food_need"][1] >= effects["target_food_need"][0]
    assert cx.verify_chain()[0] and cx.verify_anchor()["ok"]
    return len(events)


def check_branches():
    seed = 20261015
    for action in ("help", "ask", "quarrel", "socialize"):
        path = os.path.join(DIR, action + ".db")
        store, ids, guard, cx = open_world(path, seed)
        people = store.all("SELECT id FROM character ORDER BY id")
        day = next(d for d in range(1, 100) if unit(seed, "autonomy-day", d) < 0.25)
        ai = int(unit(seed, "autonomy-actor", day) * len(people))
        actor = people[ai]["id"]
        others = [p["id"] for p in people if p["id"] != actor]
        target = others[int(unit(seed, "autonomy-target", day, actor) * len(others))]
        store.update("character", actor, {"traits": json.dumps({"仁厚": 0.2, "好斗": 0.1}),
                                          "needs": json.dumps({"food": 0.9}),
                                          "goal_stack": json.dumps(["修好院墙"])})
        store.update("character", target, {"needs": json.dumps({"food": 0.9})})
        if action == "help":
            store.update("character", actor, {"traits": json.dumps({"仁厚": 0.9})})
            store.update("character", target, {"needs": json.dumps({"food": 0.3})})
        elif action == "ask":
            store.update("character", actor, {"needs": json.dumps({"food": 0.2})})
            store.con.execute("UPDATE stock SET amount=0 WHERE owner_kind='settlement' "
                              "AND owner_id=? AND resource_type='grain'", (ids["settlement_id"],))
        elif action == "quarrel":
            store.insert("relation", {"from_kind": "character", "from_id": actor,
                                       "to_kind": "character", "to_id": target,
                                       "kind": "怨", "intensity": -0.6})
        elif action == "socialize":
            store.insert("memory", {"owner_id": actor, "content": "记得上回在村口交谈",
                                    "tick": 0})
        grain_before = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                    "AND owner_id=? AND resource_type='grain'", (ids["settlement_id"],))
        eid = settle_autonomy(store, cx, guard, day * 24, seed, ids)
        event = store.one("SELECT * FROM event WHERE id=?", (eid,))
        assert event and event["kind"] == "autonomy_" + action, action
        reason = json.loads(event["effects"])["reason"]
        if action == "socialize":
            assert reason["recent_memory"] == "记得上回在村口交谈"
        grain_after = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                   "AND owner_id=? AND resource_type='grain'", (ids["settlement_id"],))
        assert grain_after == grain_before - (0.5 if action == "help" else 0)
        check_actions(store, cx)
        store.close()


def main():
    check_branches()
    seed = 20261015
    whole = os.path.join(DIR, "whole.db")
    split = os.path.join(DIR, "split.db")
    player_path = os.path.join(DIR, "player.db")
    store, ids, guard, cx = open_world(whole, seed)
    sim.run(store, cx, guard, ids, 40, seed, verbose=False)
    count = check_actions(store, cx)
    assert count <= 40
    full = snapshot(store)
    store.close()
    for days in (10, 10, 20):
        store, ids, guard, cx = open_world(split, seed)
        sim.run(store, cx, guard, ids, days, seed, verbose=False)
        store.close()
    store, ids, guard, cx = open_world(split, seed)
    assert snapshot(store) == full, "整跑与分段事件和末态必须逐条相同"
    assert check_actions(store, cx) == count
    store.close()

    store, ids, guard, cx = open_world(player_path, seed, player=True)
    sim.run(store, cx, guard, ids, 40, seed, verbose=False)
    check_actions(store, cx, player=True)
    assert not store.one("SELECT 1 FROM memory WHERE owner_id=1")
    assert not store.one("SELECT 1 FROM relation WHERE from_kind='character' AND from_id=1 "
                         "OR to_kind='character' AND to_id=1")
    before = snapshot(store)
    guard.event_cap = 0
    for d in range(41, 70):
        assert settle_autonomy(store, cx, guard, d * 24, seed, ids) is None
    assert guard.trigger_count.get("G4", 0) > 0
    assert snapshot(store) == before, "guard 拒绝时不得写角色状态/记忆/关系/库存"
    store.close()
    print(f"PASS: 自动事件 {count} 条、双人记忆/关系、排除玩家、guard 拒绝无副作用")
    print("PASS: 同种子 40 日整跑=10+10+20 分段（完整事件、记忆、关系、角色和库存逐条一致）")
    print(f"PASS: 哈希历史链和世界锚点；测试库保留在 {DIR}")


if __name__ == "__main__":
    main()
