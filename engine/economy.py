# -*- coding: utf-8 -*-
"""
经济层：货币、地权、市集、契约
（M102 货币价格 / M047 土地产权 / M049 市集 / M035 契约）

核心设计：
1. 货币不是凭空的数字，而是从劳动产出与资源交换中涌现
2. 地权记录谁拥有多少亩，纠纷记录谁在争什么
3. 市集价格由真实供需驱动，不是策划拍脑袋
"""
import json
from .base import clamp, chance, unit, pick, Calendar, DAYS_PER_SEASON

# ---- 运行时表：M103 供需总账 ----
ECONOMY_DDL = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    tick INTEGER NOT NULL,
    period TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    produced REAL NOT NULL DEFAULT 0,
    consumed REAL NOT NULL DEFAULT 0,
    net REAL NOT NULL DEFAULT 0,
    ending_stock REAL NOT NULL DEFAULT 0,
    UNIQUE(settlement_id, tick, resource_type)
);
CREATE INDEX IF NOT EXISTS idx_ledger_settlement ON ledger(settlement_id, tick);
"""

# ---- M051 仓储损耗 ----
DECAY_RATE = {"grain": 0.0008, "meat": 0.010, "wood": 0.0002, "medicine": 0.002}
SPILL_BASE = 0.0      # 超出容量部分每旬溢出比例


def ensure_economy_schema(store):
    store.con.executescript(ECONOMY_DDL)

# ---- M102 货币与价格体系 ----
COIN_PER_GRAIN = 1.0    # 基准：1 单位粮食 = 1 文钱
WAGE_BASE = 0.8          # 基准日工：0.8 文/日
PRICE_VOLATILITY = 0.15  # 价格波动幅度

# ---- M047 土地产权 ----
LAND_PER_HOUSEHOLD = 3.5  # 户均地亩
LAND_DISPUTE_BASE = 0.08  # 地权纠纷基础概率


def initialize_currency(store, cx, ids, tick):
    """
    M102：货币初始化。
    给势力公仓和聚落分配初始货币存量，基于粮食储备估值。
    """
    sid, fid = ids["settlement_id"], ids["villager_fid"]
    
    faction_grain = store.scalar(
        "SELECT amount FROM stock WHERE owner_kind='faction' AND owner_id=? "
        "AND resource_type='grain'", (fid,))
    settlement_grain = store.scalar(
        "SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
        "AND resource_type='grain'", (sid,))
    
    if faction_grain is None or settlement_grain is None:
        return
    
    # 货币存量 = 粮食储备 × 基准比价 × 0.3（流通率）
    faction_coin = round(faction_grain * COIN_PER_GRAIN * 0.3, 2)
    settlement_coin = round(settlement_grain * COIN_PER_GRAIN * 0.2, 2)
    
    # 检查是否已有货币记录
    existing_faction = store.one(
        "SELECT id FROM stock WHERE owner_kind='faction' AND owner_id=? "
        "AND resource_type='coin'", (fid,))
    existing_settlement = store.one(
        "SELECT id FROM stock WHERE owner_kind='settlement' AND owner_id=? "
        "AND resource_type='coin'", (sid,))
    
    if not existing_faction:
        store.insert("stock", {
            "owner_kind": "faction", "owner_id": fid,
            "resource_type": "coin", "amount": faction_coin,
            "updated_tick": tick})
    
    if not existing_settlement:
        store.insert("stock", {
            "owner_kind": "settlement", "owner_id": sid,
            "resource_type": "coin", "amount": settlement_coin,
            "updated_tick": tick})
    
    if not existing_faction or not existing_settlement:
        cx.emit(tick, "currency_init",
                f"村中流通货币：势力 {faction_coin:.1f} 文，聚落 {settlement_coin:.1f} 文",
                effects={"faction_coin": faction_coin, "settlement_coin": settlement_coin},
                place_kind="settlement", place_id=sid, actor_kind="settlement")


def initialize_landowner(store, cx, ids, tick, seed):
    """
    M047：土地产权初始化。
    为每个成年角色分配田地，记录在 canon_fact 表作为初始产权。
    """
    sid = ids["settlement_id"]
    population = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
    if not population:
        return
    
    households = max(1, population // 3)
    total_land = round(households * LAND_PER_HOUSEHOLD, 1)
    
    adults = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿','老弱') ORDER BY id", (sid,))
    
    if not adults:
        return
    
    # 按确定性随机分配地亩
    allocated = []
    for i, person in enumerate(adults):
        base = LAND_PER_HOUSEHOLD
        variance = unit(seed, "landvar", person["id"]) * 2.0 - 1.0  # [-1, 1]
        acreage = round(clamp(base + variance, 0.5, 8.0), 1)
        allocated.append((person["id"], person["name"], acreage))
        
        store.insert("canon_fact", {
            "content": f"{person['name']}持有田产 {acreage} 亩",
            "source": "engine",
            "mutable": 1,
            "created_tick": tick,
            "note": "开局地权分配"})
    
    cx.emit(tick, "land_allocation",
            f"村中 {len(adults)} 户田产登记：总计 {sum(a[2] for a in allocated):.1f} 亩",
            effects={"total_land": total_land,
                     "households": len(adults),
                     "allocations": [(a[1], a[2]) for a in allocated[:5]]},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


def settle_land_dispute(store, cx, guard, ids, tick, seed, day):
    """
    M047：土地纠纷。
    低概率发生地权争执事件，记录在 relation 表。
    """
    sid = ids["settlement_id"]
    
    if not chance(seed, LAND_DISPUTE_BASE, "dispute", day):
        return
    
    adults = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿') ORDER BY id", (sid,))
    
    if len(adults) < 2:
        return
    
    i = int(unit(seed, "dis_a", day) * len(adults))
    j = int(unit(seed, "dis_b", day) * len(adults))
    if i == j:
        return
    
    a, b = adults[i], adults[j]
    
    # 查询双方地产（从 canon_fact 内容中提取）
    land_a = store.one(
        "SELECT content FROM canon_fact WHERE content LIKE ? AND source='engine' "
        "ORDER BY created_tick DESC LIMIT 1", (f"%{a['name']}%田产%",))
    land_b = store.one(
        "SELECT content FROM canon_fact WHERE content LIKE ? AND source='engine' "
        "ORDER BY created_tick DESC LIMIT 1", (f"%{b['name']}%田产%",))
    
    if not land_a or not land_b:
        return
    
    # 记录怨仇关系
    existing = store.one(
        "SELECT id FROM relation WHERE from_kind='character' AND from_id=? "
        "AND to_kind='character' AND to_id=?", (a["id"], b["id"]))
    
    if not existing:
        store.insert("relation", {
            "from_kind": "character", "from_id": a["id"],
            "to_kind": "character", "to_id": b["id"],
            "kind": "怨", "intensity": 0.6, "publicity": 0.8,
            "note": f"地界纠纷：{land_a['content']} 与 {land_b['content']}",
            "updated_tick": tick})
    
    cx.emit(tick, "land_dispute",
            f"{a['name']}与{b['name']}因地界起了争执，街坊都知道了",
            effects={"disputants": [a["name"], b["name"]]},
            place_kind="settlement", place_id=sid,
            actor_kind="character", actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])


def update_market_prices(store, cx, ids, tick, eco):
    """
    M049：市集价格更新。
    扩展现有粮价逻辑，增加肉类、木材、药材的市场价格。
    """
    sid = ids["settlement_id"]
    population = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
    if not population:
        return
    
    prices = {}
    for resource in ["grain", "meat", "wood", "medicine"]:
        stock_row = store.one(
            "SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
            "AND resource_type=?", (sid, resource))
        
        if not stock_row:
            continue
        
        amount = stock_row["amount"]
        
        # 价格公式：基准价 × (1 + 稀缺系数)
        # 稀缺系数 = (需求 - 供给) / 需求
        if resource == "grain":
            daily_need = population * 0.5
            base_price = 1.0
        elif resource == "meat":
            daily_need = population * 0.15
            base_price = 2.5
        elif resource == "wood":
            daily_need = population * 0.3
            base_price = 0.6
        else:  # medicine
            daily_need = population * 0.05
            base_price = 5.0
        
        cover_days = amount / max(daily_need, 0.1)
        scarcity = clamp((20.0 - cover_days) / 20.0, -0.5, 2.0)
        price = round(base_price * (1.0 + scarcity), 2)
        prices[resource] = price
    
    if prices:
        cx.emit(tick, "market_prices",
                f"旬末市集：粮 {prices.get('grain',1.0):.2f}文/斗、"
                f"肉 {prices.get('meat',2.5):.2f}文/斤、"
                f"柴 {prices.get('wood',0.6):.2f}文/担",
                effects={"prices": prices},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    
    return prices


# ---- M035 契约订立与公证 ----
CONTRACT_TYPES = {
    "loan": "借贷契约",
    "land_lease": "田地租赁",
    "labor": "雇工契约",
    "trade": "货物买卖"
}


def create_contract(store, cx, ids, tick, contract_type, parties, terms):
    """
    M035：契约订立。
    在 canon_fact 表记录契约内容，作为可查证的约定。
    
    Args:
        contract_type: 契约类型（loan/land_lease/labor/trade）
        parties: 当事人列表 [{"kind": "character", "id": 1, "name": "张三"}, ...]
        terms: 契约条款字典 {"amount": 10, "term_days": 30, ...}
    """
    sid = ids["settlement_id"]
    
    if contract_type not in CONTRACT_TYPES:
        return None
    
    # 生成契约内容
    type_label = CONTRACT_TYPES[contract_type]
    party_names = [p["name"] for p in parties]
    
    if contract_type == "loan":
        content = f"{party_names[0]}向{party_names[1]}借粮{terms.get('amount',0)}单位，{terms.get('term_days',0)}日到期"
    elif contract_type == "land_lease":
        content = f"{party_names[0]}将{terms.get('acreage',0)}亩地租予{party_names[1]}，租期{terms.get('term_days',0)}日"
    elif contract_type == "labor":
        content = f"{party_names[0]}雇佣{party_names[1]}务工{terms.get('days',0)}日，日工{terms.get('wage',0)}文"
    else:  # trade
        content = f"{party_names[0]}与{party_names[1]}约定：以{terms.get('price',0)}文交易{terms.get('goods','物资')}"
    
    # 记入 canon_fact
    contract_id = store.insert("canon_fact", {
        "content": f"【{type_label}】{content}",
        "source": "engine",
        "mutable": 0,  # 契约一旦订立不可改
        "created_tick": tick,
        "note": f"契约类型: {contract_type}"})
    
    # 发出契约事件
    event_id = cx.emit(tick, "contract_signed",
                       f"{type_label}订立：{content}",
                       effects={"contract_id": contract_id,
                                "type": contract_type,
                                "terms": terms},
                       place_kind="settlement", place_id=sid,
                       actor_kind=parties[0]["kind"], actor_id=parties[0]["id"],
                       participants=parties)
    
    return {"contract_id": contract_id, "event_id": event_id}


# ---- M051 物资仓储损耗与溢出 ----
def settle_storage_decay(store, cx, ids, tick):
    """
    M051：物资仓储损耗。
    每旬按各类物资的 decay_rate 复利折损；超出仓储容量的部分溢出损耗。
    不可变事件记录损耗量，便于溯源。
    """
    ensure_economy_schema(store)
    sid = ids["settlement_id"]
    rows = store.all(
        "SELECT id, resource_type, amount, decay_rate, capacity FROM stock "
        "WHERE owner_kind='settlement' AND owner_id=?", (sid,))
    if not rows:
        return

    total_loss = 0.0
    for row in rows:
        rt = row["resource_type"]
        rate = row["decay_rate"] if row["decay_rate"] else DECAY_RATE.get(rt, 0.0)
        # 旬末（10 日 = 240 小时）复利折损
        decayed = row["amount"] * (1.0 - (1.0 - rate) ** 240)
        decayed = min(decayed, row["amount"])
        # 溢出损耗：超出仓储容量的部分
        overflow = max(0.0, row["amount"] - row["capacity"])
        loss = decayed + overflow * SPILL_BASE
        if loss > 0.0001:
            new_amount = max(0.0, row["amount"] - loss)
            store.con.execute(
                "UPDATE stock SET amount=?, updated_tick=? WHERE id=?",
                (new_amount, tick, row["id"]))
            total_loss += loss

    if total_loss > 0.5:
        cx.emit(tick, "storage_decay",
                f"旬末仓储折耗与溢出共损 {total_loss:.1f} 单位物资",
                effects={"loss": round(total_loss, 2)},
                place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---- M050 粮食仓储储备 ----
def settle_grain_reserve(store, cx, ids, tick):
    """
    M050：粮食仓储储备。
    维护「战备粮」概念：核算当前存粮相对目标垫底储备（人口×口粮×30日）的储备率，
    并将仓储容量同步至目标储备上限，供 M051 溢出判断。每季由 sim 在 d%30 调用一次，
    不依赖内存态，保证整跑与分段续跑一致。
    """
    ensure_economy_schema(store)
    sid = ids["settlement_id"]
    grain = store.one(
        "SELECT id, amount, capacity FROM stock WHERE owner_kind='settlement' "
        "AND owner_id=? AND resource_type='grain'", (sid,))
    if not grain:
        return
    population = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,)) or 0
    reserve_target = max(0, population) * 0.52 * 30
    stock = grain["amount"]
    rate = round(stock / reserve_target, 3) if reserve_target else 0.0
    new_capacity = max(grain["capacity"], reserve_target * 1.2)
    store.con.execute("UPDATE stock SET capacity=?, updated_tick=? WHERE id=?",
                      (new_capacity, tick, grain["id"]))
    cx.emit(tick, "grain_reserve",
            f"粮食储备率 {rate:.2f}（目标垫底 {reserve_target:.0f} 单位）",
            effects={"reserve_rate": rate, "target": round(reserve_target, 1),
                     "stock": round(stock, 1)},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---- M103 供需与产出消耗总账 ----
def reconcile_ledger(store, cx, ids, tick, period):
    """
    M103：供需与产出消耗总账。
    季末核算本季粮肉产出（取自 harvest 旬报累计）、口粮消耗（food_needed 累计）
    与各类物资期末库存，落 ledger 表，使「供需」成为可长期查阅的账本。
    """
    ensure_economy_schema(store)
    sid = ids["settlement_id"]
    period_start = tick - DAYS_PER_SEASON * 24

    # 本季产出/需求从旬报 harvest 事件累计
    harvests = store.all(
        "SELECT effects FROM event WHERE tick>? AND tick<=? AND kind='harvest'",
        (period_start, tick))
    grain_prod = meat_prod = food_need = 0.0
    for e in harvests:
        eff = json.loads(e["effects"]) if e["effects"] else {}
        grain_prod += eff.get("grain_produced", 0.0)
        meat_prod += eff.get("meat_produced", 0.0)
        food_need += eff.get("food_needed", 0.0)

    # 本季额外消耗（征粮、折耗、罚粮、徭役等）
    extra_loss = 0.0
    for kind in ("tax_levy", "storage_decay", "trial", "corvee"):
        rows = store.all(
            "SELECT effects FROM event WHERE tick>? AND tick<=? AND kind=?",
            (period_start, tick, kind))
        for e in rows:
            eff = json.loads(e["effects"]) if e["effects"] else {}
            for k, v in eff.items():
                if k in ("grain_levied", "loss"):
                    extra_loss += abs(v)

    consumed = food_need + extra_loss
    produced = {"grain": grain_prod, "meat": meat_prod}

    resources = ["grain", "meat", "wood", "medicine", "coin"]
    for rt in resources:
        stock_row = store.one(
            "SELECT amount FROM stock WHERE owner_kind='settlement' "
            "AND owner_id=? AND resource_type=?", (sid, rt))
        ending = stock_row["amount"] if stock_row else 0.0
        prod = produced.get(rt, 0.0)
        cons_amt = consumed if rt == "grain" else 0.0
        store.con.execute(
            "INSERT OR REPLACE INTO ledger(settlement_id, tick, period, resource_type, "
            "produced, consumed, net, ending_stock) VALUES(?,?,?,?,?,?,?,?)",
            (sid, tick, period, rt, round(prod, 2), round(cons_amt, 2),
             round(prod - cons_amt, 2), round(ending, 2)))

    grain_stock = store.scalar(
        "SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
        "AND resource_type='grain'", (sid,)) or 0.0
    cx.emit(tick, "ledger_reconcile",
            f"{period}总账：粮产 {grain_prod:.0f}／耗 {consumed:.0f}，期末存粮 {grain_stock:.0f}",
            effects={"produced": {"grain": round(grain_prod, 1),
                                 "meat": round(meat_prod, 1)},
                     "consumed": round(consumed, 1), "period": period},
            place_kind="settlement", place_id=sid, actor_kind="settlement")
