# -*- coding: utf-8 -*-
"""推演主循环。按日推进：生态 → 社会 → 导演，三层依次结算。"""
import json

from .base import Calendar
from . import ecology, society, director, autonomy, economy, civics
from . import lifecycle, power, infra, canon_io, genre, scheduler


class SimState:
    """跨 tick 的推演状态。不落库 —— 它是「这一旬攒了多少」这类临时账本。"""

    def __init__(self):
        self.tick = 0
        self.days = 0                  # 本旬已累计天数
        self.grain_prod_sum = 0.0
        self.meat_prod_sum = 0.0
        self.wood_sum = 0.0
        self.need_sum = 0.0
        self.shortage_today = 0.0
        self.shortage_days = 0
        self.prev_game = 1.0
        self.prev_forest = 1.0
        self.last_forest = 1.0
        self.last_game = 1.0
        # 因果锚点：让后续事件能挂在正确的原因上
        self.last_harvest = None
        self.cause_game = None
        self.cause_forest = None
        self.cause_shortage = None
        self.cause_disaster = None
        self.cause_migration = None
        self.social_events = 0
        self.timeline = []


LOAN_DDL = """
CREATE TABLE IF NOT EXISTS grain_loan (
    id INTEGER PRIMARY KEY,
    principal REAL NOT NULL CHECK (principal > 0),
    balance REAL NOT NULL CHECK (balance >= 0),
    issued_tick INTEGER NOT NULL,
    due_tick INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','overdue','repaid')),
    last_settled_tick INTEGER NOT NULL,
    issue_event_id INTEGER NOT NULL REFERENCES event(id),
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    lender_faction_id INTEGER NOT NULL REFERENCES faction(id)
);
CREATE INDEX IF NOT EXISTS idx_grain_loan_due ON grain_loan(status,due_tick);
"""


def ensure_loan_schema(store):
    store.con.executescript(LOAN_DDL)


def issue_grain_loan(store, cx, ids, tick, amount, term_days=30):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 1 <= amount <= 1000:
        raise ValueError("借粮数量须为 1~1000")
    if type(term_days) is not int or not 10 <= term_days <= 120 or term_days % 10:
        raise ValueError("借期须为 10~120 日且为 10 的倍数")
    ensure_loan_schema(store)
    sid, fid = ids["settlement_id"], ids["villager_fid"]
    source = store.one("SELECT id,amount FROM stock WHERE owner_kind='faction' AND owner_id=? "
                       "AND resource_type='grain'", (fid,))
    target = store.one("SELECT id,amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                       "AND resource_type='grain'", (sid,))
    if not source or not target or source["amount"] < amount:
        raise ValueError("势力公仓余粮不足，不能发放该笔借粮")
    due_tick = tick + term_days * 24
    
    # M035：借贷契约订立
    lender = store.one("SELECT name FROM faction WHERE id=?", (fid,))
    borrower = store.one("SELECT name FROM settlement WHERE id=?", (sid,))
    if lender and borrower:
        economy.create_contract(store, cx, ids, tick, "loan",
                                parties=[{"kind": "faction", "id": fid, "name": lender["name"]},
                                         {"kind": "settlement", "id": sid, "name": borrower["name"]}],
                                terms={"amount": amount, "term_days": term_days, "due_tick": due_tick})
    
    event_id = cx.emit(tick, "loan_issued", f"聚落向村民势力借粮 {amount:.1f}，约定第 {due_tick // 24} 日到期",
                       effects={"principal": round(float(amount), 3), "balance": round(float(amount), 3),
                                "due_tick": due_tick, "lender_grain": [round(source["amount"], 3),
                                                                        round(source["amount"] - amount, 3)],
                                "settlement_grain": [round(target["amount"], 3),
                                                     round(target["amount"] + amount, 3)]},
                       place_kind="settlement", place_id=sid, actor_kind="author")
    if event_id is None:
        raise ValueError("事件被规则护栏拒绝；借粮未发放")
    try:
        store.con.execute("UPDATE stock SET amount=amount-?,updated_tick=? WHERE id=?", (amount,tick,source["id"]))
        store.con.execute("UPDATE stock SET amount=amount+?,updated_tick=? WHERE id=?", (amount,tick,target["id"]))
        loan_id = store.con.execute(
            "INSERT INTO grain_loan(principal,balance,issued_tick,due_tick,status,last_settled_tick,"
            "issue_event_id,settlement_id,lender_faction_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (amount,amount,tick,due_tick,"active",tick,event_id,sid,fid)).lastrowid
        store.commit()
    except Exception:
        store.con.rollback()
        cx._last_hash = None
        cx._count = None
        cx.emitted -= 1
        cx.guard._per_tick[tick] = max(0, cx.guard._per_tick.get(tick, 1) - 1)
        raise
    return {"loan_id": loan_id, "event_id": event_id, "balance": round(float(amount), 3),
            "due_tick": due_tick}


def settle_loans(store, cx, ids, tick):
    ensure_loan_schema(store)
    loans = store.all("SELECT * FROM grain_loan WHERE status IN ('active','overdue') "
                      "AND last_settled_tick<? ORDER BY id", (tick,))
    for loan in loans:
        balance = loan["balance"]
        interest = round(balance * 0.01, 3)
        due = balance + interest
        stock = store.one("SELECT id,amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                          "AND resource_type='grain'", (loan["settlement_id"],))
        lender = store.one("SELECT id,amount FROM stock WHERE owner_kind='faction' AND owner_id=? "
                           "AND resource_type='grain'", (loan["lender_faction_id"],))
        available = stock["amount"] if stock else 0.0
        population = store.scalar("SELECT population FROM settlement WHERE id=?", (loan["settlement_id"],))
        ration_reserve = max(0, population or 0) * ecology.FOOD_PER_HEAD * 10
        overdue = tick >= loan["due_tick"]
        payment = min(due, max(0.0, available - ration_reserve)) if overdue and lender else 0.0
        new_balance = round(due - payment, 3)
        new_status = "repaid" if new_balance <= 0.0005 else ("overdue" if overdue else "active")
        kind = "loan_repaid" if new_status == "repaid" else ("loan_overdue" if overdue else "loan_interest")
        summary = (f"聚落偿清借粮本息 {payment:.1f}" if new_status == "repaid" else
                   f"借粮逾期：余额 {new_balance:.1f}，本旬计息 {interest:.1f}" if overdue else
                   f"借粮计息 {interest:.1f}，当前余额 {new_balance:.1f}")
        event_id = cx.emit(tick, kind, summary, cause=loan["issue_event_id"],
                           effects={"loan_id": loan["id"], "previous_balance": round(balance, 3),
                                    "interest": interest, "payment": round(payment, 3),
                                    "balance": new_balance, "status": new_status,
                                    "grain": [round(available, 3), round(available - payment, 3)],
                                    "lender_grain": [round(lender["amount"], 3),
                                                     round(lender["amount"] + payment, 3)] if lender else None},
                           place_kind="settlement", place_id=loan["settlement_id"], actor_kind="faction",
                           actor_id=loan["lender_faction_id"])
        if event_id is None:
            continue
        if payment and stock:
            store.con.execute("UPDATE stock SET amount=amount-?,updated_tick=? WHERE id=?",
                              (payment,tick,stock["id"]))
            store.con.execute("UPDATE stock SET amount=amount+?,updated_tick=? WHERE id=?",
                              (payment,tick,lender["id"]))
        store.con.execute("UPDATE grain_loan SET balance=?,status=?,last_settled_tick=? WHERE id=?",
                          (new_balance,new_status,tick,loan["id"]))


def settle_market(store, cx, ids, tick, st):
    """旬末按真实库存形成价格；征粮只在事件成功追加后发生。"""
    sid = ids["settlement_id"]
    grain = store.one("SELECT id,amount FROM stock WHERE owner_kind='settlement' "
                      "AND owner_id=? AND resource_type='grain'", (sid,))
    reserve = store.one("SELECT id,amount FROM stock WHERE owner_kind='faction' "
                        "AND owner_id=? AND resource_type='grain'", (ids["villager_fid"],))
    if not grain or not reserve:
        return
    population = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
    cover_days = grain["amount"] / max(1, population * ecology.FOOD_PER_HEAD)
    price = round(max(0.5, min(6.0, 1.0 + (20.0 - cover_days) / 20.0)), 2)
    old_price_row = store.one("SELECT effects FROM event WHERE kind='market_price' "
                              "ORDER BY id DESC LIMIT 1")
    previous = (json.loads(old_price_row["effects"])["price"] if old_price_row else 1.0)
    price_id = cx.emit(tick, "market_price", f"旬末市集粮价每斗 {price:.2f}，存粮可供 {cover_days:.1f} 日",
                       cause=st.last_harvest, effects={"price": price, "previous": previous,
                                                      "cover_days": round(cover_days, 2)},
                       place_kind="settlement", place_id=sid, actor_kind="settlement")
    if price_id is None or grain["amount"] <= population * ecology.FOOD_PER_HEAD * 10:
        return
    levy = min(grain["amount"] * 0.02, max(0.0, grain["amount"] - population * ecology.FOOD_PER_HEAD * 10))
    if levy < 0.1:
        return
    tax_id = cx.emit(tick, "tax_levy", f"村里按结余征粮 {levy:.1f}，转入公仓",
                     cause=price_id, effects={"grain_levied": round(levy, 3),
                                              "village_grain": [round(grain["amount"], 3),
                                                                round(grain["amount"] - levy, 3)]},
                     place_kind="settlement", place_id=sid, actor_kind="faction",
                     actor_id=ids["villager_fid"])
    if tax_id is not None:
        store.con.execute("UPDATE stock SET amount=amount-?,updated_tick=? WHERE id=?",
                          (levy, tick, grain["id"]))
        store.con.execute("UPDATE stock SET amount=amount+?,updated_tick=? WHERE id=?",
                          (levy, tick, reserve["id"]))


def run(store, cx, guard, ids, days, seed, log=None, verbose=True):
    log = log or (lambda m: None)
    st = SimState()

    w = store.one("SELECT * FROM world WHERE id = 1")
    r = store.one("SELECT * FROM region WHERE id = ?", (ids["region_id"],))
    st.prev_game = r["game_density"]
    st.prev_forest = r["forest"]
    start_day = w["tick"] // 24
    st.tick = w["tick"]
    last = store.one("SELECT id FROM event WHERE kind='harvest' ORDER BY id DESC LIMIT 1")
    if last:
        st.last_harvest = last["id"]
    for kind, attr in (("game_scarce", "cause_game"), ("game_collapse", "cause_game"),
                       ("deforest", "cause_forest"), ("shortage", "cause_shortage")):
        last = store.one("SELECT id FROM event WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,))
        if last:
            setattr(st, attr, last["id"])
    st.timeline.append({"day": start_day, "pop": store.scalar(
        "SELECT population FROM settlement WHERE id=?", (ids["settlement_id"],)),
        "morale": store.scalar("SELECT morale FROM settlement WHERE id=?", (ids["settlement_id"],)),
        "grain": store.scalar(
            "SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
            "AND resource_type='grain'", (ids["settlement_id"],)),
        "forest": r["forest"], "game": r["game_density"],
        "order": store.scalar("SELECT order_value FROM settlement WHERE id=?", (ids["settlement_id"],))})

    is_new_world = (start_day == 0 and cx.count() == 0)

    # M094：题材装载表自愈。旧存档或未调度过的世界，按世界 theme 补一次题材解析，
    # 否则 module_instance 为空会让 P2 题材机制（genre 门控）全部失效。
    if not store.scalar("SELECT 1 FROM module_instance LIMIT 1"):
        scheduler.schedule(store, w["theme"])

    if is_new_world:
        st.last_harvest = cx.emit(
            0, "genesis",
            f"世界「{w['name']}」始于第 1 年春季：一座小村落在河谷里立了起来",
            cause=None, place_kind="settlement", place_id=ids["settlement_id"],
            actor_kind="engine")
    
    # 初始化货币和地权（M102 + M047）：只在新世界或未初始化时运行
    if is_new_world or not store.scalar("SELECT 1 FROM stock WHERE resource_type='coin' LIMIT 1"):
        economy.initialize_currency(store, cx, ids, 0)
    if is_new_world or not store.scalar("SELECT 1 FROM canon_fact WHERE content LIKE '%田产%' LIMIT 1"):
        economy.initialize_landowner(store, cx, ids, 0, seed)
    # M100：创世时一次性播种世界背景设定（幂等，不依赖 is_new_world）
    canon_io.seed_default_canon(store, ids, 0)

    for d in range(start_day + 1, start_day + days + 1):
        tick = d * 24
        st.tick = tick
        store.con.execute("UPDATE world SET tick = ? WHERE id = 1", (tick,))

        eco = ecology.settle_day(store, cx, guard, tick, seed, ids, st, log)
        soc = society.settle_society(store, cx, guard, tick, seed, ids, st, eco, log)
        director.settle_director(store, cx, guard, tick, seed, ids, st, eco, soc, log)
        autonomy.settle_autonomy(store, cx, guard, tick, seed, ids)
        # 题材门控（M094 三档调度）：global 模块恒 on；routed 模块由世界题材决定。
        # simple/off 都视为「不跑完整循环」——于是切换题材后，跑哪些子系统本身就不一样。
        def _on(mid):
            return genre.module_on(store, mid)

        # 季节性 / 年度级治理事件优先发射（避免在年末与常规市场事件争抢 G4 每 tick 预算）
        if d % 120 == 0:
            population = store.scalar("SELECT population FROM settlement WHERE id=?", (ids["settlement_id"],))
            manpower = store.scalar("SELECT manpower FROM faction WHERE id=?", (ids["villager_fid"],))
            grain = store.scalar("SELECT amount FROM stock WHERE owner_kind='faction' AND owner_id=? "
                                 "AND resource_type='grain'", (ids["villager_fid"],))
            latest = store.one("SELECT id FROM event WHERE kind='tax_levy' ORDER BY id DESC LIMIT 1")
            cx.emit(tick, "annual_balance",
                    f"年末结算：在村 {population} 人，村民势力劳力 {manpower}，公仓存粮 {grain:.1f}",
                    cause=latest["id"] if latest else st.last_harvest,
                    effects={"population": population, "manpower": manpower,
                             "faction_grain": round(grain, 2)},
                    place_kind="settlement", place_id=ids["settlement_id"], actor_kind="settlement")
            if _on("M046"):   # 户籍人口登记（每年重登）
                civics.settle_household_registry(store, cx, ids, tick)
            if _on("M048"):   # 山林荒地私垦拓殖（每年）
                civics.settle_reclamation(store, cx, guard, ids, tick, seed, d)
            if _on("M045"):   # 聚落营造：人满则盖房，住房上限可增长（每年）
                infra.settle_housing(store, cx, ids, tick, seed, d)
            # ---- 一期社会骨架：年度级模块 ----
            if _on("M011"):
                power.settle_oral_history(store, cx, ids, tick, seed, d)
            if _on("M014"):
                power.settle_territory_contest(store, cx, ids, tick, seed, d)
            if _on("M026"):
                power.settle_memory_seal(store, cx, ids, tick, seed, d)
            if _on("M044"):
                power.settle_org_evolution(store, cx, ids, tick, seed, d)
            if _on("M066"):
                infra.settle_measure(store, cx, ids, tick, seed, d)
            if _on("M101"):
                canon_io.check_consistency(store, cx, ids, tick)
            # ---- 二期题材扩展：年度级题材机制（按世界题材门控，M004-M093）----
            genre.settle_annual(store, cx, ids, tick, seed, d)
        if d % 30 == 0:
            if _on("M017"):   # 律法与审判裁决（每季）
                civics.settle_law_trials(store, cx, ids, tick, seed, d)
            if _on("M056"):   # 徭役劳役征召（每季）
                civics.settle_corvee(store, cx, ids, tick, seed, d)
            if _on("M103"):   # 供需与产出消耗总账（每季核算）
                economy.reconcile_ledger(store, cx, ids, tick, Calendar.season(tick) + "季")
            if _on("M050"):   # 粮食仓储储备（每季核算）
                economy.settle_grain_reserve(store, cx, ids, tick)
            # ---- 一期社会骨架：季度级模块 ----
            if _on("M104"):
                lifecycle.settle_lifecycle(store, cx, ids, tick, seed, d)
            if _on("M015"):
                power.settle_bounty(store, cx, ids, tick, seed, d)
            if _on("M016"):
                power.settle_underworld(store, cx, ids, tick, seed, d)
            if _on("M018"):
                power.settle_prison(store, cx, ids, tick, seed, d)
            if _on("M032"):
                power.settle_auction(store, cx, ids, tick, seed, d)
            if _on("M039"):
                power.settle_repute_campaign(store, cx, ids, tick, seed, d)
            if _on("M040"):
                power.settle_clique(store, cx, ids, tick, seed, d)
            if _on("M043"):
                power.settle_masterwork(store, cx, ids, tick, seed, d)
            if _on("M053"):
                infra.settle_apprentice(store, cx, ids, tick, seed, d)
            if _on("M054"):
                infra.settle_guild(store, cx, ids, tick, seed, d)
            if _on("M060"):
                infra.settle_refugee(store, cx, ids, tick, seed, d, guard)
            if _on("M063"):
                infra.settle_waterway(store, cx, ids, tick, seed, d)
            if _on("M065"):
                infra.settle_messenger(store, cx, ids, tick, seed, d)
            if _on("M068"):
                infra.settle_provision(store, cx, ids, tick, seed, d)
            # ---- 二期题材扩展：季度级题材机制（按世界题材门控，M004-M093）----
            genre.settle_quarter(store, cx, ids, tick, seed, d)
        # ---- 旬末常规结算（市场 / 借贷 / 折耗 / 纠纷） ----
        if d % 10 == 0:
            settle_loans(store, cx, ids, tick)
            if _on("M049"):
                settle_market(store, cx, ids, tick, st)
                economy.update_market_prices(store, cx, ids, tick, eco)      # M049 市集多物资定价
            if _on("M047"):   # 土地纠纷
                economy.settle_land_dispute(store, cx, guard, ids, tick, seed, d)
            if _on("M051"):   # 物资仓储损耗与溢出
                economy.settle_storage_decay(store, cx, ids, tick)
            if _on("M052"):   # 水权分配与水利争端
                civics.settle_water_rights(store, cx, guard, ids, tick, seed, d)
            if _on("M059"):   # 邻里社交与乡约自治
                civics.settle_community(store, cx, ids, tick, seed, d)
            if _on("M062"):   # 墓葬与丧葬墓穴
                civics.settle_burial(store, cx, ids, tick, seed, d)
            # ---- 一期社会骨架：旬级（传播快）模块 ----
            if _on("M038"):   # 流言传播
                power.settle_rumor(store, cx, ids, tick, seed, d)
            if _on("M061"):   # 公共防疫
                infra.settle_epidemic(store, cx, ids, tick, seed, d, guard)
        guard.prune(tick)

        if d % 5 == 0:
            st.timeline.append({
                "day": d, "pop": soc["population"],
                "morale": round(soc["morale"], 3), "order": round(soc["order"], 3),
                "grain": round(eco["grain"], 1), "meat": round(eco["meat"], 1),
                "forest": round(eco["forest"], 3), "game": round(eco["game"], 3),
            })

        if verbose and d % 10 == 0:
            log(f"  {Calendar.stamp(tick)}　人口 {soc['population']:>3}　"
                f"民心 {soc['morale']:.2f}　存粮 {eco['grain']:>7.0f}　"
                f"林木 {eco['forest']:.2f}　猎物 {eco['game']:.2f}")

        if d % 30 == 0:
            store.commit()

    store.commit()
    return st


def verdict(store, st, ids):
    """P0 验收判定：不崩坏、不死锁。"""
    sid = ids["settlement_id"]
    pop = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
    alive = store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'", (sid,))
    n_events = store.scalar("SELECT COUNT(*) FROM event")
    factions = store.scalar("SELECT COUNT(*) FROM faction WHERE status='active'")
    last_tick = store.scalar("SELECT MAX(tick) FROM event")
    checks = [
        ("聚落未灭绝（G1 人口下限生效）", pop >= 5),
        ("仍有存活角色", alive >= 1),
        ("事件流未中断（非死锁）", n_events >= max(6, st.tick // (24 * 12))),
        ("活跃势力不少于 2 个（G5）", factions >= 2),
        ("最后事件发生在推演末端附近（世界仍在运转）",
         last_tick is not None and last_tick >= st.tick - 24 * 5),
    ]
    return checks
