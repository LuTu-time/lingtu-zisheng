# -*- coding: utf-8 -*-
"""
治理与社会（P1 社会骨架）：
  M046 户籍人口登记、M017 律法与审判裁决、M056 徭役劳役征召、
  M059 邻里社交与乡约自治、M052 水权分配与水利争端、
  M062 墓葬与丧葬墓穴、M048 山林荒地私垦拓殖。

设计原则：
  1. 全部使用确定性随机 (seed, 维度, 序号)，不依赖调用顺序。
  2. 所有状态变更都通过不可变事件记录，可在纪事里溯源。
  3. 新增表用 ensure_civics_schema 在运行时按需创建，旧存档非破坏性升级。
"""
from .base import clamp, chance, unit, pick, Calendar

# ---------- 运行时表结构（旧库按需补装） ----------
CIVICS_DDL = """
CREATE TABLE IF NOT EXISTS household (
    id INTEGER PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    head_character_id INTEGER REFERENCES character(id),
    member_count INTEGER NOT NULL DEFAULT 1,
    registered_tick INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_household_settlement ON household(settlement_id);

CREATE TABLE IF NOT EXISTS grave (
    id INTEGER PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    character_id INTEGER NOT NULL REFERENCES character(id),
    died_tick INTEGER NOT NULL,
    buried_tick INTEGER NOT NULL,
    location TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_grave_char ON grave(character_id);
"""

# ---------- 概率与阈值 ----------
HOUSEHOLD_REBUILD_PERIOD = 120      # 每年重登一次户籍
CORVEE_BASE = 0.30                  # 徭役征召基础概率（每季）
WATER_DISPUTE_BASE = 0.06           # 水权争端基础概率（每旬）
TRIAL_BASE = 0.05                   # 纠纷案件基础概率（秩序偏低时升高）
COMMUNITY_BASE = 0.04               # 邻里互助基础概率
RECLAIM_BASE = 0.015                # 私垦拓殖基础概率（每年）


def ensure_civics_schema(store):
    store.con.executescript(CIVICS_DDL)


# ---------- M046 户籍人口登记 ----------
def settle_household_registry(store, cx, ids, tick):
    """岁末重登户籍：按在村存活成年人重新编组户，记录户主与人数。"""
    ensure_civics_schema(store)
    sid = ids["settlement_id"]
    adults = store.all(
        "SELECT id, name, age FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿') ORDER BY id", (sid,))
    if not adults:
        return

    # 清掉旧年度户籍，重新登记
    store.con.execute("DELETE FROM household WHERE settlement_id=?", (sid,))

    # 以「家长 + 同户老弱幼」编组：每 3 名成年人带若干依附者为一户
    households = []
    i = 0
    while i < len(adults):
        head = adults[i]
        members = 1
        # 该户可包含后续最多 2 名成年劳动力 + 其依附者（简化为计数）
        i += 3
        members = 3 if i <= len(adults) else max(1, len(adults) - (i - 3))
        households.append((head["id"], head["name"], members))

    for head_id, head_name, members in households:
        store.con.execute(
            "INSERT INTO household(settlement_id, head_character_id, member_count, "
            "registered_tick, note) VALUES(?,?,?,?,?)",
            (sid, head_id, members, tick, f"户主{head_name}"))

    store.update("settlement", sid, {
        "households": len(households), "updated_tick": tick})

    cx.emit(tick, "household_register",
            f"岁末户籍重登：在村 {len(households)} 户",
            effects={"households": len(households),
                     "population": store.scalar(
                         "SELECT COUNT(*) FROM character WHERE settlement_id=? "
                         "AND status='alive'", (sid,))},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M062 墓葬与丧葬墓穴 ----------
def settle_burial(store, cx, ids, tick, seed, day):
    """为近期亡故者安排下葬，记录墓穴。死亡必须先在 society 里 emit death 事件。"""
    ensure_civics_schema(store)
    sid = ids["settlement_id"]
    # 找出本旬内死亡、且尚未入葬的角色
    dead = store.all(
        "SELECT id, name, died_tick FROM character "
        "WHERE settlement_id=? AND status='dead' AND died_tick>=? AND died_tick<?",
        (sid, max(0, tick - 240), tick + 1))
    for person in dead:
        if store.one("SELECT 1 FROM grave WHERE character_id=?", (person["id"],)):
            continue
        loc = pick(seed, ["村东坡地", "河湾槐树下", "祖坟岗", "后山松林"],
                   "graveloc", person["id"] + day)
        store.con.execute(
            "INSERT INTO grave(settlement_id, character_id, died_tick, buried_tick, location) "
            "VALUES(?,?,?,?,?)",
            (sid, person["id"], person["died_tick"], tick, loc))
        death_evt = store.scalar(
            "SELECT id FROM event WHERE kind='death' AND actor_id=? AND tick=?",
            (person["id"], person["died_tick"]))
        cx.emit(tick, "burial",
                f"{person['name']}入土为安，葬于{loc}",
                cause=death_evt, effects={"location": loc},
                place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M017 律法与审判裁决 ----------
def settle_law_trials(store, cx, ids, tick, seed, day):
    """
    当聚落秩序偏低时，低概率发生纠纷案件。
    由村中长者/村长裁断：依情节或罚役、或罚款（货币），严重扰乱则记怨。
    """
    ensure_civics_schema(store)
    sid = ids["settlement_id"]
    s = store.one("SELECT order_value, morale FROM settlement WHERE id=?", (sid,))
    if not s:
        return
    order = s["order_value"]
    # 秩序越低，纠纷越易发
    p = TRIAL_BASE * (1.0 + (0.6 - order) * 2.0)
    if not chance(seed, p, "trial", day):
        return

    suspects = store.all(
        "SELECT id, name, occupation FROM character WHERE settlement_id=? "
        "AND status='alive' AND occupation NOT IN ('幼儿','村长') ORDER BY id", (sid,))
    if len(suspects) < 2:
        return
    a = suspects[int(unit(seed, "trial_a", day) * len(suspects))]
    b = suspects[int(unit(seed, "trial_b", day) * len(suspects))]
    if a["id"] == b["id"]:
        return

    kind = pick(seed, ["争水", "斗殴", "偷盗", "赖账", "占地"], "trial_k", day)
    verdict = pick(seed, ["罚役三日", "罚粮五斗", "当庭责惩", "各打五十"], "trial_v", day)
    cx.emit(tick, "trial",
            f"村中发生{kind}纠纷，{a['name']}与{b['name']}对簿；裁断：{verdict}",
            effects={"kind": kind, "verdict": verdict,
                     "parties": [a["name"], b["name"]]},
            place_kind="settlement", place_id=sid,
            actor_kind="settlement",
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])
    # 罚粮从聚落库存扣减
    if "罚粮" in verdict:
        fine = 5.0
        g = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                      "AND owner_id=? AND resource_type='grain'", (sid,))
        if g and g["amount"] > fine:
            store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                              (fine, tick, g["id"]))


# ---------- M056 徭役劳役征召 ----------
def settle_corvee(store, cx, ids, tick, seed, day):
    """
    每季征召徭役：修缮工事、疏浚沟渠、修路等。
    征召会压低民心，但提升聚落设施（这里以秩序小幅上升体现）。
    """
    ensure_civics_schema(store)
    sid = ids["settlement_id"]
    if not chance(seed, CORVEE_BASE, "corvee", day):
        return
    able = store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿','老弱','村长')", (sid,))
    if not able:
        return
    conscript = max(1, int(able * 0.25))
    project = pick(seed, ["修葺村墙", "疏浚水渠", "拓宽村道", "整修仓廪"],
                   "corvee_p", day)
    cx.emit(tick, "corvee",
            f"村中征调 {conscript} 人服徭役：{project}",
            effects={"conscript": conscript, "project": project},
            place_kind="settlement", place_id=sid, actor_kind="settlement")
    # 徭役短暂压低民心，但以设施改善稍补秩序
    s = store.one("SELECT morale, order_value FROM settlement WHERE id=?", (sid,))
    if s:
        store.update("settlement", sid, {
            "morale": round(clamp(s["morale"] - 0.03, 0.02, 0.98), 4),
            "order_value": round(clamp(s["order_value"] + 0.02, 0.02, 0.98), 4),
            "updated_tick": tick})


# ---------- M059 邻里社交与乡约自治 ----------
def settle_community(store, cx, ids, tick, seed, day):
    """
    邻里互助：低概率发生乡约议事或互助事件，强化社区凝聚力（小幅回补民心/秩序）。
    """
    ensure_civics_schema(store)
    sid = ids["settlement_id"]
    if not chance(seed, COMMUNITY_BASE, "community", day):
        return
    residents = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿') ORDER BY id", (sid,))
    if len(residents) < 2:
        return
    a = residents[int(unit(seed, "com_a", day) * len(residents))]
    b = residents[int(unit(seed, "com_b", day) * len(residents))]
    if a["id"] == b["id"]:
        return
    act = pick(seed, ["帮工修屋", "分粮济急", "共育牲畜", "夜间围坐议事定乡约"],
               "com_act", day)
    cx.emit(tick, "community",
            f"{a['name']}与{b['name']}{act}，乡邻和睦",
            effects={"act": act, "parties": [a["name"], b["name"]]},
            place_kind="settlement", place_id=sid,
            actor_kind="character", actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])
    m = store.scalar("SELECT morale FROM settlement WHERE id=?", (sid,)) or 0.6
    store.update("settlement", sid, {
        "morale": round(clamp(m + 0.02, 0.02, 0.98), 4),
        "updated_tick": tick})


# ---------- M052 水权分配与水利争端 ----------
def settle_water_rights(store, cx, guard, ids, tick, seed, day):
    """
    水资源由 region.water_health 表征。干旱（water_health 低）时易生水权争端，
    记怨并进一步损害水利健康；丰水时偶发共建水利、小幅改善。
    """
    ensure_civics_schema(store)
    sid, rid = ids["settlement_id"], ids["region_id"]
    r = store.one("SELECT water_health FROM region WHERE id=?", (rid,))
    if not r:
        return
    water = r["water_health"]
    if not chance(seed, WATER_DISPUTE_BASE * (1.0 + (0.8 - water) * 2.0), "water", day):
        return
    residents = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿') ORDER BY id", (sid,))
    if len(residents) < 2:
        return
    a = residents[int(unit(seed, "wat_a", day) * len(residents))]
    b = residents[int(unit(seed, "wat_b", day) * len(residents))]
    if a["id"] == b["id"]:
        return
    cx.emit(tick, "water_dispute",
            f"{a['name']}与{b['name']}为引水灌溉争得面红耳赤",
            effects={"water_health": round(water, 3)},
            place_kind="region", place_id=rid,
            actor_kind="character", actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])
    # 争端损害水利健康
    new_water = clamp(water - 0.03, 0.05, 1.0)
    store.con.execute("UPDATE region SET water_health=?, updated_tick=? WHERE id=?",
                      (new_water, tick, rid))
    # 记怨
    if not store.one("SELECT 1 FROM relation WHERE from_kind='character' AND from_id=? "
                     "AND to_kind='character' AND to_id=? AND kind='怨'",
                     (a["id"], b["id"])):
        store.insert("relation", {
            "from_kind": "character", "from_id": a["id"],
            "to_kind": "character", "to_id": b["id"],
            "kind": "怨", "intensity": 0.4, "publicity": 0.7,
            "note": "水权争执", "updated_tick": tick})


# ---------- M048 山林荒地私垦拓殖 ----------
def settle_reclamation(store, cx, guard, ids, tick, seed, day):
    """
    每年低概率有农户私垦林地/荒地，扩大可耕地（提升 region.fertility 附近的可耕面积），
    但会小幅降低林木覆盖率。
    """
    ensure_civics_schema(store)
    sid, rid = ids["settlement_id"], ids["region_id"]
    if not chance(seed, RECLAIM_BASE, "reclaim", day):
        return
    r = store.one("SELECT forest, fertility FROM region WHERE id=?", (rid,))
    if not r:
        return
    acres = round(1.0 + unit(seed, "recl_a", day) * 4.0, 1)
    new_forest = clamp(r["forest"] - acres * 0.01, 0.05, 0.95)
    # 新垦地提升土壤可耕（以 fertility 微升表示）
    new_fert = clamp(r["fertility"] + acres * 0.005, 0.3, 1.0)
    store.con.execute(
        "UPDATE region SET forest=?, fertility=?, updated_tick=? WHERE id=?",
        (new_forest, new_fert, tick, rid))
    cx.emit(tick, "reclamation",
            f"有农户私垦林地 {acres} 亩，扩大了村里的口粮田",
            effects={"acres": acres, "forest": [round(r["forest"], 3), round(new_forest, 3)],
                     "fertility": [round(r["fertility"], 3), round(new_fert, 3)]},
            place_kind="region", place_id=rid, actor_kind="settlement")
