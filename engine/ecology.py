# -*- coding: utf-8 -*-
"""
生态与生产（M045 聚落 + M069 生态 + M103 供需总账的最小实现）。

这里承载整个 Demo 最想演示的那条链：

    采伐林木 → 植被覆盖率下降 → 猎物再生能力下降 → 狩猎产出降低
             → 粮食储备下降 → 民心下跌 → 人口流失

关键在「再生能力取决于上一环的存量」：林木少一分，猎物的再生就慢一分。
所以过度采伐不是立刻报应，而是在几十天后以猎获减少的形式找上门 ——
这正是「涌现」，不是脚本。
"""
from .base import clamp, Calendar

# ---- 劳动力产出基准（每人每日） ----
FARM_BASE = 2.05      # 农人产粮
HUNT_BASE = 1.15      # 猎人产肉
HERD_BASE = 0.60      # 牧人
CRAFT_BASE = 0.50     # 工匠
LIGHT_BASE = 0.18     # 轻劳动（老者、村长）

FOOD_PER_HEAD = 0.52  # 每人每日口粮

# ---- 生态参数 ----
WOOD_PER_DAY = 1.20        # 全村日采木材基数
WOOD_FOREST_COST = 0.0018  # 每单位木材采伐对林木覆盖率的消耗
FOREST_REGEN = 0.0022      # 林木日再生基数
HUNT_GAME_COST = 0.0022    # 每单位猎获对猎物密度的消耗
GAME_REGEN = 0.0120        # 猎物日再生基数（再生产率正比于林木覆盖率）

# ---- 阈值 ----
GAME_SCARCE = 0.30
GAME_CRITICAL = 0.15
FOREST_DEGRADED = 0.40
SHORTAGE_RATIO = 0.06      # 缺口占日需求比例超过此值视为短缺


def _stock_row(store, ok, oid, rt):
    return store.one(
        "SELECT id, amount, decay_rate FROM stock "
        "WHERE owner_kind=? AND owner_id=? AND resource_type=?", (ok, oid, rt))


def _set_amount(store, row, new_amount, tick):
    if row is None:
        return
    store.con.execute(
        "UPDATE stock SET amount=?, updated_tick=? WHERE id=?",
        (max(0.0, new_amount), tick, row["id"]))


def settle_day(store, cx, guard, tick, seed, ids, st, log=None):
    sid, rid = ids["settlement_id"], ids["region_id"]
    s = store.one("SELECT * FROM settlement WHERE id=?", (sid,))
    r = store.one("SELECT * FROM region WHERE id=?", (rid,))
    if s is None or r is None:
        return None

    season_f = Calendar.farm_factor(tick)
    hunt_f = Calendar.hunt_factor(tick)
    # 秩序影响劳动效率，但保留下限 —— 否则「秩序崩 → 产出崩 → 更缺粮」
    # 会形成一个出不来的正反馈死锁，世界一旦恶化就永远好不了。
    # 现实中村落秩序再差，地还是要种的，所以下限给到 0.85。
    labor = 0.85 + 0.15 * s["order_value"]

    occ = {row["occupation"]: row["n"] for row in store.all(
        "SELECT occupation, COUNT(*) AS n FROM character "
        "WHERE settlement_id=? AND status='alive' GROUP BY occupation", (sid,))}
    farmers = occ.get("农人", 0)
    hunters = occ.get("猎人", 0)
    herders = occ.get("牧人", 0)
    crafts = occ.get("工匠", 0)
    light = occ.get("村长", 0) + occ.get("老弱", 0)

    # ---- 产出 ----
    grain_prod = (farmers * FARM_BASE * r["fertility"] * season_f * labor
                  + herders * HERD_BASE * season_f * 0.5
                  + crafts * CRAFT_BASE * 0.4 * labor
                  + light * LIGHT_BASE)
    meat_prod = hunters * HUNT_BASE * r["game_density"] * hunt_f * labor
    wood_got = WOOD_PER_DAY * r["forest"] * labor

    # ---- 消耗 ----
    pop = s["population"]
    food_need = pop * FOOD_PER_HEAD

    g_row = _stock_row(store, "settlement", sid, "grain")
    m_row = _stock_row(store, "settlement", sid, "meat")
    w_row = _stock_row(store, "settlement", sid, "wood")
    grain_amt = g_row["amount"] if g_row else 0.0
    meat_amt = m_row["amount"] if m_row else 0.0
    wood_amt = w_row["amount"] if w_row else 0.0

    avail = grain_amt + grain_prod + meat_amt + meat_prod
    shortage = max(0.0, food_need - avail)
    consumed = min(food_need, avail)
    meat_used = min(meat_amt + meat_prod, consumed)      # 先吃易腐的肉
    grain_used = max(0.0, consumed - meat_used)

    # 自然损耗（霉变、虫蛀）。decay_rate 是每 tick 损耗率，按日复利折算。
    new_meat = (meat_amt + meat_prod - meat_used)
    new_grain = (grain_amt + grain_prod - grain_used)
    new_wood = wood_amt + wood_got
    if m_row:
        new_meat *= (1.0 - m_row["decay_rate"]) ** 24
    if g_row:
        new_grain *= (1.0 - g_row["decay_rate"]) ** 24
    if w_row:
        new_wood *= (1.0 - w_row["decay_rate"]) ** 24

    _set_amount(store, m_row, new_meat, tick)
    _set_amount(store, g_row, new_grain, tick)
    _set_amount(store, w_row, new_wood, tick)

    # ---- 生态反馈（G3：超采后再生能力递减） ----
    harvest_forest = wood_got * WOOD_FOREST_COST
    regen_forest = FOREST_REGEN * (1.0 - r["forest"])
    new_forest, eff_forest = guard.resource_step(
        r["forest"], regen_forest, harvest_forest, tag="林木")
    new_forest = clamp(new_forest, 0.0, 0.95)

    harvest_game = meat_prod * HUNT_GAME_COST
    regen_game = GAME_REGEN * new_forest * (1.0 - r["game_density"])   # ← 林木决定猎物再生
    new_game, eff_game = guard.resource_step(
        r["game_density"], regen_game, harvest_game, tag="猎物")
    new_game = clamp(new_game, 0.0, 0.90)

    store.con.execute(
        "UPDATE region SET forest=?, game_density=?, updated_tick=? WHERE id=?",
        (new_forest, new_game, tick, rid))

    # ---- 累积到旬报 ----
    st.grain_prod_sum += grain_prod
    st.meat_prod_sum += meat_prod
    st.wood_sum += wood_got
    st.need_sum += food_need
    st.shortage_today = shortage
    st.days += 1
    st.last_forest, st.last_game = new_forest, new_game

    # ---- 质变事件 ----
    eff_common = {
        "forest": [round(r["forest"], 3), round(new_forest, 3)],
        "game_density": [round(r["game_density"], 3), round(new_game, 3)],
    }

    if st.prev_game >= GAME_SCARCE > new_game:
        cause = st.last_harvest
        eid = cx.emit(
            tick, "game_scarce",
            f"山中猎物密度跌至 {new_game:.2f}，猎人空手而归的日子多起来了",
            cause=cause, effects=eff_common, place_kind="region", place_id=rid,
            actor_kind="nature")
        st.cause_game = eid
    if st.prev_game >= GAME_CRITICAL > new_game:
        eid = cx.emit(
            tick, "game_collapse",
            f"猎物几近绝迹（密度 {new_game:.2f}），猎户开始另谋出路",
            cause=st.cause_game or st.last_harvest, effects=eff_common,
            place_kind="region", place_id=rid, actor_kind="nature")
        st.cause_game = eid

    if st.prev_forest >= FOREST_DEGRADED > new_forest:
        eid = cx.emit(
            tick, "deforest",
            f"近山林木覆盖率跌破 {new_forest:.2f}，采薪要走越来越远的路",
            cause=st.last_harvest, effects=eff_common, place_kind="region", place_id=rid,
            actor_kind="nature")
        st.cause_forest = eid

    if shortage > food_need * SHORTAGE_RATIO:
        st.shortage_days += 1
        if st.shortage_days == 1 or st.shortage_days % 5 == 0:
            cause = st.cause_game or st.cause_forest or st.last_harvest
            eid = cx.emit(
                tick, "shortage",
                f"存粮见底，当日缺粮 {shortage:.1f}（连续第 {st.shortage_days} 日）",
                cause=cause,
                effects={"grain": [round(grain_amt, 1), round(new_grain, 1)],
                         "shortage_days": st.shortage_days},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
            st.cause_shortage = eid
    else:
        st.shortage_days = max(0, st.shortage_days - 1)

    # ---- 旬报 ----
    if Calendar.day(tick) % 10 == 0 and st.days > 0:
        st.last_harvest = cx.emit(
            tick, "harvest",
            f"旬报：得粮 {st.grain_prod_sum:.0f}、肉 {st.meat_prod_sum:.0f}、"
            f"薪柴 {st.wood_sum:.0f}，全村耗粮 {st.need_sum:.0f}"
            + (f"，缺口 {st.need_sum - st.grain_prod_sum - st.meat_prod_sum:.0f}"
               if st.need_sum > st.grain_prod_sum + st.meat_prod_sum else "，略有盈余"),
            effects={"forest": round(new_forest, 3), "game_density": round(new_game, 3),
                     "grain_stock": round(new_grain, 1),
                     "grain_produced": round(st.grain_prod_sum, 1),
                     "meat_produced": round(st.meat_prod_sum, 1),
                     "food_needed": round(st.need_sum, 1)}, 
            place_kind="settlement", place_id=sid, actor_kind="settlement")
        st.grain_prod_sum = st.meat_prod_sum = st.wood_sum = st.need_sum = 0.0
        st.days = 0

    st.prev_game, st.prev_forest = new_game, new_forest

    return {
        "grain": new_grain, "meat": new_meat, "wood": new_wood,
        "shortage": shortage, "food_need": food_need,
        "forest": new_forest, "game": new_game,
        "grain_prod": grain_prod, "meat_prod": meat_prod,
    }
