# -*- coding: utf-8 -*-
"""P2 题材扩展：把 12 组题材标签真正接通（M004–M093 的题材模块）。

世界开局时 scheduler.schedule() 把题材解析成每个模块的 on/simple/off，
落进 module_instance。这里为 P2 的 55 个题材模块补上真实推演：只有当前
世界题材把某模块标为 on 时，对应机制才跑。于是同一句设定、同一套内核，
切换题材后事件构成与状态轨迹明显不同 —— 这是 P2 验收的核心。

确定性铁律（与 P0/P1 一致）：
  1. 随机只来自 base.unit/chance/pick(seed, 维度, day)，不碰 SQL RANDOM()/时间/内存态。
  2. 事件一律经 cx.emit，受 G4 每 tick 上限与 G2 因果深度约束。
  3. relation 写入先判重或 INSERT OR IGNORE，绝不触发 uq_relation。
  4. 涉及死亡必须先 emit 死亡事件并经 guard.require_death_cause 才落 status='dead'（G6）。
  5. 所有新表用 ensure_genre_schema 运行时按需补装（旧库非破坏升级）。
"""
from .base import unit, chance, pick, clamp


# ============================================================
# 题材机制专用表（运行时按需补装，旧库非破坏升级）
# ============================================================
GENRE_DDL = """
-- 题材机制：通用「在跑的线头」——羁绊/恩怨/密谋/潜伏/赝品/遗产/副本等
CREATE TABLE IF NOT EXISTS genre_thread (
    id INTEGER PRIMARY KEY,
    module_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_kind TEXT NOT NULL DEFAULT 'character',
    subject_id INTEGER,
    target_kind TEXT NOT NULL DEFAULT 'character',
    target_id INTEGER,
    stage INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',   -- active / resolved / failed
    started_tick INTEGER NOT NULL,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_genre_thread ON genre_thread(module_id, status);

-- 题材机制：化物（遗物/法器/丹药/赝品/信物/藏品）
CREATE TABLE IF NOT EXISTS genre_artifact (
    id INTEGER PRIMARY KEY,
    module_id TEXT NOT NULL,
    name TEXT NOT NULL,
    quality REAL NOT NULL DEFAULT 0.5,
    holder_kind TEXT NOT NULL DEFAULT 'settlement',
    holder_id INTEGER,
    status TEXT NOT NULL DEFAULT 'kept',      -- kept / lost / destroyed / fake
    created_tick INTEGER NOT NULL,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_genre_artifact ON genre_artifact(module_id, status);

-- 题材机制：痕迹/线索/藏匿点
CREATE TABLE IF NOT EXISTS genre_trace (
    id INTEGER PRIMARY KEY,
    module_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_kind TEXT NOT NULL DEFAULT 'character',
    subject_id INTEGER,
    strength REAL NOT NULL DEFAULT 1.0,
    detail TEXT NOT NULL DEFAULT '',
    tick INTEGER NOT NULL,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_genre_trace ON genre_trace(module_id, kind);

-- 题材机制：情报/线报/档案
CREATE TABLE IF NOT EXISTS genre_intel (
    id INTEGER PRIMARY KEY,
    module_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    faction_id INTEGER,
    reliability REAL NOT NULL DEFAULT 0.5,
    tick INTEGER NOT NULL,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_genre_intel ON genre_intel(module_id, kind);

-- 题材机制：预言/预兆
CREATE TABLE IF NOT EXISTS genre_omen (
    id INTEGER PRIMARY KEY,
    module_id TEXT NOT NULL,
    text TEXT NOT NULL,
    tick INTEGER NOT NULL,
    fulfilled INTEGER NOT NULL DEFAULT 0,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);

-- 题材机制：名声（区别于 power.repute：这里承载名号/口碑/圈层/学术等软声望）
CREATE TABLE IF NOT EXISTS standing (
    id INTEGER PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('character','faction','settlement')),
    owner_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.5,
    updated_tick INTEGER NOT NULL DEFAULT 0,
    UNIQUE(owner_kind, owner_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_standing_owner ON standing(owner_kind, owner_id);
"""


def ensure_genre_schema(store):
    store.con.executescript(GENRE_DDL)


# ============================================================
# 门控：题材是否让某模块跑完整推演
# ============================================================
def module_state(store, mid):
    return store.scalar("SELECT state FROM module_instance WHERE module_id=?", (mid,))


def module_on(store, mid):
    """仅当当前世界题材把该模块标为 on 时才返回 True（simple 不跑完整循环）。"""
    return module_state(store, mid) == "on"


# ============================================================
# 通用工具
# ============================================================
def _alive(store, sid):
    return store.all(
        "SELECT id, name, age, health, occupation, faction_id FROM character "
        "WHERE settlement_id=? AND status='alive' ORDER BY id", (sid,))


def _adults(store, sid):
    return [c for c in _alive(store, sid)
            if c["occupation"] not in ("幼儿", "老弱", "村长") and c["age"] >= 14]


def _pair(rowset, seed, *parts):
    """确定性地取两个不同的人。"""
    if len(rowset) < 2:
        return None, None
    i = int(unit(seed, *(parts + ("i",))) * len(rowset)) % len(rowset)
    j = int(unit(seed, *(parts + ("j",))) * len(rowset)) % len(rowset)
    if i == j:
        j = (j + 1) % len(rowset)
    return rowset[i], rowset[j]


def _rel(store, a, b, kind, intensity, tick, note="", publicity=1.0):
    """角色间有向关系写入（判重后 UPDATE / INSERT），不触发 uq_relation。"""
    if not a or not b or a == b:
        return
    row = store.one("SELECT id FROM relation WHERE from_kind='character' AND from_id=? "
                    "AND to_kind='character' AND to_id=? AND kind=?", (a, b, kind))
    if row:
        store.con.execute("UPDATE relation SET intensity=?, publicity=?, note=?, updated_tick=? "
                          "WHERE id=?", (round(intensity, 3), publicity, note, tick, row["id"]))
    else:
        store.con.execute(
            "INSERT OR IGNORE INTO relation(from_kind,from_id,to_kind,to_id,kind,intensity,"
            "publicity,note,updated_tick) VALUES('character',?,'character',?,?,?,?,?,?)",
            (a, b, kind, round(intensity, 3), publicity, note, tick))


def _standing(store, ok, oid, domain, delta, tick):
    if oid is None:
        return 0.5
    row = store.one("SELECT id,score FROM standing WHERE owner_kind=? AND owner_id=? AND domain=?",
                    (ok, oid, domain))
    if row:
        nv = clamp(row["score"] + delta, 0.0, 1.0)
        store.con.execute("UPDATE standing SET score=?, updated_tick=? WHERE id=?",
                          (round(nv, 3), tick, row["id"]))
        return nv
    nv = clamp(0.5 + delta, 0.0, 1.0)
    store.con.execute("INSERT OR IGNORE INTO standing(owner_kind,owner_id,domain,score,updated_tick) "
                      "VALUES(?,?,?,?,?)", (ok, oid, domain, round(nv, 3), tick))
    return nv


def _stock_add(store, ok, oid, rtype, delta, tick, capacity=None):
    row = store.one("SELECT id,amount,capacity FROM stock WHERE owner_kind=? AND owner_id=? "
                    "AND resource_type=?", (ok, oid, rtype))
    if not row:
        if delta <= 0:
            return
        cap = capacity if capacity is not None else 1000000.0
        store.con.execute("INSERT OR IGNORE INTO stock(owner_kind,owner_id,resource_type,amount,"
                          "capacity,updated_tick) VALUES(?,?,?,?,?,?)",
                          (ok, oid, rtype, round(max(0.0, min(delta, cap)), 3), cap, tick))
        return
    cap = capacity if capacity is not None else row["capacity"]
    nv = max(0.0, min(row["amount"] + delta, cap))
    store.con.execute("UPDATE stock SET amount=?, updated_tick=? WHERE id=?",
                      (round(nv, 3), tick, row["id"]))


def _stock_get(store, ok, oid, rtype):
    row = store.one("SELECT amount FROM stock WHERE owner_kind=? AND owner_id=? AND resource_type=?",
                    (ok, oid, rtype))
    return row["amount"] if row else 0.0


def _thread(store, module_id, kind, subject_kind, subject_id, target_kind, target_id,
            tick, meta="{}"):
    return store.con.execute(
        "INSERT INTO genre_thread(module_id,kind,subject_kind,subject_id,target_kind,target_id,"
        "stage,status,started_tick,updated_tick,meta) VALUES(?,?,?,?,?,?,0,'active',?,?,?)",
        (module_id, kind, subject_kind, subject_id, target_kind, target_id, tick, tick, meta)).lastrowid


def _artifact(store, module_id, name, quality, holder_kind, holder_id, tick, status="kept", meta="{}"):
    return store.con.execute(
        "INSERT INTO genre_artifact(module_id,name,quality,holder_kind,holder_id,status,"
        "created_tick,updated_tick,meta) VALUES(?,?,?,?,?,?,?,?,?)",
        (module_id, name, round(quality, 3), holder_kind, holder_id, status, tick, tick, meta)).lastrowid


def _trace(store, module_id, kind, subject_kind, subject_id, strength, detail, tick, meta="{}"):
    return store.con.execute(
        "INSERT INTO genre_trace(module_id,kind,subject_kind,subject_id,strength,detail,tick,"
        "updated_tick,meta) VALUES(?,?,?,?,?,?,?,?,?)",
        (module_id, kind, subject_kind, subject_id, round(strength, 3), detail, tick, tick, meta)).lastrowid


def _intel(store, module_id, kind, content, faction_id, reliability, tick, meta="{}"):
    return store.con.execute(
        "INSERT INTO genre_intel(module_id,kind,content,faction_id,reliability,tick,updated_tick,meta) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (module_id, kind, content, faction_id, round(reliability, 3), tick, tick, meta)).lastrowid


def _omen(store, module_id, text, tick, meta="{}"):
    return store.con.execute(
        "INSERT INTO genre_omen(module_id,text,tick,fulfilled,updated_tick,meta) VALUES(?,?,?,0,?,?)",
        (module_id, text, tick, tick, meta)).lastrowid


def _kill(store, cx, ch, tick, cause_id, reason):
    """G6 合规的死亡：先 emit 死亡事件，经护栏核验后再落 status='dead'。"""
    eid = cx.emit(tick, "death", f"{ch['name']} 死于{reason}", cause=cause_id,
                  effects={"health": [ch["health"], 0.0], "status": ["alive", "dead"]},
                  place_kind="settlement", actor_kind="character", actor_id=ch["id"])
    if eid is None or not cx.guard.require_death_cause(eid, ch["id"]):
        return False
    store.con.execute("UPDATE character SET status='dead', health=0.0, death_event_id=?, "
                      "died_tick=?, updated_tick=? WHERE id=?", (eid, tick, tick, ch["id"]))
    return True


def _sid(ids):
    return ids["settlement_id"]


def _fid(ids):
    return ids["villager_fid"]


# ============================================================
# 玄幻 / 仙侠 / 高武
# ============================================================
def _xuan_bonds(store, cx, ids, tick, seed, day):
    """M004 师承血脉羁绊 / M005 信物羁绊 / M006 遗物共鸣 / M007 名号绰号 /
       M009 徽记符号 / M010 遗迹发掘。"""
    sid = _sid(ids)
    mods = ("M004", "M005", "M006", "M007", "M009", "M010")
    if not any(module_on(store, m) for m in mods):
        return
    adults = _adults(store, sid)
    if len(adults) < 3:
        return

    if module_on(store, "M004") and chance(seed, 0.16, "M004", day):
        a, b = _pair(adults, seed, "M004", day)
        _rel(store, a["id"], b["id"], "恩", 0.65, tick, "师承血脉")
        _rel(store, b["id"], a["id"], "恩", 0.55, tick, "门下血脉")
        cx.emit(tick, "lineage_bond", f"{a['name']} 依血脉师承拜入 {b['name']} 门下",
                cause=None, effects={"master": b["name"], "disciple": a["name"],
                                     "bond": "师承血脉羁绊"},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])

    if module_on(store, "M005") and chance(seed, 0.14, "M005", day):
        a, b = _pair(adults, seed, "M005", day)
        _rel(store, a["id"], b["id"], "恩", 0.5, tick, "信物之约")
        _artifact(store, "M005", f"{a['name']}与{b['name']}的信物", 0.45 + unit(seed, "M005q", day) * 0.3,
                  "character", a["id"], tick, meta='{"bond":1}')
        cx.emit(tick, "token_bond", f"{a['name']} 与 {b['name']} 互赠信物，以此为凭",
                effects={"holders": [a["name"], b["name"]]},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])

    if module_on(store, "M006") and chance(seed, 0.11, "M006", day):
        a, _ = _pair(adults, seed, "M006", day)
        q = 0.5 + unit(seed, "M006q", day) * 0.5
        _artifact(store, "M006", "无名遗物", q, "character", a["id"], tick, meta='{"relic":1}')
        cx.emit(tick, "relic_resonance", f"{a['name']} 握住的遗物泛起共鸣，似有旧主回声",
                effects={"holder": a["name"], "quality": round(q, 3)},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])

    if module_on(store, "M007") and chance(seed, 0.18, "M007", day):
        a, _ = _pair(adults, seed, "M007", day)
        nv = _standing(store, "character", a["id"], "title", 0.12, tick)
        cx.emit(tick, "title_grant", f"江湖送 {a['name']} 一个新名号，绰号就此传开",
                effects={"person": a["name"], "standing_title": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])

    if module_on(store, "M009") and chance(seed, 0.12, "M009", day):
        f = store.one("SELECT id,name FROM faction WHERE status='active' ORDER BY id LIMIT 1")
        if f:
            nv = _standing(store, "faction", f["id"], "emblem", 0.1, tick)
            cx.emit(tick, "emblem_raise", f"{f['name']} 立起新的徽记符号，自成一脉标识",
                    effects={"faction": f["name"], "standing_emblem": nv},
                    place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=f["id"])

    if module_on(store, "M010") and chance(seed, 0.13, "M010", day):
        a, _ = _pair(adults, seed, "M010", day)
        q = 0.4 + unit(seed, "M010q", day) * 0.6
        _artifact(store, "M010", "遗迹发掘所得", q, "settlement", sid, tick, meta='{"ruin":1}')
        gain = round(4.0 + q * 20.0, 1)
        _stock_add(store, "settlement", sid, "coin", gain, tick)
        cx.emit(tick, "ruin_dig", f"{a['name']} 掘开一处遗迹，得旧物若干（估值 {gain}）",
                effects={"digger": a["name"], "quality": round(q, 3), "coin_gain": gain},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])


def _omens(store, cx, ids, tick, seed, day):
    """M008 预言预兆 / M092 风俗礼仪与节日庆典 / M093 丧葬祭祀与信仰。"""
    sid = _sid(ids)
    if module_on(store, "M008") and chance(seed, 0.22, "M008", day):
        texts = ["岁星逆行，主村落有变", "夜闻异声，将有远客", "枯井复涌，福祸未卜",
                 "赤虹贯日，兵戈之兆", "白鹿入林，主丰年"]
        t = pick(seed, texts, "M008", day)
        _omen(store, "M008", t, tick)
        cx.emit(tick, "omen", f"方士断言：「{t}」",
                effects={"omen": t}, place_kind="settlement", place_id=sid, actor_kind="engine")
    if module_on(store, "M092") and chance(seed, 0.5, "M092", day):
        names = ["春社", "秋报", "腊祭", "上元", "端午", "中秋"]
        fest = pick(seed, names, "M092", day)
        store.con.execute("UPDATE settlement SET morale=MIN(1.0,morale+0.04), updated_tick=? WHERE id=?",
                          (tick, sid))
        cx.emit(tick, "festival", f"村中过 {fest}，邻里同庆，民心稍安",
                effects={"festival": fest, "morale_delta": 0.04},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    if module_on(store, "M093") and chance(seed, 0.4, "M093", day):
        dead = store.scalar("SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='dead' "
                            "AND died_tick>=?", (sid, tick - 24 * 120))
        if dead:
            nv = _standing(store, "settlement", sid, "rite", 0.05, tick)
            cx.emit(tick, "rite", f"村人为年内逝者（{dead} 人）设祭，香火不绝",
                    effects={"dead_recent": dead, "standing_rite": nv},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")


def _xuan_cultivation(store, cx, ids, tick, seed, day):
    """M077 灵能灵气资源循环 / M078 功法武技传承 / M080 丹药法器炼制 / M081 宗门任务考核。"""
    sid = _sid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M077"):
        flow = round(6.0 + unit(seed, "M077", day // 120) * 10.0, 1)
        _stock_add(store, "settlement", sid, "qi", flow, tick)
        if chance(seed, 0.3, "M077e", day):
            cx.emit(tick, "qi_cycle", f"方圆灵气涨落，聚落化纳灵气 {flow}",
                    effects={"qi_gain": flow}, place_kind="settlement", place_id=sid,
                    actor_kind="engine")
    if module_on(store, "M078") and adults and chance(seed, 0.18, "M078", day):
        a, b = _pair(adults, seed, "M078", day)
        _rel(store, a["id"], b["id"], "恩", 0.45, tick, "传功")
        nv = _standing(store, "character", a["id"], "skill", 0.12, tick)
        cx.emit(tick, "technique_pass", f"{b['name']} 将一门功法倾囊传于 {a['name']}",
                effects={"learner": a["name"], "teacher": b["name"], "standing_skill": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M080") and chance(seed, 0.2, "M080", day):
        qi = _stock_get(store, "settlement", sid, "qi")
        med = _stock_get(store, "settlement", sid, "medicine")
        if qi >= 3:
            _stock_add(store, "settlement", sid, "qi", -3.0, tick)
            q = 0.45 + unit(seed, "M080q", day) * 0.5
            nm = pick(seed, ["回春丹", "凝元丹", "青锋剑", "纳灵符", "护身法衣"], "M080n", day)
            _artifact(store, "M080", nm, q, "settlement", sid, tick, meta='{"craft":1}')
            healable = [c for c in _alive(store, sid) if c["health"] < 0.999]
            if healable:
                tgt = healable[int(unit(seed, "M080t", day) * len(healable)) % len(healable)]
                nh = clamp(tgt["health"] + 0.25, 0.0, 1.0)
                store.con.execute("UPDATE character SET health=?, updated_tick=? WHERE id=?",
                                  (round(nh, 3), tick, tgt["id"]))
            cx.emit(tick, "alchemy_craft", f"以灵气 3 炼成「{nm}」，品质 {q:.2f}",
                    effects={"artifact": nm, "quality": round(q, 3), "qi_used": 3.0},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")
        elif med >= 2:
            _stock_add(store, "settlement", sid, "medicine", -2.0, tick)
            cx.emit(tick, "alchemy_craft", "以药材 2 炮制伤药，可备后用",
                    effects={"medicine_used": 2.0},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")
    if module_on(store, "M081") and adults and chance(seed, 0.28, "M081", day):
        a, _ = _pair(adults, seed, "M081", day)
        _thread(store, "M081", "mission", "character", a["id"], "settlement", sid, tick,
                meta='{"reward":"standing"}')
        nv = _standing(store, "character", a["id"], "merit", 0.1, tick)
        cx.emit(tick, "sect_mission", f"宗门向 {a['name']} 派下任务，功过另计",
                effects={"assignee": a["name"], "standing_merit": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])


def _xuan_beasts(store, cx, ids, tick, seed, day):
    """M079 妖兽异兽生态。"""
    if not module_on(store, "M079"):
        return
    rid = ids["region_id"]
    r = store.one("SELECT id,game_density FROM region WHERE id=?", (rid,))
    if not r:
        return
    if chance(seed, 0.35, "M079", day):
        delta = round((unit(seed, "M079d", day) - 0.45) * 0.08, 4)
        nd = clamp(r["game_density"] + delta, 0.05, 1.0)
        store.con.execute("UPDATE region SET game_density=?, updated_tick=? WHERE id=?",
                          (round(nd, 4), tick, r["id"]))
        cx.emit(tick, "beast_shift", f"山泽间妖兽异兽出没，猎物密度变为 {nd:.2f}",
                effects={"game_density": [round(r["game_density"], 4), round(nd, 4)]},
                place_kind="region", place_id=rid, actor_kind="nature")
        bf = store.scalar("SELECT id FROM faction WHERE kind='野兽' LIMIT 1")
        if bf:
            store.con.execute("UPDATE faction SET manpower=MAX(0,manpower+?), updated_tick=? WHERE id=?",
                              (1 if delta > 0 else -1, tick, bf))


def _battle_support(store, cx, ids, tick, seed, day):
    """M082 战场损伤 / M083 俘虏处置 / M084 人质挟持 / M085 药品伤药 /
       M086 毒药迷药 / M087 陷阱布设。"""
    sid = _sid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M082") and adults and chance(seed, 0.25, "M082", day):
        hurt = [c for c in adults if chance(seed, 0.3, "M082s", day, c["id"])]
        if hurt:
            for c in hurt[:3]:
                nh = clamp(c["health"] - (0.15 + unit(seed, "M082h", day, c["id"]) * 0.25), 0.05, 1.0)
                store.con.execute("UPDATE character SET health=?, updated_tick=? WHERE id=?",
                                  (round(nh, 3), tick, c["id"]))
            cx.emit(tick, "battle_injury", f"一场械斗后 {len(hurt[:3])} 人负伤，需将养",
                    effects={"injured": [c["name"] for c in hurt[:3]]},
                    place_kind="settlement", place_id=sid, actor_kind="character")
    if module_on(store, "M083") and adults and chance(seed, 0.16, "M083", day):
        a, b = _pair(adults, seed, "M083", day)
        _thread(store, "M083", "captive", "character", a["id"], "character", b["id"], tick)
        coin = _stock_get(store, "settlement", sid, "coin")
        ransom = round(min(max(0.0, coin * 0.05), 30.0), 1)
        if ransom > 0:
            _stock_add(store, "settlement", sid, "coin", -ransom, tick)
        cx.emit(tick, "captive_deal", f"{b['name']} 被掳，议定以 {ransom} 钱赎回",
                effects={"captive": b["name"], "captor": a["name"], "ransom_coin": ransom},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M084") and adults and chance(seed, 0.14, "M084", day):
        a, b = _pair(adults, seed, "M084", day)
        _thread(store, "M084", "hostage", "character", a["id"], "character", b["id"], tick)
        _rel(store, a["id"], b["id"], "忌", -0.6, tick, "人质之怨")
        cx.emit(tick, "hostage_swap", f"{a['name']} 挟持 {b['name']} 以为质，双方僵持",
                effects={"holder": a["name"], "hostage": b["name"]},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M085") and chance(seed, 0.25, "M085", day):
        med = _stock_get(store, "settlement", sid, "medicine")
        if med >= 1:
            _stock_add(store, "settlement", sid, "medicine", -1.0, tick)
            hurt = [c for c in _alive(store, sid) if c["health"] < 0.99]
            if hurt:
                tgt = hurt[int(unit(seed, "M085t", day) * len(hurt)) % len(hurt)]
                nh = clamp(tgt["health"] + 0.3, 0.0, 1.0)
                store.con.execute("UPDATE character SET health=?, updated_tick=? WHERE id=?",
                                  (round(nh, 3), tick, tgt["id"]))
            cx.emit(tick, "medicine_use", "以伤药救治伤者，创口渐合",
                    effects={"medicine_used": 1.0},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")
    if module_on(store, "M086") and adults and chance(seed, 0.15, "M086", day):
        a, b = _pair(adults, seed, "M086", day)
        nh = clamp(b["health"] - (0.2 + unit(seed, "M086h", day) * 0.3), 0.05, 1.0)
        store.con.execute("UPDATE character SET health=?, updated_tick=? WHERE id=?",
                          (round(nh, 3), tick, b["id"]))
        _rel(store, a["id"], b["id"], "怨", -0.7, tick, "下毒")
        cx.emit(tick, "poison_plot", f"{b['name']} 中毒不起，疑为 {a['name']} 所下",
                effects={"victim": b["name"], "suspect": a["name"],
                         "health": [b["health"], round(nh, 3)]},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"],
                visibility="secret")
    if module_on(store, "M087") and chance(seed, 0.2, "M087", day):
        _trace(store, "M087", "trap", "settlement", sid, 0.7, "环村布设的陷阱", tick)
        bf = store.scalar("SELECT id FROM faction WHERE kind='野兽' LIMIT 1")
        if bf:
            store.con.execute("UPDATE faction SET manpower=MAX(0,manpower-1), updated_tick=? WHERE id=?",
                              (tick, bf))
        cx.emit(tick, "trap_set", "村人沿林缘布下陷阱，野兽来犯受阻",
                effects={"trap_strength": 0.7}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")


def _inquiry(store, cx, ids, tick, seed, day):
    """M019 证词博弈伪证 / M020 证人庇护 / M021 痕迹留存衰减 / M022 秘密藏匿点 /
       M023 残简碎片线索拼接。"""
    sid = _sid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M021"):
        old = store.all("SELECT id,strength FROM genre_trace WHERE module_id='M021' "
                        "AND tick<? ORDER BY id LIMIT 20", (tick - 24 * 30,))
        for tr in old:
            ns = round(tr["strength"] * 0.6, 3)
            if ns < 0.05:
                store.con.execute("DELETE FROM genre_trace WHERE id=?", (tr["id"],))
            else:
                store.con.execute("UPDATE genre_trace SET strength=?, updated_tick=? WHERE id=?",
                                  (ns, tick, tr["id"]))
    if module_on(store, "M019") and adults and chance(seed, 0.22, "M019", day):
        a, b = _pair(adults, seed, "M019", day)
        _rel(store, a["id"], b["id"], "怨", -0.5, tick, "公堂对质")
        store.con.execute("UPDATE settlement SET order_value=MAX(0.0,order_value-0.02), updated_tick=? "
                          "WHERE id=?", (tick, sid))
        _trace(store, "M019", "testimony", "character", a["id"], 0.6, "当堂证词", tick)
        cx.emit(tick, "testimony_clash", f"公堂之上 {a['name']} 与 {b['name']} 各执一词，真假难辨",
                effects={"witness_a": a["name"], "witness_b": b["name"]},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M020") and adults and chance(seed, 0.18, "M020", day):
        a, _ = _pair(adults, seed, "M020", day)
        _thread(store, "M020", "witness", "character", a["id"], "settlement", sid, tick)
        cx.emit(tick, "witness_shield", f"{a['name']} 被暗中庇护，证词不再外流",
                effects={"protected": a["name"]}, place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=a["id"], visibility="private")
    if module_on(store, "M022") and adults and chance(seed, 0.15, "M022", day):
        a, _ = _pair(adults, seed, "M022", day)
        _trace(store, "M022", "hideout", "character", a["id"], 0.8, "隐秘窝点", tick)
        cx.emit(tick, "hideout_made", f"{a['name']} 收拾出一处藏物之所",
                effects={"owner": a["name"]}, place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=a["id"], visibility="secret")
    if module_on(store, "M023") and chance(seed, 0.2, "M023", day):
        frags = store.scalar("SELECT COUNT(*) FROM genre_trace WHERE module_id='M023' "
                            "AND kind='fragment'")
        if frags >= 3:
            store.con.execute("DELETE FROM genre_trace WHERE module_id='M023' AND kind='fragment'")
            _intel(store, "M023", "assembled_clue", "残简碎片终被拼合，指向一处旧事", None, 0.8, tick)
            cx.emit(tick, "clue_assembled", "残简碎片悉数拼合，一处被掩埋的旧事浮现",
                    effects={"fragments": 3}, place_kind="settlement", place_id=sid,
                    actor_kind="engine")
        else:
            _trace(store, "M023", "fragment", "settlement", sid, 0.5, "残简碎片", tick)
            cx.emit(tick, "clue_fragment", "有人拾得一片残简，字迹残缺难辨",
                    effects={"fragments": frags + 1}, place_kind="settlement", place_id=sid,
                    actor_kind="engine")


def _intel_ops(store, cx, ids, tick, seed, day):
    """M027 密信暗号隐写 / M028 卧底潜伏渗透 / M029 情报驿站网络 /
       M030 情报收买线人 / M031 秘密交易黑市。"""
    sid = _sid(ids)
    fid = _fid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M027") and chance(seed, 0.2, "M027", day):
        _intel(store, "M027", "cipher", "密信以暗号隐写，非知机者不能读", fid, 0.7, tick)
        cx.emit(tick, "cipher_message", "一封隐写密信在暗处流转，字句皆藏机锋",
                effects={"reliability": 0.7}, place_kind="settlement", place_id=sid,
                actor_kind="faction", actor_id=fid, visibility="secret")
    if module_on(store, "M028") and adults and chance(seed, 0.13, "M028", day):
        a, _ = _pair(adults, seed, "M028", day)
        _thread(store, "M028", "undercover", "character", a["id"], "faction", fid, tick,
                meta='{"deep":1}')
        cx.emit(tick, "infiltrate", f"{a['name']} 改了身份混入外间，行迹隐秘",
                effects={"agent": a["name"]}, place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=a["id"], visibility="secret")
    if module_on(store, "M029") and chance(seed, 0.25, "M029", day):
        _intel(store, "M029", "relay", "驿站递来外地消息一则", fid,
               0.4 + unit(seed, "M029r", day) * 0.4, tick)
        cx.emit(tick, "intel_relay", "情报驿站递来一则可用的消息",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="faction",
                actor_id=fid, visibility="private")
    if module_on(store, "M030") and adults and chance(seed, 0.22, "M030", day):
        a, _ = _pair(adults, seed, "M030", day)
        coin = _stock_get(store, "settlement", sid, "coin")
        cost = round(min(max(0.0, coin * 0.03), 12.0), 1)
        if cost > 0:
            _stock_add(store, "settlement", sid, "coin", -cost, tick)
        _thread(store, "M030", "informant", "character", a["id"], "faction", fid, tick)
        _intel(store, "M030", "informant_report", f"{a['name']} 受雇呈报线报", fid, 0.6, tick)
        cx.emit(tick, "informant_paid", f"以 {cost} 钱收买 {a['name']} 为线人",
                effects={"informant": a["name"], "coin_spent": cost},
                place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=fid,
                visibility="secret")
    if module_on(store, "M031") and adults and chance(seed, 0.24, "M031", day):
        a, _ = _pair(adults, seed, "M031", day)
        _thread(store, "M031", "blackmarket", "character", a["id"], "settlement", sid, tick)
        _stock_add(store, "settlement", sid, "coin", round(2.0 + unit(seed, "M031c", day) * 8.0, 1), tick)
        cx.emit(tick, "black_market", f"{a['name']} 在黑市做成一批见不得光的买卖",
                effects={"dealer": a["name"]}, place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=a["id"], visibility="secret")


def _covert(store, cx, ids, tick, seed, day):
    """M025 档案卷宗 / M033 赝品造假鉴别 / M036 保密契约泄密 / M072 藏匿密室。"""
    sid = _sid(ids)
    fid = _fid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M025") and chance(seed, 0.3, "M025", day):
        _intel(store, "M025", "archive", "卷宗归档：本季人事、讼案与账目俱录在案", fid, 0.9, tick)
        cx.emit(tick, "archive_entry", "档案卷宗添了新的一页，旧事有了凭据",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="faction",
                actor_id=fid, visibility="private")
    if module_on(store, "M033") and chance(seed, 0.18, "M033", day):
        fake = chance(seed, 0.5, "M033f", day)
        nm = pick(seed, ["古画", "旧瓷", "孤本", "名砚", "老玉"], "M033n", day)
        if fake:
            _artifact(store, "M033", f"{nm}（赝品）", 0.3, "settlement", sid, tick, status="fake",
                      meta='{"forgery":1}')
            cx.emit(tick, "forgery_pass", f"一幅「{nm}」以假乱真过了手，行家一时未能辨",
                    effects={"item": nm, "forgery": True},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")
        else:
            _artifact(store, "M033", f"{nm}（真）", 0.8, "settlement", sid, tick, meta='{"appraised":1}')
            cx.emit(tick, "appraisal", f"行家验出「{nm}」是真品，价增数倍",
                    effects={"item": nm, "forgery": False},
                    place_kind="settlement", place_id=sid, actor_kind="settlement")
    if module_on(store, "M036") and adults and chance(seed, 0.16, "M036", day):
        a, b = _pair(adults, seed, "M036", day)
        _thread(store, "M036", "secrecy", "character", a["id"], "character", b["id"], tick)
        if chance(seed, 0.4, "M036leak", day):
            _rel(store, a["id"], b["id"], "怨", -0.6, tick, "泄密追责")
            cx.emit(tick, "leak_pursued", f"{b['name']} 背约泄密，{a['name']} 上门追责",
                    effects={"leaker": b["name"], "aggrieved": a["name"]},
                    place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
        else:
            cx.emit(tick, "secrecy_pact", f"{a['name']} 与 {b['name']} 立下保密之约，不得外传",
                    effects={"parties": [a["name"], b["name"]]},
                    place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"],
                    visibility="secret")
    if module_on(store, "M072") and chance(seed, 0.2, "M072", day):
        _trace(store, "M072", "vault", "settlement", sid, 0.8, "藏匿密室", tick)
        cx.emit(tick, "vault_built", "一处密室落成，紧要之物有了去处",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="settlement",
                visibility="secret")


def _dushi(store, cx, ids, tick, seed, day):
    """M024 身份隐匿伪装 / M037 遗产争夺家族内斗 / M041 职场圈层 /
       M042 娱乐圈口碑直播声誉。"""
    sid = _sid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M024") and adults and chance(seed, 0.2, "M024", day):
        a, _ = _pair(adults, seed, "M024", day)
        _trace(store, "M024", "disguise", "character", a["id"], 0.7, "伪造身份", tick)
        cx.emit(tick, "disguise", f"{a['name']} 换了副面孔与他人身份示人",
                effects={"subject": a["name"]}, place_kind="settlement", place_id=sid,
                actor_kind="character", actor_id=a["id"], visibility="secret")
    if module_on(store, "M037") and adults and chance(seed, 0.2, "M037", day):
        a, b = _pair(adults, seed, "M037", day)
        _rel(store, a["id"], b["id"], "忌", -0.55, tick, "争产")
        _rel(store, b["id"], a["id"], "忌", -0.5, tick, "争产")
        store.con.execute("UPDATE settlement SET order_value=MAX(0.0,order_value-0.015), updated_tick=? "
                          "WHERE id=?", (tick, sid))
        cx.emit(tick, "inheritance_feud", f"{a['name']} 与 {b['name']} 为家产反目，内斗渐起",
                effects={"claimant_a": a["name"], "claimant_b": b["name"]},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M041") and adults and chance(seed, 0.22, "M041", day):
        a, _ = _pair(adults, seed, "M041", day)
        nv = _standing(store, "character", a["id"], "career", 0.12, tick)
        cx.emit(tick, "workplace_strife", f"{a['name']} 在队伍里合纵连横，站稳一角",
                effects={"person": a["name"], "standing_career": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M042") and adults and chance(seed, 0.22, "M042", day):
        a, _ = _pair(adults, seed, "M042", day)
        up = unit(seed, "M042u", day) > 0.4
        nv = _standing(store, "character", a["id"], "fame", 0.15 if up else -0.15, tick)
        cx.emit(tick, "fame_flux", f"{a['name']} 名声大噪" if up else f"{a['name']} 口碑翻车，人皆侧目",
                effects={"person": a["name"], "standing_fame": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])


def _cyber(store, cx, ids, tick, seed, day):
    """M073 副本空间 / M074 赛博身份电子档案 / M075 信用票据电子信贷 /
       M076 星际资源开采与航道。"""
    sid = _sid(ids)
    fid = _fid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M073") and adults and chance(seed, 0.18, "M073", day):
        a, _ = _pair(adults, seed, "M073", day)
        q = 0.4 + unit(seed, "M073q", day) * 0.6
        _thread(store, "M073", "instance", "character", a["id"], "settlement", sid, tick)
        _artifact(store, "M073", "副本掉落之物", q, "character", a["id"], tick, meta='{"instance":1}')
        cx.emit(tick, "instance_clear", f"{a['name']} 自一处副本空间全身而退，带回所得",
                effects={"runner": a["name"], "quality": round(q, 3)},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])
    if module_on(store, "M074") and adults and chance(seed, 0.24, "M074", day):
        a, _ = _pair(adults, seed, "M074", day)
        _intel(store, "M074", "e_dossier", f"{a['name']} 的电子档案被改写", fid, 0.7, tick)
        nv = _standing(store, "character", a["id"], "identity", 0.1, tick)
        cx.emit(tick, "e_dossier", f"{a['name']} 的电子档案被人动了手脚",
                effects={"subject": a["name"], "standing_identity": nv},
                place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=fid,
                visibility="secret")
    if module_on(store, "M075") and chance(seed, 0.25, "M075", day):
        _stock_add(store, "settlement", sid, "coin", round(3.0 + unit(seed, "M075c", day) * 10.0, 1), tick)
        _intel(store, "M075", "credit", "电子信贷放出一笔款项", fid, 0.6, tick)
        cx.emit(tick, "e_credit", "凭信用票据放出一笔电子信贷，钱款到账",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=fid)
    if module_on(store, "M076") and chance(seed, 0.3, "M076", day):
        ore = round(2.0 + unit(seed, "M076o", day) * 8.0, 1)
        _stock_add(store, "settlement", sid, "ore", ore, tick)
        cx.emit(tick, "space_mining", f"星际航道开出一批矿藏，入账矿石 {ore}",
                effects={"ore_gain": ore}, place_kind="region", place_id=ids["region_id"],
                actor_kind="settlement")


def _lishi_gov(store, cx, ids, tick, seed, day):
    """M057 赋税征收 / M058 保甲连坐 / M067 科举人才选拔。"""
    sid = _sid(ids)
    fid = _fid(ids)
    adults = _adults(store, sid)
    if module_on(store, "M057") and chance(seed, 0.6, "M057", day):
        grain = _stock_get(store, "settlement", sid, "grain")
        pop = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,)) or 0
        reserve = max(0.0, grain - pop * 1.2 * 10)
        levy = round(min(reserve * 0.08, max(0.0, reserve)), 2)
        if levy >= 0.5:
            _stock_add(store, "settlement", sid, "grain", -levy, tick)
            _stock_add(store, "faction", fid, "grain", levy, tick)
            cx.emit(tick, "tax_assess", f"依户等征赋，起运粮 {levy} 入公仓",
                    effects={"assessed_grain": levy}, place_kind="settlement", place_id=sid,
                    actor_kind="faction", actor_id=fid)
    if module_on(store, "M058") and chance(seed, 0.4, "M058", day):
        store.con.execute("UPDATE settlement SET order_value=MIN(1.0,order_value+0.02), updated_tick=? "
                          "WHERE id=?", (tick, sid))
        if adults:
            a, b = _pair(adults, seed, "M058", day)
            _rel(store, a["id"], b["id"], "恩", 0.2, tick, "同甲互保")
        cx.emit(tick, "baojia", "保甲编成，邻里互相稽察连坐，治安稍靖",
                effects={"order_delta": 0.02}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")
    if module_on(store, "M067") and adults and chance(seed, 0.16, "M067", day):
        a, _ = _pair(adults, seed, "M067", day)
        nv = _standing(store, "character", a["id"], "scholar", 0.18, tick)
        if a["occupation"] == "农人" and unit(seed, "M067o", day) > 0.5:
            store.con.execute("UPDATE character SET occupation='读书人', updated_tick=? WHERE id=?",
                              (tick, a["id"]))
        cx.emit(tick, "exam_pass", f"{a['name']} 经科考得中，士林有声",
                effects={"scholar": a["name"], "standing_scholar": nv},
                place_kind="settlement", place_id=sid, actor_kind="character", actor_id=a["id"])


def _lishi_daily(store, cx, ids, tick, seed, day):
    """M055 家畜畜牧 / M064 渡口浮桥收费 / M088 交通工具体系 / M089 向导保镖 /
       M090 旅店客栈驿站 / M091 食物食材采集加工。"""
    sid = _sid(ids)
    if module_on(store, "M055") and chance(seed, 0.35, "M055", day):
        meat = round(1.5 + unit(seed, "M055m", day) * 4.0, 1)
        _stock_add(store, "settlement", sid, "meat", meat, tick)
        cx.emit(tick, "livestock", f"牲畜长大出栏，添肉食 {meat}",
                effects={"meat_gain": meat}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")
    if module_on(store, "M064") and chance(seed, 0.3, "M064", day):
        toll = round(1.0 + unit(seed, "M064t", day) * 4.0, 1)
        _stock_add(store, "settlement", sid, "coin", toll, tick)
        cx.emit(tick, "toll_income", f"渡口浮桥收过路钱 {toll}",
                effects={"toll_coin": toll}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")
    if module_on(store, "M088") and chance(seed, 0.28, "M088", day):
        cx.emit(tick, "transport", "新添车马舟具，往来脚力见便",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="settlement")
    if module_on(store, "M089") and chance(seed, 0.22, "M089", day):
        cost = round(1.0 + unit(seed, "M089c", day) * 5.0, 1)
        _stock_add(store, "settlement", sid, "coin", -cost, tick)
        cx.emit(tick, "guide_hire", f"雇下向导保镖，花去 {cost} 钱",
                effects={"coin_spent": cost}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")
    if module_on(store, "M090") and chance(seed, 0.3, "M090", day):
        income = round(1.5 + unit(seed, "M090i", day) * 6.0, 1)
        _stock_add(store, "settlement", sid, "coin", income, tick)
        cx.emit(tick, "inn_income", f"旅店客栈住客盈门，入账 {income} 钱",
                effects={"inn_coin": income}, place_kind="settlement", place_id=sid,
                actor_kind="settlement")
    if module_on(store, "M091") and chance(seed, 0.4, "M091", day):
        food = round(1.0 + unit(seed, "M091f", day) * 3.5, 1)
        _stock_add(store, "settlement", sid, "grain", food, tick)
        cx.emit(tick, "forage", f"采集食物食材，得粮 {food}",
                effects={"grain_gain": food}, place_kind="region", place_id=ids["region_id"],
                actor_kind="settlement")


def _disaster(store, cx, ids, tick, seed, day):
    """M069 野生动物生态 / M070 自然灾害动态事件 / M071 囤物资源争夺。"""
    sid = _sid(ids)
    rid = ids["region_id"]
    if module_on(store, "M069") and chance(seed, 0.3, "M069", day):
        r = store.one("SELECT id,game_density FROM region WHERE id=?", (rid,))
        if r:
            nd = clamp(r["game_density"] + (unit(seed, "M069d", day) - 0.5) * 0.06, 0.05, 1.0)
            store.con.execute("UPDATE region SET game_density=?, updated_tick=? WHERE id=?",
                              (round(nd, 4), tick, r["id"]))
            cx.emit(tick, "wildlife_shift", f"野生动物群落迁移，猎物密度变为 {nd:.2f}",
                    effects={"game_density": [round(r["game_density"], 4), round(nd, 4)]},
                    place_kind="region", place_id=rid, actor_kind="nature")
    if module_on(store, "M070") and chance(seed, 0.22, "M070", day):
        kinds = ["洪水", "大旱", "蝗灾", "瘟疫之风", "山火", "雹灾"]
        dk = pick(seed, kinds, "M070k", day)
        r = store.one("SELECT id,forest,fertility FROM region WHERE id=?", (rid,))
        if r:
            nf = clamp(r["forest"] - 0.05, 0.0, 1.0)
            nfe = clamp(r["fertility"] - 0.06, 0.1, 1.0)
            store.con.execute("UPDATE region SET forest=?, fertility=?, updated_tick=? WHERE id=?",
                              (round(nf, 4), round(nfe, 4), tick, r["id"]))
        store.con.execute("UPDATE settlement SET morale=MAX(0.0,morale-0.05), "
                          "order_value=MAX(0.0,order_value-0.03), updated_tick=? WHERE id=?", (tick, sid))
        grain = _stock_get(store, "settlement", sid, "grain")
        if grain > 0:
            _stock_add(store, "settlement", sid, "grain", -round(grain * 0.06, 2), tick)
        cx.emit(tick, "natural_disaster", f"{dk}骤至，田庐受损，民心浮动",
                effects={"disaster": dk, "morale_delta": -0.05},
                place_kind="region", place_id=rid, actor_kind="nature")
    if module_on(store, "M071") and chance(seed, 0.28, "M071", day):
        grain = _stock_get(store, "settlement", sid, "grain")
        if grain > 0:
            grab = round(grain * 0.03, 2)
            _stock_add(store, "settlement", sid, "grain", -grab, tick)
            store.con.execute("UPDATE settlement SET order_value=MAX(0.0,order_value-0.02), "
                              "updated_tick=? WHERE id=?", (tick, sid))
            cx.emit(tick, "hoard_grab", f"有人囤物居奇，被夺走粮 {grab}",
                    effects={"grain_lost": grab}, place_kind="settlement", place_id=sid,
                    actor_kind="character")


def _finance(store, cx, ids, tick, seed, day):
    """M034 票据借贷与债务契约。"""
    if not module_on(store, "M034"):
        return
    sid = _sid(ids)
    if chance(seed, 0.25, "M034", day):
        _thread(store, "M034", "promissory", "settlement", sid, "faction", _fid(ids), tick,
                meta='{"instrument":"note"}')
        cx.emit(tick, "promissory_note", "立下一纸票据为凭，钱债两清有据",
                effects={}, place_kind="settlement", place_id=sid, actor_kind="settlement")


# ============================================================
# 调度入口
# ============================================================
def settle_annual(store, cx, ids, tick, seed, day):
    """年度级题材机制。"""
    ensure_genre_schema(store)
    _xuan_bonds(store, cx, ids, tick, seed, day)
    _omens(store, cx, ids, tick, seed, day)
    _xuan_cultivation(store, cx, ids, tick, seed, day)
    _xuan_beasts(store, cx, ids, tick, seed, day)
    _covert(store, cx, ids, tick, seed, day)
    _cyber(store, cx, ids, tick, seed, day)
    _lishi_gov(store, cx, ids, tick, seed, day)


def settle_quarter(store, cx, ids, tick, seed, day):
    """季度级题材机制。"""
    ensure_genre_schema(store)
    _battle_support(store, cx, ids, tick, seed, day)
    _inquiry(store, cx, ids, tick, seed, day)
    _intel_ops(store, cx, ids, tick, seed, day)
    _dushi(store, cx, ids, tick, seed, day)
    _lishi_daily(store, cx, ids, tick, seed, day)
    _disaster(store, cx, ids, tick, seed, day)
    _finance(store, cx, ids, tick, seed, day)
