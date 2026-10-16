# -*- coding: utf-8 -*-
"""
聚落基建与物流（P1 社会骨架）：

  M053 工匠学徒契约　　M054 行业行会自治
  M060 流民安置与遣返　M061 公共防疫与隔离管控
  M063 河运漕运航道　　M065 双线通信
  M066 地域度量衡差异　M068 旅途行囊补给管理

设计纪律（与既有内核一致）：
  1. 全部随机来自 (seed, 维度, day)，day 取绝对日，不依赖内存计数或调用序。
  2. 状态变更一律经不可变事件落库；新建表用 ensure_infra_schema 按需补装。
  3. 涉及人口增减（M060）必须守住 G1 人口下限，且新增角色的 id/姓名/年龄完全确定，
     整跑与分段续跑产生逐条一致的事件流与末态。
"""
import json

from .base import clamp, chance, unit, pick, Calendar

# ---------- 概率与阈值 ----------
APPRENTICE_BASE = 0.08     # M053 学徒契约（每季）
GUILD_BASE = 0.10          # M054 行会成立（每季）
GUILD_ENFORCE = 0.20       # 已成立行会的立规（每季）
REFUGEE_BASE = 0.10        # M060 流民来/去（每季）
EPIDEMIC_BASE = 0.05       # M061 疫起（每旬）
EPIDEMIC_DURATION = 60     # 疫病持续（日）
QUARANTINE_BASE = 0.4      # 疫中是否封村（每旬）
WATERWAY_BASE = 0.10       # M063 水路货运（每季）
MESSENGER_BASE = 0.10      # M065 通信（每季）
MEASURE_BASE = 0.5         # M066 度量衡折算（每年）
PROVISION_BASE = 0.10      # M068 行囊补给（每季）
HOUSE_BASE = 0.35          # M045 聚落营造：住房满员后每年动工的概率
HOUSE_WOOD = 10.0          # 盖一间新房耗木料
HOUSE_GRAIN = 12.0         # 及耗存粮（供工匠吃喝）
HOUSES_PER_BUILD = 2       # 每次动工盖几间
HOUSE_MIN_MORALE = 0.45    # 民心低于此不兴土木

REFUGEE_SURNAMES = list("钱孙李周吴郑冯陈卫蒋沈韩杨朱秦许何吕施张孔曹")
REFUGEE_GIVEN = ["阿离", "路生", "渡客", "北望", "南来", "行远", "流年", "客卿",
                 "散木", "无名", "逆旅", "行歌", "避秦", "远舟"]
GUILD_DOMAINS = ["铁作", "木作", "织造", "猎户", "庖厨", "药铺"]


# ---------- 运行时表 ----------
INFRA_DDL = """
CREATE TABLE IF NOT EXISTS guild (
    id INTEGER PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    members INTEGER NOT NULL DEFAULT 0,
    founded_tick INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','dormant')),
    UNIQUE(settlement_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_guild_settlement ON guild(settlement_id);

CREATE TABLE IF NOT EXISTS epidemic (
    id INTEGER PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES settlement(id),
    started_tick INTEGER NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('active','ended')),
    quarantined INTEGER NOT NULL DEFAULT 0,
    ended_tick INTEGER NOT NULL DEFAULT 0,
    origin_event_id INTEGER,
    updated_tick INTEGER NOT NULL DEFAULT 0
);
-- 仅约束「同时存在多个 active 疫情」；允许多轮 ended 历史共存，否则第二轮疫情结束时
-- 把 phase 改回 'ended' 会与上一轮 ended 记录撞 UNIQUE(settlement_id, phase)。
CREATE UNIQUE INDEX IF NOT EXISTS uq_epidemic_active ON epidemic(settlement_id) WHERE phase='active';
CREATE INDEX IF NOT EXISTS idx_epidemic ON epidemic(settlement_id, phase);
"""


def ensure_infra_schema(store):
    store.con.executescript(INFRA_DDL)
    # 非破坏性升级：早期世界误带了 UNIQUE(settlement_id, phase)，会阻断多轮疫情结束。
    # 检测到该约束（PRAGMA 中 origin='u' 的自动索引）则按新结构重建表并保留数据。
    try:
        idxs = store.con.execute("PRAGMA index_list(epidemic)").fetchall()
        has_bad = any(r[2] == "u" for r in idxs)
    except Exception:
        has_bad = False
    if has_bad:
        store.con.executescript("""
            CREATE TABLE epidemic_new (
                id INTEGER PRIMARY KEY,
                settlement_id INTEGER NOT NULL REFERENCES settlement(id),
                started_tick INTEGER NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN ('active','ended')),
                quarantined INTEGER NOT NULL DEFAULT 0,
                ended_tick INTEGER NOT NULL DEFAULT 0,
                origin_event_id INTEGER,
                updated_tick INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO epidemic_new
                (id,settlement_id,started_tick,phase,quarantined,ended_tick,
                 origin_event_id,updated_tick)
                SELECT id,settlement_id,started_tick,phase,quarantined,ended_tick,
                       origin_event_id,updated_tick FROM epidemic;
            DROP TABLE epidemic;
            ALTER TABLE epidemic_new RENAME TO epidemic;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_epidemic_active
                ON epidemic(settlement_id) WHERE phase='active';
            CREATE INDEX IF NOT EXISTS idx_epidemic ON epidemic(settlement_id, phase);
        """)
        store.commit()


# ---------- 通用：抽样 ----------
def _adults(store, sid, exclude=()):
    return store.all(
        "SELECT id, name, age, occupation FROM character "
        "WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿','村长') ORDER BY id", (sid,))


def _has_relation(store, a, b, kind):
    return store.one(
        "SELECT 1 FROM relation WHERE from_kind='character' AND from_id=? "
        "AND to_kind='character' AND to_id=? AND kind=?", (a, b, kind))


# ---------- M053 工匠学徒契约 ----------
def settle_apprentice(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, APPRENTICE_BASE, "appr", day):
        return
    masters = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation='工匠' ORDER BY id", (sid,))
    youths = store.all(
        "SELECT id, name, age FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation='农人' AND age BETWEEN 14 AND 20 ORDER BY id", (sid,))
    if not masters or not youths:
        return
    m = masters[int(unit(seed, "app_m", day) * len(masters))]
    y = youths[int(unit(seed, "app_y", day) * len(youths))]
    if _has_relation(store, m["id"], y["id"], "恩"):
        return
    store.con.execute(
        "INSERT OR IGNORE INTO relation "
        "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
        "VALUES ('character', ?, 'character', ?, '恩', ?, ?, ?, ?)",
        (m["id"], y["id"], 0.7, 0.6, "师徒：传授手艺", tick))
    cx.emit(tick, "apprentice",
            f"{m['name']}收{y['name']}为徒，立下学徒契约，言传身教",
            effects={"master": m["name"], "apprentice": y["name"]},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=m["id"],
            participants=[{"kind": "character", "id": m["id"]},
                          {"kind": "character", "id": y["id"]}])


# ---------- M054 行业行会自治 ----------
def settle_guild(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    ensure_infra_schema(store)
    if not chance(seed, GUILD_BASE, "guild", day):
        return
    domain = pick(seed, GUILD_DOMAINS, "guild_d", day)
    existing = store.one(
        "SELECT id, members, status FROM guild WHERE settlement_id=? AND domain=?", (sid, domain))
    craftsmen = store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation IN ('工匠','猎人','牧人','农人')", (sid,))
    if existing:
        # 已立行会：低概率立规整肃，小幅回补秩序
        if not chance(seed, GUILD_ENFORCE, "guild_e", day):
            return
        s = store.one("SELECT order_value FROM settlement WHERE id=?", (sid,))
        if s:
            store.update("settlement", sid,
                         {"order_value": round(clamp(s["order_value"] + 0.015, 0.02, 0.98), 4),
                          "updated_tick": tick})
        cx.emit(tick, "guild",
                f"{domain}行会聚会议事，定下今年的行规与工价",
                effects={"domain": domain, "members": existing["members"]},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    else:
        if craftsmen < 2:
            return
        store.insert("guild", {
            "settlement_id": sid, "name": f"{domain}行会", "domain": domain,
            "members": craftsmen, "founded_tick": tick, "status": "active"})
        cx.emit(tick, "guild",
                f"村中{domain}手艺人有感于各自为政，公议结为{domain}行会",
                effects={"domain": domain, "members": craftsmen},
                place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M060 流民安置与遣返 ----------
# ---------- M045 聚落营造：住房可增长 ----------
def settle_housing(store, cx, ids, tick, seed, day):
    """人口顶到住房上限、且木料与存粮宽裕、民心尚可时，村里合力盖起新房。

    settlement.housing 是生育与接纳流民的承载力上限（society 用它卡 `old_pop < housing`，
    infra 用它判断 `housing - pop >= 2`）。本函数让它**可以增长**，从而给「人口增长
    最终撞上承载力」的马尔萨斯陷阱留出一个释放阀；但增长有代价：花掉木料与存粮，
    所以并非无条件扩张。返回新上限（未动工则返回 None）。
    """
    sid = ids["settlement_id"]
    s = store.one("SELECT population, housing, morale FROM settlement WHERE id=?", (sid,))
    if not s or s["morale"] < HOUSE_MIN_MORALE or s["population"] < s["housing"]:
        return None
    wood = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                        "AND owner_id=? AND resource_type='wood'", (sid,)) or 0.0
    grain = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                         "AND owner_id=? AND resource_type='grain'", (sid,)) or 0.0
    if wood < HOUSE_WOOD or grain < HOUSE_GRAIN:
        return None
    if not chance(seed, HOUSE_BASE, "M045build", day):
        return None
    for resource, cost in (("wood", HOUSE_WOOD), ("grain", HOUSE_GRAIN)):
        store.con.execute("UPDATE stock SET amount=MAX(0, amount-?), updated_tick=? "
                          "WHERE owner_kind='settlement' AND owner_id=? AND resource_type=?",
                          (cost, tick, sid, resource))
    new_housing = s["housing"] + HOUSES_PER_BUILD
    store.con.execute("UPDATE settlement SET housing=?, updated_tick=? WHERE id=?",
                      (new_housing, tick, sid))
    cx.emit(tick, "house_built",
            f"村里合力盖起 {HOUSES_PER_BUILD} 间新房，能多容几口人了",
            cause=None,
            effects={"housing": [s["housing"], new_housing],
                     "wood_cost": HOUSE_WOOD, "grain_cost": HOUSE_GRAIN},
            place_kind="settlement", place_id=sid, actor_kind="settlement")
    return new_housing


def _refugee_name(store, sid, seed, key, day):
    base = REFUGEE_SURNAMES[int(unit(seed, key + "s", day) * len(REFUGEE_SURNAMES))]
    giv = REFUGEE_GIVEN[int(unit(seed, key + "g", day) * len(REFUGEE_GIVEN))]
    name = base + giv
    # 与在村者重名则加区分字（确定性）
    if store.one("SELECT 1 FROM character WHERE settlement_id=? AND name=? AND status='alive'",
                 (sid, name)):
        name += "·客"
    return name


def settle_refugee(store, cx, ids, tick, seed, day, guard):
    sid = ids["settlement_id"]
    s = store.one("SELECT population, housing, morale FROM settlement WHERE id=?", (sid,))
    if not s:
        return
    if not chance(seed, REFUGEE_BASE, "refugee", day):
        return
    pop = store.scalar("SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'", (sid,))
    # 住房宽裕、民心尚可 → 接纳流民；拥挤或民心低迷 → 遣返（守 G1 下限）
    if s["housing"] - pop >= 2 and s["morale"] > 0.45:
        n = 1 + int(unit(seed, "ref_n", day) * 2)
        arrived = []
        for k in range(n):
            age = int(16 + unit(seed, f"ref_age_{k}", day) * 40)
            occ = pick(seed, ["农人", "工匠", "猎人", "牧人"], f"ref_occ_{k}", day)
            nm = _refugee_name(store, sid, seed, f"ref_{k}", day)
            cid = store.insert("character", {
                "settlement_id": sid, "faction_id": ids["villager_fid"],
                "name": nm, "occupation": occ, "age": age,
                "health": round(clamp(0.6 + unit(seed, f"ref_h_{k}", day) * 0.3, 0.3, 1.0), 2),
                "status": "alive",
                "profile": json.dumps({"origin": "避祸流落至此的异乡人",
                                       "look": "风尘仆仆", "habit": "少言",
                                       "obsession": "找回失散的亲人", "fear": "再被驱赶",
                                       "secret": "—"}, ensure_ascii=False),
                "traits": json.dumps({"勤勉": 0.5, "谨慎": 0.5, "好斗": 0.3,
                                      "仁厚": 0.5, "贪利": 0.3}, ensure_ascii=False),
                "needs": json.dumps({"food": 0.6, "safety": 0.6, "wood": 0.5}, ensure_ascii=False),
                "goal_stack": json.dumps(["在村里站稳脚跟"], ensure_ascii=False),
                "born_tick": -age * 365 * 24, "updated_tick": tick})
            arrived.append(nm)
        cx.emit(tick, "refugee_arrive",
                f"有 {n} 名流民拖家带口投奔本村，被收留安顿",
                effects={"names": arrived, "count": n},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    else:
        # 遣返：送走最边缘者（无户可附、非村长），但守住 G1 下限
        if pop <= guard.pop_floor + 1:
            return
        marginal = store.all(
            "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
            "AND occupation='老弱' ORDER BY id", (sid,))
        if not marginal:
            marginal = store.all(
                "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
                "AND occupation!='村长' ORDER BY id", (sid,))
        if not marginal:
            return
        victim = marginal[int(unit(seed, "ref_out", day) * len(marginal))]
        if store.scalar("SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'",
                       (sid,)) <= guard.pop_floor:
            return
        store.update("character", victim["id"], {"status": "left", "updated_tick": tick})
        cx.emit(tick, "refugee_depart",
                f"村中口粮吃紧，{victim['name']}等被婉言劝返，另寻生路",
                effects={"name": victim["name"]},
                place_kind="settlement", place_id=sid, actor_kind="settlement",
                actor_id=victim["id"],
                participants=[{"kind": "character", "id": victim["id"]}])


# ---------- M061 公共防疫与隔离管控 ----------
def settle_epidemic(store, cx, ids, tick, seed, day, guard):
    sid = ids["settlement_id"]
    ensure_infra_schema(store)
    active = store.one(
        "SELECT id, started_tick, quarantined, origin_event_id FROM epidemic "
        "WHERE settlement_id=? AND phase='active'", (sid,))

    if active is None:
        if not chance(seed, EPIDEMIC_BASE, "epi", day):
            return
        pop = store.scalar(
            "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'", (sid,))
        if pop and pop >= guard.pop_floor + 2:
            eid = cx.emit(tick, "epidemic_outbreak",
                          "村中陡然起了时疫，发热咳喘的人一日多过一日",
                          effects={"phase": "start"},
                          place_kind="settlement", place_id=sid, actor_kind="settlement")
            if eid is None:
                return
            store.insert("epidemic", {
                "settlement_id": sid, "started_tick": tick, "phase": "active",
                "quarantined": 0, "ended_tick": 0, "origin_event_id": eid})
            # 疫病压低民心，并令数人健康下滑
            s = store.one("SELECT morale FROM settlement WHERE id=?", (sid,))
            if s:
                store.update("settlement", sid,
                             {"morale": round(clamp(s["morale"] - 0.05, 0.02, 0.98), 4),
                              "updated_tick": tick})
            sick = store.all(
                "SELECT id, health FROM character WHERE settlement_id=? AND status='alive' "
                "AND occupation!='村长' ORDER BY id", (sid,))
            k = min(len(sick), 1 + int(unit(seed, "epi_sick", day) * 3))
            for person in sick[:k]:
                store.update("character", person["id"],
                             {"health": round(clamp(person["health"] - 0.2, 0.05, 1.0), 2),
                              "updated_tick": tick})
        return

    # 已爆发：封村与否、扩散、康复
    days = (tick - active["started_tick"]) // 24
    if days >= EPIDEMIC_DURATION:
        store.con.execute("UPDATE epidemic SET phase='ended', ended_tick=? WHERE id=?",
                          (tick, active["id"]))
        s = store.one("SELECT morale FROM settlement WHERE id=?", (sid,))
        if s:
            store.update("settlement", sid,
                         {"morale": round(clamp(s["morale"] + 0.04, 0.02, 0.98), 4),
                          "updated_tick": tick})
        cx.emit(tick, "epidemic_end", "熬过了这一轮时疫，村中渐渐复了元气",
                cause=active["origin_event_id"], effects={"phase": "end"},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
        return

    quarantined = bool(active["quarantined"])
    if not quarantined and chance(seed, QUARANTINE_BASE, "epi_q", day):
        store.con.execute("UPDATE epidemic SET quarantined=1, updated_tick=? WHERE id=?",
                          (tick, active["id"]))
        s = store.one("SELECT morale, order_value FROM settlement WHERE id=?", (sid,))
        if s:
            store.update("settlement", sid,
                         {"morale": round(clamp(s["morale"] - 0.03, 0.02, 0.98), 4),
                          "order_value": round(clamp(s["order_value"] + 0.02, 0.02, 0.98), 4),
                          "updated_tick": tick})
        cx.emit(tick, "quarantine",
                "里正下令封村断路，疑似者隔离看顾，宁可误了营生也不放疫出去",
                cause=active["origin_event_id"], effects={"quarantined": 1},
                place_kind="settlement", place_id=sid, actor_kind="settlement")

    # 扩散：封村则轻，否则偶有重症亡故（须有因死亡 G6）
    spread_p = 0.02 if quarantined else 0.10
    if chance(seed, spread_p, "epi_spread", day):
        sick = store.all(
            "SELECT id, name, health FROM character WHERE settlement_id=? AND status='alive' "
            "AND occupation!='村长' ORDER BY id", (sid,))
        if sick:
            person = sick[int(unit(seed, "epi_dead", day) * len(sick))]
            if person["health"] < 0.4 and chance(seed, 0.5, "epi_die", person["id"], day):
                eid = cx.emit(tick, "epidemic_death",
                              f"{person['name']}没熬过时疫，撒手去了",
                              cause=active["origin_event_id"],
                              effects={"health": [person["health"], 0.0],
                                       "status": ["alive", "dead"]},
                              place_kind="settlement", place_id=sid, actor_kind="character",
                              actor_id=person["id"])
                if guard.require_death_cause(eid, person["id"]):
                    store.update("character", person["id"],
                                 {"status": "dead", "health": 0.0, "death_event_id": eid,
                                  "died_tick": tick})
            else:
                store.update("character", person["id"],
                             {"health": round(clamp(person["health"] - 0.15, 0.05, 1.0), 2),
                              "updated_tick": tick})


# ---------- M063 河运漕运航道 ----------
def settle_waterway(store, cx, ids, tick, seed, day):
    sid, fid = ids["settlement_id"], ids["villager_fid"]
    if not chance(seed, WATERWAY_BASE, "water", day):
        return
    r = store.one("SELECT water_health FROM region WHERE id=?", (ids["region_id"],))
    # 水脉健康则通航顺、外销得利；水脉衰则漕运受阻，反受拖累
    if r and r["water_health"] > 0.45:
        coin_row = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                             "AND owner_id=? AND resource_type='coin'", (sid,))
        grain = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                          "AND owner_id=? AND resource_type='grain'", (sid,))
        price = round(8 + unit(seed, "way_p", day) * 24, 0)
        if coin_row and grain and grain["amount"] > price * 0.4:
            ship_grain = price * 0.4
            store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                              (ship_grain, tick, grain["id"]))
            store.con.execute("UPDATE stock SET amount=amount+?, updated_tick=? WHERE id=?",
                              (price, tick, coin_row["id"]))
            cx.emit(tick, "waterway",
                    f"趁水脉丰沛，村中雇船顺流运粮外销，换得 {price:.0f} 文",
                    effects={"coin_gain": price, "grain_shipped": round(ship_grain, 1)},
                    place_kind="region", place_id=ids["region_id"],
                    actor_kind="settlement", actor_id=sid)
    else:
        cx.emit(tick, "waterway",
                "水脉枯浅，漕运阻滞，外头的货进不来，村里的也出不去",
                effects={"water_health": round(r["water_health"], 3) if r else 0.3},
                place_kind="region", place_id=ids["region_id"],
                actor_kind="settlement", actor_id=sid)


# ---------- M065 双线通信 ----------
def settle_messenger(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, MESSENGER_BASE, "msg", day):
        return
    mode = pick(seed, ["驿路快马", "烽火传讯", "商队口信", "溪畔结绳为记"], "msg_m", day)
    s = store.one("SELECT morale FROM settlement WHERE id=?", (sid,))
    if s:
        store.update("settlement", sid,
                     {"morale": round(clamp(s["morale"] + 0.01, 0.02, 0.98), 4),
                      "updated_tick": tick})
    cx.emit(tick, "messenger",
            f"村中铺设{mode}，远信可至，人心不再与世隔绝",
            effects={"mode": mode},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M066 地域度量衡差异 ----------
def settle_measure(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, MEASURE_BASE, "measure", day):
        return
    local_unit = pick(seed, ["青溪斗", "白水秤", "长岗尺", "石梁升"], "ms_u", day)
    standard = pick(seed, ["官斗", "府秤", "营造尺"], "ms_s", day)
    # 折算总有损益：与外地交易时，村中要么占便宜要么吃亏（确定性）
    gain = round((unit(seed, "ms_g", day) - 0.5) * 16, 0)
    coin = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                     "AND owner_id=? AND resource_type='coin'", (sid,))
    tag = ""
    if coin and gain != 0:
        g = max(-coin["amount"], gain)
        store.con.execute("UPDATE stock SET amount=amount+?, updated_tick=? WHERE id=?",
                          (g, tick, coin["id"]))
        tag = f"，折算间 {'赚' if g > 0 else '折'}了 {abs(g):.0f} 文"
    cx.emit(tick, "measure_diff",
            f"外乡客商带来{standard}，与本地{local_unit}并不一般大{tag}",
            effects={"local": local_unit, "standard": standard, "coin_delta": gain},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M068 旅途行囊补给管理 ----------
def settle_provision(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, PROVISION_BASE, "prov", day):
        return
    grain = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                      "AND owner_id=? AND resource_type='grain'", (sid,))
    if not grain:
        return
    cost = round(2 + unit(seed, "prov_c", day) * 8, 1)
    if grain["amount"] < cost + 20:
        return
    # 过路旅人就地采买补给，村中得一文钱（外财），耗一点存粮
    coin = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                     "AND owner_id=? AND resource_type='coin'", (sid,))
    store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                      (cost, tick, grain["id"]))
    if coin:
        store.con.execute("UPDATE stock SET amount=amount+?, updated_tick=? WHERE id=?",
                          (cost * 0.8, tick, coin["id"]))
    cx.emit(tick, "provision",
            f"南来北往的行客在村口打尖，采买行囊干粮，用去 {cost:.1f} 单位存粮",
            effects={"grain_used": cost},
            place_kind="settlement", place_id=sid, actor_kind="settlement")
