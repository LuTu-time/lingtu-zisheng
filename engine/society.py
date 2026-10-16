# -*- coding: utf-8 -*-
"""
社会层：民心、秩序、人口、角色生命周期、人际网络、势力博弈
（M001 档案 / M002 动机 / M003 人际 / M012 势力 / M013 结盟背信 / M045 聚落 / M104 生命周期）。

这一层回答的是「粮食不够之后会怎样」：
民心先跌，接着秩序松动，然后有人走 —— 而不是人口数字直接掉。

两条必须守住的设计：
  1. 人口不是独立变量，它恒等于「在村存活角色数」。出生必须真的创建一个角色，
     否则人口与角色表会脱钩，推演越久错得越离谱。
  2. 世界恶化后必须存在出路。民心触底时恐慌外流要显著加速，
     让「人口下降 → 需求下降 → 恢复平衡」这条负反馈能真正生效，
     而不是卡在最低值上再也出不来。
"""
import json

from .base import clamp, chance, unit, pick, Calendar

MORALE_HIT = 0.060       # 缺粮对民心的每日打击上限
MORALE_RECOVER = 0.025
MORALE_ANCHOR = 0.62     # 中性锚点：不挨饿也回不到满分，日子总有别的磨
ORDER_HIT = 0.035
ORDER_RECOVER = 0.012
ORDER_ANCHOR = 0.66
DEATH_BASE = 0.0030
BIRTH_RATE = 0.15        # 民心每高出 0.50 一个单位带来的日添丁概率系数
MIGRATE_BASE = 0.06      # 常态外流
MIGRATE_PANIC = 0.22     # 民心跌破 0.25 后的恐慌外流
ADULT_AGE = 14

CHILD_NAMES = ["阿禾", "阿石", "小满", "谷生", "阿宁", "小穗", "阿水", "麦儿",
               "阿松", "小豆", "谷雨", "阿苓", "小椴", "阿穗", "石头", "小满儿"]


def _morale_step(s, eco):
    need = max(eco["food_need"], 1e-6)
    if eco["shortage"] <= 0:
        d = MORALE_RECOVER * (MORALE_ANCHOR - s["morale"])
    else:
        d = -MORALE_HIT * (eco["shortage"] / need)
    return clamp(s["morale"] + d, 0.02, 0.98)


def _order_step(s, eco):
    need = max(eco["food_need"], 1e-6)
    if eco["shortage"] <= 0:
        d = ORDER_RECOVER * (ORDER_ANCHOR - s["order_value"])
    else:
        d = -ORDER_HIT * (eco["shortage"] / need)
    return clamp(s["order_value"] + d, 0.02, 0.98)


def _recount(store, sid):
    """人口恒等于在村存活角色数 —— 单一事实来源。"""
    return store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'", (sid,))


def settle_society(store, cx, guard, tick, seed, ids, st, eco, log=None):
    log = log or (lambda m: None)
    sid = ids["settlement_id"]
    s = store.one("SELECT * FROM settlement WHERE id=?", (sid,))
    if s is None:
        return {}

    old_morale, old_order = s["morale"], s["order_value"]
    old_pop = _recount(store, sid)
    morale = _morale_step(s, eco)
    order = _order_step(s, eco)
    day = Calendar.day(tick)

    # ---- 年岁增长（M104） ----
    if Calendar.is_new_year(tick) and tick > 0:
        store.con.execute("UPDATE character SET age = age + 1 WHERE status='alive'")
        grown = store.con.execute(
            "UPDATE character SET occupation='农人', updated_tick=? "
            "WHERE settlement_id=? AND status='alive' AND occupation='幼儿' AND age>=?",
            (tick, sid, ADULT_AGE)).rowcount
        if grown:
            cx.emit(tick, "coming_of_age", f"{grown} 个孩子长成了能下地的劳力",
                    cause=None, effects={"new_laborers": grown},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")

    # ---- 新生：真的创建角色，而不是只加计数 ----
    if morale > 0.56 and old_pop < s["housing"]:
        p = BIRTH_RATE * (morale - 0.50)
        if chance(seed, p, "birth", day):
            name = pick(seed, CHILD_NAMES, "childname", day)
            store.insert("character", {
                "settlement_id": sid, "faction_id": ids["villager_fid"],
                "name": name, "occupation": "幼儿", "age": 0,
                "health": round(clamp(0.80 + unit(seed, "chp", day) * 0.15, 0.3, 1.0), 2),
                "status": "alive",
                "profile": json.dumps({"origin": "生于本村", "look": "尚在襁褓",
                                       "habit": "爱哭", "obsession": "—",
                                       "fear": "陌生面孔", "secret": "—"},
                                      ensure_ascii=False),
                "traits": json.dumps({
                    "勤勉": round(0.4 + unit(seed, "cd", day) * 0.3, 2),
                    "谨慎": round(0.3 + unit(seed, "cc", day) * 0.3, 2),
                    "好斗": round(unit(seed, "ca", day) * 0.4, 2),
                    "仁厚": round(0.4 + unit(seed, "ck", day) * 0.3, 2),
                    "贪利": round(unit(seed, "cg", day) * 0.4, 2)}, ensure_ascii=False),
                "needs": json.dumps({"food": 0.6, "safety": 0.7, "wood": 0.5},
                                    ensure_ascii=False),
                "goal_stack": json.dumps(["长大成人"], ensure_ascii=False),
                "born_tick": tick, "updated_tick": tick})
            cx.emit(tick, "birth", f"村东头添丁，唤作{name}",
                    cause=st.cause_shortage if morale < 0.6 else None,
                    effects={"population": [old_pop, old_pop + 1]},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")

    # ---- 外流：民心越低走得越多，触底时恐慌 ----
    elif morale < 0.42:
        base = MIGRATE_PANIC if morale < 0.25 else MIGRATE_BASE
        p = base * (0.42 - morale)
        if chance(seed, p, "migrate", day):
            # 注意：这里**不能**用 SQL 的 RANDOM() 来挑人。
            # 那个函数不受种子控制，会让同一个 world 每次跑出不一样的历史 ——
            # 整个引擎的随机性都必须来自 (seed, 维度, 序号) 的哈希派生。
            rows = store.all(
                "SELECT id, name, occupation FROM character "
                "WHERE settlement_id=? AND status='alive' AND occupation NOT IN ('村长') "
                "ORDER BY (occupation='老弱') DESC, id", (sid,))
            weak = [r for r in rows if r["occupation"] == "老弱"]
            pool = weak or rows
            row = pool[min(len(pool) - 1,
                           int(unit(seed, "migpick", day) * len(pool)))] if pool else None
            if row and _recount(store, sid) > guard.pop_floor:
                store.update("character", row["id"], {"status": "left", "updated_tick": tick})
                now_pop = _recount(store, sid)
                eid = cx.emit(
                    tick, "migration",
                    f"{row['name']}收拾行装离开了村子 —— 这里待不下去了",
                    cause=st.cause_shortage,
                    effects={"population": [old_pop, now_pop],
                             "morale": [round(old_morale, 3), round(morale, 3)]},
                    place_kind="settlement", place_id=sid,
                    actor_kind="character", actor_id=row["id"],
                    participants=[{"kind": "character", "id": row["id"]}])
                st.cause_migration = eid

    # ---- 角色：生老病死（G6 死亡必须可回溯） ----
    winter = Calendar.season_index(tick) == 3
    for row in store.all(
            "SELECT id, name, age, health FROM character "
            "WHERE settlement_id=? AND status='alive' ORDER BY id", (sid,)):
        if row["age"] < 46:
            continue
        p = DEATH_BASE * ((row["age"] - 45) / 25.0) * (1.5 if winter else 0.7)
        if eco["shortage"] > 0:
            p *= 1.4
        if chance(seed, p, "death", row["id"], day):
            eid = cx.emit(
                tick, "death",
                f"{row['name']}（{row['age']}岁）亡故"
                + ("，冬寒加上吃不饱" if winter else ""),
                cause=st.cause_shortage,
                effects={"health": [row["health"], 0.0], "status": ["alive", "dead"]},
                place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=row["id"])
            if guard.require_death_cause(eid, row["id"]):
                store.update("character", row["id"],
                             {"status": "dead", "health": 0.0,
                              "death_event_id": eid, "died_tick": tick})

    # ---- 人际网络：低概率的恩怨生成（M003） ----
    if chance(seed, 0.02, "social", day):
        rows = store.all(
            "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
            "AND occupation != '幼儿' ORDER BY id", (sid,))
        if len(rows) >= 2:
            a_i = int(unit(seed, "rel", day) * len(rows))
            b_i = int(unit(seed, "rel2", day) * len(rows))
            if a_i != b_i:
                a, b = rows[a_i], rows[b_i]
                kind = pick(seed, ["怨", "恩", "忌", "爱"], "relkind", day)
                store.con.execute(
                    "INSERT OR REPLACE INTO relation "
                    "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
                    "VALUES ('character', ?, 'character', ?, ?, ?, ?, ?, ?)",
                    (a["id"], b["id"], kind,
                     round(unit(seed, "relv", day) * 0.7 + 0.2, 2),
                     round(unit(seed, "relp", day), 2), "因日常琐事而起", tick))
                st.social_events = getattr(st, "social_events", 0) + 1

    # ---- 回写 ----
    pop = guard.clamp_population(sid, _recount(store, sid), tick)
    able = store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('老弱','幼儿')", (sid,))
    store.update("settlement", sid, {
        "population": pop, "morale": round(morale, 4),
        "order_value": round(order, 4), "households": max(1, pop // 3),
        "updated_tick": tick})
    store.update("faction", ids["villager_fid"], {"manpower": able, "updated_tick": tick})

    # ---- 势力关系漂移（M013）：猎物越少，冲突越烈 ----
    game = eco["game"]
    drift = -0.010 if game < 0.25 else (0.004 if game > 0.5 else 0.0)
    if drift:
        store.con.execute(
            "UPDATE relation SET intensity = MAX(-1.0, MIN(1.0, intensity + ?)), updated_tick=? "
            "WHERE from_kind='faction' AND from_id=? AND to_kind='faction' AND to_id=? "
            "AND kind='attitude'",
            (drift, tick, ids["villager_fid"], ids["beast_fid"]))

    # 野兽武力随猎物丰歉波动
    store.con.execute(
        "UPDATE faction SET martial = MAX(0.15, MIN(0.9, ?)), updated_tick=? WHERE id=?",
        (round(0.30 + 0.55 * game, 3), tick, ids["beast_fid"]))

    return {"morale": morale, "order": order, "population": pop, "able": able}
