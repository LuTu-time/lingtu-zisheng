# -*- coding: utf-8 -*-
"""
事件生成与冲突调度（M097 导演）。

没有导演，推演会陷入两种无聊：要么一切平稳（没有故事），
要么随机噪声满天飞（故事没有来由）。导演的职责是让意外事件
**有概率但不高频**，且尽量挂在已经存在的因果链上。

外生事件（天灾）允许没有父节点 —— 它是链条的起点。
"""
from .base import clamp, chance, unit, pick, Calendar

DISASTERS = [
    ("drought", "大旱", "溪水浅了，田里裂开指头宽的缝"),
    ("flood", "山洪", "一夜暴雨冲垮了半坡的田垄"),
    ("pest", "蝗灾", "蝗虫过境，青苗被啃得只剩秆"),
    ("frost", "早霜", "开春一场严霜，冻死了刚出芽的秧"),
]

DISASTER_LOSS = {"drought": 0.18, "flood": 0.16, "pest": 0.14, "frost": 0.11}


def settle_director(store, cx, guard, tick, seed, ids, st, eco, soc, log=None):
    sid, rid = ids["settlement_id"], ids["region_id"]
    day = Calendar.day(tick)
    fired = []

    # ---- 1. 自然灾害（低频，外生） ----
    if chance(seed, 0.0048, "disaster", day):
        kind, name, desc = pick(seed, DISASTERS, "dkind", day)
        g_row = store.one(
            "SELECT id, amount FROM stock WHERE owner_kind='settlement' "
            "AND owner_id=? AND resource_type='grain'", (sid,))
        lost = (g_row["amount"] * DISASTER_LOSS[kind]) if g_row else 0.0
        if g_row:
            store.con.execute(
                "UPDATE stock SET amount=MAX(0, amount-?), updated_tick=? WHERE id=?",
                (lost, tick, g_row["id"]))
        store.con.execute(
            "UPDATE region SET water_health=MAX(0.05, water_health-0.12), "
            "fertility=MAX(0.10, fertility-0.05), updated_tick=? WHERE id=?", (tick, rid))
        eid = cx.emit(
            tick, "disaster", f"{name}：{desc}，损粮 {lost:.0f}",
            cause=None, effects={"grain_lost": round(lost, 1), "disaster": name},
            place_kind="region", place_id=rid, actor_kind="nature")
        fired.append(eid)
        st.cause_disaster = eid

    # ---- 2. 野兽袭村：猎物越少越容易下山 ----
    game = eco["game"]
    p_raid = 0.013 * clamp(1.25 - game / 0.55, 0.0, 1.6)
    if chance(seed, p_raid, "raid", day):
        # ORDER BY id 是必需的：下面按确定性的哈希下标取人，
        # 行序一旦不稳，同一个世界就会跑出不同的人被拖走。
        rows = store.all(
            "SELECT id, name, occupation, health FROM character "
            "WHERE settlement_id=? AND status='alive' ORDER BY id", (sid,))
        victim = rows[min(len(rows) - 1,
                          int(unit(seed, "raidv", day) * len(rows)))] if rows else None
        killed = False
        if victim and chance(seed, 0.22, "raidkill", day):
            eid = cx.emit(
                tick, "death",
                f"{victim['name']}在夜里被野兽拖走，寻回时只剩一双鞋",
                cause=st.cause_game or st.cause_disaster,
                effects={"health": [victim["health"], 0.0], "status": ["alive", "dead"]},
                place_kind="settlement", place_id=sid,
                actor_kind="faction", actor_id=ids["beast_fid"])
            if guard.require_death_cause(eid, victim["id"]):
                store.update("character", victim["id"],
                             {"status": "dead", "health": 0.0,
                              "death_event_id": eid, "died_tick": tick})
                killed = True
        store.con.execute(
            "UPDATE settlement SET order_value=MAX(0.02, order_value-0.05), "
            "updated_tick=? WHERE id=?", (tick, sid))
        store.con.execute(
            "UPDATE settlement SET population=MAX(1, population-?) WHERE id=?",
            (1 if killed else 0, sid))
        eid = cx.emit(
            tick, "beast_raid",
            "野兽夜袭村口"
            + (f"，{victim['name']}没能躲过" if killed else "，咬死两只羊后遁入山林"),
            cause=st.cause_game,
            effects={"game_density": round(game, 3), "order": "下降"},
            place_kind="settlement", place_id=sid,
            actor_kind="faction", actor_id=ids["beast_fid"])
        fired.append(eid)

    # ---- 3. 村民冲突：秩序低时 ----
    if soc["order"] < 0.40 and chance(seed, 0.028, "feud", day):
        rows = store.all(
            "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
            "ORDER BY id", (sid,))
        if len(rows) >= 2:
            n = len(rows)
            a = rows[min(n - 1, int(unit(seed, "fa", day) * n))]
            b = rows[min(n - 1, int(unit(seed, "fb", day) * n))]
            if a["id"] != b["id"]:
                store.con.execute(
                    "INSERT OR REPLACE INTO relation "
                    "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
                    "VALUES ('character', ?, 'character', ?, '怨', ?, 0.85, ?, ?)",
                    (a["id"], b["id"], round(0.4 + unit(seed, "fv", day) * 0.4, 2),
                     "为一口粮争执", tick))
                eid = cx.emit(
                    tick, "feud",
                    f"{a['name']}与{b['name']}为分粮起了争执，动了手",
                    cause=st.cause_shortage,
                    place_kind="settlement", place_id=sid, actor_kind="character",
                    actor_id=a["id"],
                    participants=[{"kind": "character", "id": a["id"]},
                                  {"kind": "character", "id": b["id"]}])
                fired.append(eid)

    return fired
