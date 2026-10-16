# -*- coding: utf-8 -*-
"""
世界种子与初始生成（M099）+ 角色基础档案（M001）。

同一个 seed 必须生成同一个世界。所有随机值走 base 的确定性随机，
不依赖调用顺序，因此可以放心地改动生成顺序而不破坏已有存档的可复现性。
"""
import json

from .base import unit, between, pick, jitter, clamp, Calendar

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜")
GIVEN = ["安", "平", "山", "河", "禾", "粟", "石", "松", "柏", "川", "言", "信",
         "守", "义", "田", "苗", "青", "白", "林", "水", "生", "茂", "丰", "顺",
         "望", "归", "宁", "康", "有", "余", "拾", "得", "长", "庚", "冬", "夏"]

ORIGINS = ["本村世代农户", "逃荒落籍的外乡人", "退役的边军士卒", "没落行商之后",
           "猎户出身，随父入山", "镇上迁来的手艺人"]
LOOKS = ["面色黝黑，手掌粗大", "身形精瘦，眼神锐利", "眉目温和，说话慢",
         "左颊有旧疤，寡言", "肩背宽厚，走路带风", "手指细长，惯于摆弄器物"]
HABITS = ["天不亮就下地", "入夜必巡一遍屋后", "逢集日必去镇上", "爱在村口听闲话",
          "随身带一把小刀", "饭前必先喂牲口"]
OBSESSIONS = ["想给儿子攒够聘礼", "要把祖上那块地赎回来", "想在山里寻一处水源",
              "盼着有朝一日当上里正", "只想安安稳稳过完这一生"]
FEARS = ["怕闹饥荒", "怕野兽下山", "怕被征去服徭役", "怕旧仇家找上门", "怕独子夭折"]
SECRETS = ["去年私藏了半袋谷子", "与邻村某人有旧怨未了", "曾在山里见过不该见的东西",
           "欠着镇上钱庄一笔利钱", "替人顶过一个罪名"]
GOALS_POOL = ["熬过今年冬天", "给家里多置两亩地", "让孩子识字", "修好塌了的院墙",
              "再猎一头鹿换钱", "把欠债还清"]

OCCUPATIONS = [
    ("村长", 1),
    ("农人", 9),
    ("猎人", 3),
    ("工匠", 2),
    ("牧人", 1),
    ("老弱", 3),
]


def _name(seed, i):
    return pick(seed, SURNAMES, "sur", i) + pick(seed, GIVEN, "giv", i) + \
        (pick(seed, GIVEN, "giv2", i) if unit(seed, "two", i) < 0.45 else "")


def _traits(seed, i, occ):
    t = {
        "勤勉": round(clamp(jitter(seed, 0.62, 0.18, "diligent", i), 0.05, 0.98), 2),
        "谨慎": round(clamp(jitter(seed, 0.52, 0.20, "cautious", i), 0.05, 0.98), 2),
        "好斗": round(clamp(jitter(seed, 0.32, 0.20, "aggressive", i), 0.02, 0.95), 2),
        "仁厚": round(clamp(jitter(seed, 0.58, 0.18, "kind", i), 0.05, 0.98), 2),
        "贪利": round(clamp(jitter(seed, 0.45, 0.18, "greedy", i), 0.05, 0.95), 2),
    }
    if occ == "猎人":
        t["好斗"] = round(min(0.95, t["好斗"] + 0.18), 2)
        t["谨慎"] = round(min(0.98, t["谨慎"] + 0.10), 2)
    if occ == "村长":
        t["仁厚"] = round(min(0.98, t["仁厚"] + 0.12), 2)
        t["贪利"] = round(max(0.05, t["贪利"] - 0.08), 2)
    return t


def generate(store, seed, theme="lishi", world_name=None, log=None, verbose=True):
    log = log or (lambda m: None)
    tick0 = 0

    # ---- 世界 ----
    world_name = world_name or pick(seed, ["青溪", "白水", "长岗", "石梁", "芦荡",
                                           "柳坞", "云岭"], "world") + "境"
    store.insert("world", {
        "id": 1,
        "seed": seed,
        "name": world_name,
        "theme": theme,
        "tick": tick0,
        "tick_mode": "normal",
        "calendar": json.dumps({
            "hours_per_day": 24, "days_per_xun": 10,
            "xun_per_season": 3, "seasons_per_year": 4},
            ensure_ascii=False),
    })
    if verbose:
        log(f"世界「{world_name}」已生成（seed={seed}，题材={theme}）")

    # ---- 势力（先建，region 需要引用） ----
    villager_fid = store.insert("faction", {
        "name": "青溪村民", "kind": "村民",
        "goal": "守住田地与水源，让村里人活下去",
        "manpower": 0, "territory": 1, "cohesion": 0.62,
        "martial": round(between(seed, 0.18, 0.34, "villager_martial"), 2),
        "status": "active", "color": "#534AB7", "updated_tick": tick0,
    })
    beast_fid = store.insert("faction", {
        "name": "山林野兽", "kind": "野兽",
        "goal": "扩大领地，觅食",
        "manpower": int(between(seed, 22, 38, "beast_man")),
        "territory": 2, "cohesion": 0.78,
        "martial": round(between(seed, 0.42, 0.62, "beast_martial"), 2),
        "status": "active", "color": "#A32D2D", "updated_tick": tick0,
    })

    # ---- 地域 ----
    region_id = store.insert("region", {
        "world_id": 1,
        "name": pick(seed, ["青溪谷", "白水滩", "长岗坡", "石梁坳"], "region"),
        "terrain": "河谷",
        "climate": "温带",
        "forest": round(clamp(jitter(seed, 0.72, 0.06, "forest"), 0.35, 0.92), 2),
        "game_density": round(clamp(jitter(seed, 0.58, 0.07, "game"), 0.30, 0.85), 2),
        "water_health": round(clamp(jitter(seed, 0.84, 0.05, "water"), 0.5, 0.98), 2),
        "fertility": round(clamp(jitter(seed, 0.70, 0.06, "fertility"), 0.4, 0.9), 2),
        "owner_faction": villager_fid,
        "updated_tick": tick0,
    })

    # ---- 聚落 ----
    villagers = []
    for occ, n in OCCUPATIONS:
        villagers.extend([occ] * n)
    population = len(villagers)
    st_id = store.insert("settlement", {
        "region_id": region_id,
        "name": pick(seed, ["青溪村", "白水屯", "长岗堡"], "settle"),
        "tier": "村",
        "population": population,
        "households": max(1, population // 3),
        "order_value": round(clamp(jitter(seed, 0.66, 0.06, "order"), 0.3, 0.95), 2),
        "morale": round(clamp(jitter(seed, 0.62, 0.06, "morale"), 0.3, 0.95), 2),
        "housing": population + int(between(seed, 2, 8, "housing")),
        "faction_id": villager_fid,
        "founded_tick": tick0,
        "updated_tick": tick0,
    })

    # ---- 角色 ----
    for i, occ in enumerate(villagers):
        age = (int(between(seed, 44, 61, "age_head", i)) if occ == "村长" else
               int(between(seed, 52, 74, "age_old", i)) if occ == "老弱" else
               int(between(seed, 16, 47, "age", i)))
        store.insert("character", {
            "settlement_id": st_id,
            "faction_id": villager_fid,
            "name": _name(seed, i),
            "occupation": occ,
            "age": age,
            "health": round(clamp(jitter(seed, 0.88, 0.08, "health", i), 0.4, 1.0), 2),
            "status": "alive",
            "profile": json.dumps({
                "origin": pick(seed, ORIGINS, "origin", i),
                "look": pick(seed, LOOKS, "look", i),
                "habit": pick(seed, HABITS, "habit", i),
                "obsession": pick(seed, OBSESSIONS, "ob", i),
                "fear": pick(seed, FEARS, "fear", i),
                "secret": pick(seed, SECRETS, "secret", i),
            }, ensure_ascii=False),
            "traits": json.dumps(_traits(seed, i, occ), ensure_ascii=False),
            "needs": json.dumps({
                "food": round(between(seed, 0.55, 0.85, "nf", i), 2),
                "safety": round(between(seed, 0.55, 0.9, "ns", i), 2),
                "wood": round(between(seed, 0.4, 0.8, "nw", i), 2),
            }, ensure_ascii=False),
            "goal_stack": json.dumps([
                pick(seed, GOALS_POOL, "g1", i),
                pick(seed, GOALS_POOL, "g2", i),
            ], ensure_ascii=False),
            "born_tick": -age * 365 * 24,
            "updated_tick": tick0,
        })

    # ---- 资源存量 ----
    # decay_rate 是「每 tick 损耗率」，按日复利折算（见 ecology.settle_day）。
    # 参考量级：0.000012/tick ≈ 年损耗 10%；0.000060/tick（肉）≈ 年损耗 40%。
    stocks = [
        ("settlement", st_id, "grain", round(between(seed, 230, 300, "sg"), 1), 0.98, 0.000012),
        ("settlement", st_id, "wood", round(between(seed, 110, 160, "sw"), 1), 0.95, 0.000008),
        ("settlement", st_id, "meat", round(between(seed, 30, 60, "sm"), 1), 0.9, 0.000060),
        ("settlement", st_id, "medicine", round(between(seed, 8, 18, "sd"), 1), 0.9, 0.000050),
        ("faction", villager_fid, "grain", round(between(seed, 40, 70, "fg"), 1), 0.98, 0.000012),
        ("faction", villager_fid, "weapon", round(between(seed, 6, 14, "fw"), 1), 0.9, 0.0),
        ("faction", villager_fid, "coin", round(between(seed, 30, 80, "fc"), 1), 1.0, 0.0),
    ]
    for ok, oid, rt, amt, q, dec in stocks:
        store.insert("stock", {
            "owner_kind": ok, "owner_id": oid, "resource_type": rt,
            "amount": amt, "quality": q, "decay_rate": dec,
            "capacity": 1000000, "updated_tick": tick0,
        })

    # ---- 初始势力关系（有向：允许 A 亲 B 而 B 仇 A） ----
    for a, b, v in ((villager_fid, beast_fid, -0.62), (beast_fid, villager_fid, -0.50)):
        store.insert("relation", {
            "from_kind": "faction", "from_id": a,
            "to_kind": "faction", "to_id": b,
            "kind": "attitude", "intensity": v, "publicity": 0.9,
            "note": "世代相邻，争夺山林与猎物", "updated_tick": tick0,
        })

    store.update("faction", villager_fid,
                 {"manpower": sum(1 for o in villagers if o not in ("老弱",))})

    store.commit()
    if verbose:
        log(f"地域「{store.scalar('SELECT name FROM region WHERE id=?', (region_id,))}」"
            f"· 聚落「{store.scalar('SELECT name FROM settlement WHERE id=?', (st_id,))}」"
            f"· 角色 {population} 人 · 势力 2 个")

    return {"world_id": 1, "region_id": region_id, "settlement_id": st_id,
            "villager_fid": villager_fid, "beast_fid": beast_fid}
