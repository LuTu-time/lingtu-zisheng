# -*- coding: utf-8 -*-
"""Deterministic, low-frequency village character actions."""
import json

from .base import Calendar, clamp, unit
from .society import ADULT_AGE


ACTIONS = {"help": ("恩", 0.12), "ask": ("恩", 0.04),
           "quarrel": ("怨", -0.12), "socialize": ("恩", 0.06)}


def settle_autonomy(store, cx, guard, tick, seed, ids):
    """One possible encounter per day; all choices come from persisted state."""
    day = Calendar.day(tick)
    if unit(seed, "autonomy-day", day) >= 0.25:
        return None
    sid = ids["settlement_id"]
    has_player = store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_identity'")
    exclude_player = (" AND NOT EXISTS (SELECT 1 FROM player_identity p WHERE p.character_id=c.id)"
                      if has_player else "")
    people = store.all(
        "SELECT c.* FROM character c WHERE c.settlement_id=? AND c.status='alive' "
        "AND c.age>=?" + exclude_player + " ORDER BY c.id", (sid, ADULT_AGE))
    if len(people) < 2:
        return None
    ai = int(unit(seed, "autonomy-actor", day) * len(people))
    actor = people[ai]
    others = [p for p in people if p["id"] != actor["id"]]
    target = others[int(unit(seed, "autonomy-target", day, actor["id"]) * len(others))]
    traits = json.loads(actor["traits"] or "{}")
    needs = json.loads(actor["needs"] or "{}")
    target_needs = json.loads(target["needs"] or "{}")
    goals = json.loads(actor["goal_stack"] or "[]")
    goal = goals[0] if goals else "照顾村中日常"
    recent = store.one(
        "SELECT m.content, e.id AS cause_id, e.kind, e.depth FROM memory m "
        "LEFT JOIN event e ON e.id=m.event_id WHERE m.owner_id=? "
        "ORDER BY m.tick DESC, m.id DESC LIMIT 1", (actor["id"],))
    relation = store.one(
        "SELECT kind, intensity FROM relation WHERE from_kind='character' AND from_id=? "
        "AND to_kind='character' AND to_id=? ORDER BY updated_tick DESC, id DESC LIMIT 1",
        (actor["id"], target["id"]))
    stock = store.one(
        "SELECT id, amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
        "AND resource_type='grain'", (sid,))
    grain = stock["amount"] if stock else 0.0
    memory_kind = recent["kind"] if recent else None
    hostility = relation and (relation["kind"] in ("怨", "忌") or relation["intensity"] < -0.2)
    hungry_goal = any(word in goal for word in ("冬", "粮", "吃", "饥"))
    if grain >= 0.5 and target_needs.get("food", 1) < 0.8 and traits.get("仁厚", 0) >= 0.5:
        action = "help"
    elif needs.get("food", 1) < 0.7 or (hungry_goal and grain < 1):
        action = "ask"
    elif hostility or (memory_kind in ("feud", "autonomy_quarrel") and traits.get("好斗", 0) > 0.35):
        action = "quarrel"
    else:
        action = "socialize"
    kind, delta = ACTIONS[action]
    old_relations = []
    for source, dest, change in ((actor, target, delta), (target, actor, delta * 0.75)):
        old = store.one(
            "SELECT intensity FROM relation WHERE from_kind='character' AND from_id=? "
            "AND to_kind='character' AND to_id=? AND kind=?",
            (source["id"], dest["id"], kind))
        before = old["intensity"] if old else 0.0
        after = round(clamp(before + change, -1, 1), 3)
        old_relations.append({"from_id": source["id"], "to_id": dest["id"],
                              "kind": kind, "intensity": [before, after]})
    reason = {"goal": goal, "traits": {"仁厚": traits.get("仁厚", 0),
                                         "好斗": traits.get("好斗", 0)},
              "food_need": needs.get("food"), "target_food_need": target_needs.get("food"),
              "grain_available": grain, "recent_memory": recent["content"] if recent else None,
              "prior_relation": dict(relation) if relation else None}
    effects = {"reason": reason, "relations": old_relations}
    if action == "help":
        improved = round(clamp(target_needs["food"] + 0.08, 0, 1), 3)
        effects["grain"] = [grain, grain - 0.5]
        effects["target_food_need"] = [target_needs["food"], improved]
    descriptions = {"help": "分给了半份村中存粮", "ask": "请求帮忙渡过难关",
                    "quarrel": "因日常分歧争执", "socialize": "交谈了村中近况"}
    summary = f"{actor['name']}向{target['name']}{descriptions[action]}"
    cause = recent["cause_id"] if recent and recent["cause_id"] and recent["depth"] < guard.max_depth else None
    eid = cx.emit(tick, "autonomy_" + action, summary, cause=cause,
                  effects=effects, place_kind="settlement", place_id=sid,
                  actor_kind="character", actor_id=actor["id"],
                  participants=[{"kind": "character", "id": actor["id"]},
                                {"kind": "character", "id": target["id"]}])
    if eid is None:
        return None
    if action == "help":
        store.update("stock", stock["id"], {"amount": grain - 0.5, "updated_tick": tick})
        target_needs["food"] = improved
        store.update("character", target["id"],
                     {"needs": json.dumps(target_needs, ensure_ascii=False), "updated_tick": tick})
    for change in old_relations:
        store.con.execute(
            "INSERT INTO relation (from_kind,from_id,to_kind,to_id,kind,intensity,publicity,note,updated_tick) "
            "VALUES ('character',?,'character',?,?,?,?,?,?) "
            "ON CONFLICT(from_kind,from_id,to_kind,to_id,kind) DO UPDATE SET "
            "intensity=excluded.intensity,note=excluded.note,updated_tick=excluded.updated_tick",
            (change["from_id"], change["to_id"], kind, change["intensity"][1],
             0.7, f"事件#{eid}：{summary}", tick))
    for owner, other in ((actor, target), (target, actor)):
        store.insert("memory", {"owner_id": owner["id"],
                                "content": f"我与{other['name']}经历了：{summary}",
                                "importance": 0.7 if action == "quarrel" else 0.55,
                                "tick": tick, "event_id": eid, "decay": 1.0})
    return eid
