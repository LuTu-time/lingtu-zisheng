# -*- coding: utf-8 -*-
"""
势力博弈与人伦网络（P1 社会骨架）：

  M011 口述秘史传承　　M014 资源据点争夺与攻防拉锯
  M015 悬赏追杀与庇护逃亡　M016 江湖与行业潜规则
  M018 监狱囚禁与赎身　　M026 记忆档案封存与解禁
  M032 藏品流转与拍卖　　M038 媒体舆论与流言传播
  M039 势力声望争夺与舆论造势　M040 圈子派系与小团体社交
  M043 作品名望与口碑演化　M044 门派宗门公司组织架构演化

设计纪律（与既有内核一致）：
  1. 全部随机来自 (seed, 维度, day)，day 取绝对日，不依赖内存计数或调用序。
  2. 状态变更一律经不可变事件落库；新建表用 ensure_power_schema 按需补装（旧库非破坏升级）。
  3. relation 写入先判重或用 INSERT OR IGNORE，绝不触发 uq_relation 唯一约束。
"""
from .base import clamp, chance, unit, pick, Calendar

# ---------- 概率与阈值 ----------
ORAL_BASE = 0.18          # M011 口述秘史（每年）
CONTEST_BASE = 0.16       # M014 据点争夺（每年）
BOUNTY_BASE = 0.05        # M015 悬赏追杀（每季）
UNDERWORLD_BASE = 0.06    # M016 江湖潜规则（每季）
PRISON_BASE = 0.07        # M018 入狱（每季）
PRISON_TERM = 90          # 刑期（日）
SEAL_BASE = 0.14          # M026 记忆封存/解禁（每年）
AUCTION_BASE = 0.06       # M032 藏品流转（每季）
RUMOR_BASE = 0.10         # M038 流言（每旬，传播快）
REPUTE_CAMP_BASE = 0.08   # M039 声望造势（每季）
CLIQUE_BASE = 0.05        # M040 圈子派系（每季）
MASTERWORK_BASE = 0.07    # M043 作品名望（每季）
ORG_BASE = 0.30           # M044 组织演化（每年）

ORAL_TOPICS = ["先人开荒的旧事", "早年大水的传说", "祖辈与山里野兽的恩怨",
               "村边古树的来历", "某户人家失传的手艺", "旧年饥荒里分粮的情义"]
COLLECTIBLE_NAMES = ["青铜香炉", "祖传玉佩", "雕花银锁", "旧年兵器", "异域琉璃盏",
                     "褪色的族谱卷", "山中所获奇石"]
REPUTE_DOMAINS = ["铁匠手艺", "木作", "织造", "猎术", "炊事", "医术"]


# ---------- 运行时表 ----------
POWER_DDL = """
CREATE TABLE IF NOT EXISTS prison (
    id INTEGER PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES character(id),
    reason TEXT NOT NULL DEFAULT '',
    jailed_tick INTEGER NOT NULL,
    release_tick INTEGER NOT NULL,
    ransom_coin REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('serving','released')),
    updated_tick INTEGER NOT NULL DEFAULT 0
);
-- 仅约束「同一角色同时在服多刑」；允许多次入狱的历史 released 记录共存，
-- 否则第二次刑满将 status 改回 'released' 会与上一次 released 撞 UNIQUE(character_id, status)。
CREATE UNIQUE INDEX IF NOT EXISTS uq_prison_serving ON prison(character_id) WHERE status='serving';
CREATE INDEX IF NOT EXISTS idx_prison_status ON prison(status, release_tick);

CREATE TABLE IF NOT EXISTS collectible (
    id INTEGER PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('settlement','faction','character')),
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    value_coin REAL NOT NULL DEFAULT 0,
    created_tick INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    updated_tick INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_collectible_owner ON collectible(owner_kind, owner_id);

CREATE TABLE IF NOT EXISTS repute (
    id INTEGER PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('character','faction')),
    owner_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.5,
    updated_tick INTEGER NOT NULL,
    UNIQUE(owner_kind, owner_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_repute_owner ON repute(owner_kind, owner_id);
"""


def ensure_power_schema(store):
    store.con.executescript(POWER_DDL)
    # 非破坏性升级：早期 world 误带了 UNIQUE(character_id, status)，会阻断角色二次刑满。
    # 检测到该约束则按新结构重建表并保留数据。
    try:
        idxs = store.con.execute("PRAGMA index_list(prison)").fetchall()
        has_bad = any(r[2] == "u" for r in idxs)
    except Exception:
        has_bad = False
    if has_bad:
        store.con.executescript("""
            CREATE TABLE prison_new (
                id INTEGER PRIMARY KEY,
                character_id INTEGER NOT NULL REFERENCES character(id),
                reason TEXT NOT NULL DEFAULT '',
                jailed_tick INTEGER NOT NULL,
                release_tick INTEGER NOT NULL,
                ransom_coin REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('serving','released')),
                updated_tick INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO prison_new
                (id,character_id,reason,jailed_tick,release_tick,ransom_coin,status,updated_tick)
                SELECT id,character_id,reason,jailed_tick,release_tick,ransom_coin,status,updated_tick
                FROM prison;
            DROP TABLE prison;
            ALTER TABLE prison_new RENAME TO prison;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_prison_serving
                ON prison(character_id) WHERE status='serving';
            CREATE INDEX IF NOT EXISTS idx_prison_status ON prison(status, release_tick);
        """)
        store.commit()


# ---------- 通用：角色抽样 ----------
def _adults(store, sid, exclude=()):
    return store.all(
        "SELECT id, name, age, occupation FROM character "
        "WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿','村长') ORDER BY id", (sid,))


def _has_relation(store, a, b, kind):
    return store.one(
        "SELECT 1 FROM relation WHERE from_kind='character' AND from_id=? "
        "AND to_kind='character' AND to_id=? AND kind=?", (a, b, kind))


# ---------- M011 口述秘史传承 ----------
def settle_oral_history(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, ORAL_BASE, "oral", day):
        return
    elders = store.all(
        "SELECT id, name, age FROM character WHERE settlement_id=? AND status='alive' "
        "AND age>=50 AND occupation IN ('老弱','农人','猎人','工匠','牧人') ORDER BY id", (sid,))
    heirs = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND age BETWEEN 14 AND 35 ORDER BY id", (sid,))
    if not elders or not heirs:
        return
    elder = elders[int(unit(seed, "oral_e", day) * len(elders))]
    heir = heirs[int(unit(seed, "oral_h", day) * len(heirs))]
    topic = pick(seed, ORAL_TOPICS, "oral_t", day)
    content = f"{elder['name']}向{heir['name']}讲述{topic}"
    # 避免完全重复的口述记忆无限堆积：同一继承人同主题仅记一次
    if store.one("SELECT 1 FROM memory WHERE owner_id=? AND content=?", (heir["id"], content)):
        return
    store.insert("memory", {
        "owner_id": heir["id"], "content": content,
        "importance": round(0.6 + unit(seed, "oral_i", day) * 0.3, 2),
        "tick": tick, "event_id": None, "decay": 0.985})
    cx.emit(tick, "oral_history", content,
            effects={"topic": topic, "heir": heir["name"]},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=elder["id"],
            participants=[{"kind": "character", "id": elder["id"]},
                          {"kind": "character", "id": heir["id"]}])


# ---------- M014 资源据点争夺与攻防拉锯 ----------
def settle_territory_contest(store, cx, ids, tick, seed, day):
    sid, rid = ids["settlement_id"], ids["region_id"]
    fid, bid = ids["villager_fid"], ids["beast_fid"]
    r = store.one("SELECT owner_faction, forest, game_density FROM region WHERE id=?", (rid,))
    if not r:
        return
    if not chance(seed, CONTEST_BASE, "contest", day):
        return
    s = store.one("SELECT cohesion, martial FROM faction WHERE id=?", (fid,))
    # 村民凝聚力越高越守得住；山林越荒，野兽越想压下来
    hold = (s["cohesion"] if s else 0.6) + 0.2 * (1.0 - r["forest"])
    push = 0.5 + 0.3 * r["game_density"]
    won = unit(seed, "contest_r", day) < hold / (hold + push)
    if won:
        if r["owner_faction"] != fid:
            store.con.execute("UPDATE region SET owner_faction=?, updated_tick=? WHERE id=?",
                              (fid, tick, rid))
        cx.emit(tick, "territory_contest",
                "村民合力守住山口据点，把野兽挡在林线之外",
                effects={"result": "hold", "owner": fid},
                place_kind="region", place_id=rid, actor_kind="faction", actor_id=fid)
        if s:
            store.update("faction", fid,
                         {"cohesion": round(clamp(s["cohesion"] + 0.02, 0.05, 0.98), 4),
                          "updated_tick": tick})
    else:
        cx.emit(tick, "territory_contest",
                "野兽趁着林密下山，占了靠河的一处猎场，村民退守村口",
                effects={"result": "lost", "owner": bid},
                place_kind="region", place_id=rid, actor_kind="faction", actor_id=bid)
        if s:
            store.update("faction", fid,
                         {"cohesion": round(clamp(s["cohesion"] - 0.02, 0.05, 0.98), 4),
                          "updated_tick": tick})


# ---------- M015 悬赏追杀与庇护逃亡 ----------
def settle_bounty(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, BOUNTY_BASE, "bounty", day):
        return
    targets = _adults(store, sid)
    if not targets:
        return
    t = targets[int(unit(seed, "bou_t", day) * len(targets))]
    outcome = pick(seed, ["追逃", "被擒", "受庇护"], "bou_o", day)
    living = store.scalar(
        "SELECT COUNT(*) FROM character WHERE settlement_id=? AND status='alive'", (sid,))
    if outcome == "追逃" and living and living > 8:
        store.update("character", t["id"], {"status": "left", "updated_tick": tick})
        cx.emit(tick, "bounty",
                f"{t['name']}被人悬赏追拿，连夜逃出村去，再没回来",
                effects={"outcome": "fled"},
                place_kind="settlement", place_id=sid, actor_kind="character",
                actor_id=t["id"],
                participants=[{"kind": "character", "id": t["id"]}])
    elif outcome == "被擒":
        cx.emit(tick, "bounty",
                f"{t['name']}被缉拿归案，押入村中看管",
                effects={"outcome": "caught"},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    else:
        helper = targets[int(unit(seed, "bou_h", day) * len(targets))]
        if helper["id"] != t["id"] and not _has_relation(store, helper["id"], t["id"], "恩"):
            store.con.execute(
                "INSERT OR IGNORE INTO relation "
                "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
                "VALUES ('character', ?, 'character', ?, '恩', ?, ?, ?, ?)",
                (helper["id"], t["id"], 0.6, 0.4, "冒风险庇护被追拿的人", tick))
        cx.emit(tick, "bounty",
                f"{t['name']}遭人追拿，{helper['name']}暗中收留庇护了他",
                effects={"outcome": "sheltered", "helper": helper["name"]},
                place_kind="settlement", place_id=sid, actor_kind="character",
                actor_id=helper["id"],
                participants=[{"kind": "character", "id": t["id"]},
                              {"kind": "character", "id": helper["id"]}])


# ---------- M016 江湖与行业潜规则 ----------
def settle_underworld(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, UNDERWORLD_BASE, "under", day):
        return
    pros = store.all(
        "SELECT id, name, occupation FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation IN ('工匠','猎人','牧人','农人') ORDER BY id", (sid,))
    if len(pros) < 2:
        return
    a = pros[int(unit(seed, "und_a", day) * len(pros))]
    b = pros[int(unit(seed, "und_b", day) * len(pros))]
    if a["id"] == b["id"]:
        return
    rule = pick(seed, ["同行不拆台", "见货先讲暗号", "欠的工钱隔年清",
                       "外乡人来的头一单要抽成", "手艺传儿不传女"], "und_r", day)
    kind = "恩" if unit(seed, "und_k", day) < 0.5 else "忌"
    if not _has_relation(store, a["id"], b["id"], kind):
        store.con.execute(
            "INSERT OR IGNORE INTO relation "
            "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
            "VALUES ('character', ?, 'character', ?, ?, ?, ?, ?, ?)",
            (a["id"], b["id"], kind, 0.4, 0.25, f"行业潜规则：{rule}", tick))
    cx.emit(tick, "underworld_rule",
            f"{a['name']}与{b['name']}心照不宣地恪守行里的规矩：{rule}",
            effects={"rule": rule, "kind": kind},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])


# ---------- M018 监狱囚禁与赎身 ----------
def settle_prison(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    ensure_power_schema(store)
    # 先处理到期释放
    due = store.all(
        "SELECT id, character_id, ransom_coin FROM prison "
        "WHERE status='serving' AND release_tick<=? ORDER BY id", (tick,))
    for p in due:
        store.con.execute("UPDATE prison SET status='released', updated_tick=? WHERE id=?",
                          (tick, p["id"]))
        name = store.scalar("SELECT name FROM character WHERE id=?", (p["character_id"],))
        cx.emit(tick, "prison_release",
                f"{name}刑满，从村中看管处放了回来",
                effects={"prison_id": p["id"]},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    # 再处理赎身：已服刑过半且村中有余钱可赎
    mid = store.all(
        "SELECT id, character_id, ransom_coin FROM prison "
        "WHERE status='serving' AND ransom_coin>0 AND release_tick>? ORDER BY id",
        (tick,))
    for p in mid:
        if not chance(seed, 0.4, "ransom", p["id"], day):
            continue
        coin = store.one("SELECT id, amount FROM stock WHERE owner_kind='settlement' "
                         "AND owner_id=? AND resource_type='coin'", (sid,))
        if not coin or coin["amount"] < p["ransom_coin"]:
            continue
        store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                          (p["ransom_coin"], tick, coin["id"]))
        store.con.execute("UPDATE prison SET status='released', updated_tick=? WHERE id=?",
                          (tick, p["id"]))
        nm = store.scalar("SELECT name FROM character WHERE id=?", (p["character_id"],))
        cx.emit(tick, "ransom",
                f"有人凑出 {p['ransom_coin']:.0f} 文，将{nm}赎了出来",
                cause=None, effects={"ransom_coin": p["ransom_coin"], "prison_id": p["id"]},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
    # 新入狱：低概率从在村者中抽签，且村中无人正在服刑
    if store.scalar("SELECT COUNT(*) FROM prison WHERE status='serving'") >= 2:
        return
    if not chance(seed, PRISON_BASE, "imprison", day):
        return
    candidates = _adults(store, sid)
    if not candidates:
        return
    c = candidates[int(unit(seed, "pri_c", day) * len(candidates))]
    if store.one("SELECT 1 FROM prison WHERE character_id=? AND status='serving'", (c["id"],)):
        return
    ransom = round(20 + unit(seed, "pri_r", day) * 60, 0)
    store.con.execute(
        "INSERT OR IGNORE INTO prison(character_id, reason, jailed_tick, release_tick, "
        "ransom_coin, status) VALUES(?,?,?,?,?,'serving')",
        (c["id"], pick(seed, ["斗殴伤人", "偷盗", "抗徭", "欠债不还"], "pri_rsn", day),
         tick, tick + PRISON_TERM * 24, ransom))
    cx.emit(tick, "imprison",
            f"{c['name']}因事获罪，被收在村中看管，待满 {PRISON_TERM} 日或有人赎身",
            effects={"ransom_coin": ransom, "term_days": PRISON_TERM},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M026 记忆档案封存与解禁 ----------
def settle_memory_seal(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, SEAL_BASE, "seal", day):
        return
    mem = store.all(
        "SELECT id, owner_id, content, decay FROM memory "
        "WHERE tick>=0 ORDER BY id", ())
    if not mem:
        return
    m = mem[int(unit(seed, "seal_m", day) * len(mem))]
    if m["decay"] > 0.5:
        # 封存：停衰减（视为被郑重记档，不再随岁月淡去）
        store.con.execute("UPDATE memory SET decay=0 WHERE id=?", (m["id"],))
        cx.emit(tick, "memory_seal", f"一段往事被郑重封档，不再随岁月淡去：{m['content']}",
                effects={"memory_id": m["id"], "action": "seal"},
                place_kind="settlement", place_id=sid, actor_kind="character",
                actor_id=m["owner_id"])
    else:
        store.con.execute("UPDATE memory SET decay=0.985 WHERE id=?", (m["id"],))
        cx.emit(tick, "memory_seal", f"封存旧档被启封，重又提起：{m['content']}",
                effects={"memory_id": m["id"], "action": "unseal"},
                place_kind="settlement", place_id=sid, actor_kind="character",
                actor_id=m["owner_id"])


# ---------- M032 藏品流转与拍卖 ----------
def settle_auction(store, cx, ids, tick, seed, day):
    sid, fid = ids["settlement_id"], ids["villager_fid"]
    ensure_power_schema(store)
    if not chance(seed, AUCTION_BASE, "auction", day):
        return
    existing = store.all(
        "SELECT id, owner_kind, owner_id, name, value_coin FROM collectible ORDER BY id")
    # 尚未有藏品时，先由村中现出一件祖传之物
    if not existing:
        name = pick(seed, COLLECTIBLE_NAMES, "col_n", day)
        value = round(40 + unit(seed, "col_v", day) * 120, 0)
        store.insert("collectible", {
            "owner_kind": "settlement", "owner_id": sid, "name": name,
            "value_coin": value, "created_tick": tick, "note": "村中公产"})
        cx.emit(tick, "auction", f"村中清点出一件公产：{name}（估值 {value:.0f} 文）",
                effects={"name": name, "value_coin": value},
                place_kind="settlement", place_id=sid, actor_kind="settlement")
        return
    # 已有藏品：低概率转手（settlement<->faction），伴随文钱结算
    item = existing[int(unit(seed, "auc_i", day) * len(existing))]
    target = "faction" if item["owner_kind"] == "settlement" else "settlement"
    target_id = fid if target == "faction" else sid
    coin = store.one("SELECT id, amount FROM stock WHERE owner_kind=? AND owner_id=? "
                     "AND resource_type='coin'", (target, target_id))
    if not coin or coin["amount"] < item["value_coin"]:
        return
    price = item["value_coin"]
    store.con.execute("UPDATE collectible SET owner_kind=?, owner_id=?, updated_tick=? WHERE id=?",
                      (target, target_id, tick, item["id"]))
    from_coin = store.one("SELECT id, amount FROM stock WHERE owner_kind=? AND owner_id=? "
                          "AND resource_type='coin'",
                          (item["owner_kind"], item["owner_id"]))
    if from_coin:
        store.con.execute("UPDATE stock SET amount=amount+?, updated_tick=? WHERE id=?",
                          (price, tick, from_coin["id"]))
    store.con.execute("UPDATE stock SET amount=amount-?, updated_tick=? WHERE id=?",
                      (price, tick, coin["id"]))
    cx.emit(tick, "auction",
            f"{item['name']}在村中与村民势力之间转手，作价 {price:.0f} 文",
            effects={"name": item["name"], "value_coin": price, "to": target},
            place_kind="settlement", place_id=sid, actor_kind="settlement")


# ---------- M038 媒体舆论与流言传播 ----------
def settle_rumor(store, cx, ids, tick, seed, day):
    sid = s_id = ids["settlement_id"]
    if not chance(seed, RUMOR_BASE, "rumor", day):
        return
    people = store.all(
        "SELECT id, name FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation!='幼儿' ORDER BY id", (sid,))
    if len(people) < 2:
        return
    a = people[int(unit(seed, "rum_a", day) * len(people))]
    b = people[int(unit(seed, "rum_b", day) * len(people))]
    if a["id"] == b["id"]:
        return
    topic = pick(seed, ["某户藏粮", "谁家要搬走", "村口来了生人", "今年赋税要加",
                        "山那边不太平", "谁与谁结了怨"], "rum_t", day)
    # 流言微调两人关系（公开度更高，众人皆知）
    if not _has_relation(store, a["id"], b["id"], "忌"):
        store.con.execute(
            "INSERT OR IGNORE INTO relation "
            "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
            "VALUES ('character', ?, 'character', ?, '忌', ?, ?, ?, ?)",
            (a["id"], b["id"], 0.3, 0.9, f"流言：{topic}", tick))
    else:
        store.con.execute(
            "UPDATE relation SET intensity=MIN(1.0, intensity+0.05), updated_tick=? "
            "WHERE from_kind='character' AND from_id=? AND to_kind='character' "
            "AND to_id=? AND kind='忌'", (tick, a["id"], b["id"]))
    cx.emit(tick, "rumor",
            f"村里都在传：{topic}——{a['name']}与{b['name']}因此生了嫌隙",
            effects={"topic": topic},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])


# ---------- M039 势力声望争夺与舆论造势 ----------
def settle_repute_campaign(store, cx, ids, tick, seed, day):
    fid = ids["villager_fid"]
    sid = ids["settlement_id"]
    if not chance(seed, REPUTE_CAMP_BASE, "rep_camp", day):
        return
    f = store.one("SELECT cohesion, martial FROM faction WHERE id=?", (fid,))
    s = store.one("SELECT morale, order_value FROM settlement WHERE id=?", (sid,))
    if not f or not s:
        return
    up = unit(seed, "rep_dir", day) < 0.6
    delta = 0.03 if up else -0.02
    store.update("faction", fid,
                 {"cohesion": round(clamp(f["cohesion"] + delta, 0.05, 0.98), 4),
                  "updated_tick": tick})
    store.update("settlement", sid,
                 {"morale": round(clamp(s["morale"] + (0.02 if up else -0.01), 0.02, 0.98), 4),
                  "updated_tick": tick})
    cx.emit(tick, "repute_campaign",
            ("村民推举头面人物四处奔走，为村子挣了好名声" if up else
             "邻村风评有损，村中声望略挫"),
            effects={"direction": "up" if up else "down"},
            place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=fid)


# ---------- M040 圈子派系与小团体社交 ----------
def settle_clique(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    if not chance(seed, CLIQUE_BASE, "clique", day):
        return
    people = _adults(store, sid)
    if len(people) < 3:
        return
    n = min(3, len(people))
    idxs = sorted(set(int(unit(seed, f"cli_{k}", day) * len(people)) for k in range(n)))
    while len(idxs) < 2:
        idxs.append((idxs[-1] + 1) % len(people))
    members = [people[i] for i in idxs[:3]]
    bond = pick(seed, ["结为一伙", "拜了把子", "约定互相照拂", "同拜一师"], "cli_b", day)
    for x in range(len(members)):
        for y in range(x + 1, len(members)):
            if not _has_relation(store, members[x]["id"], members[y]["id"], "恩"):
                store.con.execute(
                    "INSERT OR IGNORE INTO relation "
                    "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
                    "VALUES ('character', ?, 'character', ?, '恩', ?, ?, ?, ?)",
                    (members[x]["id"], members[y]["id"], 0.45, 0.7,
                     f"同伙：{bond}", tick))
    cx.emit(tick, "clique",
            f"{'、'.join(m['name'] for m in members)} {bond}，成了一个小圈子",
            effects={"bond": bond, "members": [m["name"] for m in members]},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=members[0]["id"],
            participants=[{"kind": "character", "id": m["id"]} for m in members])


# ---------- M043 作品名望与口碑演化 ----------
def settle_masterwork(store, cx, ids, tick, seed, day):
    sid = ids["settlement_id"]
    ensure_power_schema(store)
    if not chance(seed, MASTERWORK_BASE, "master", day):
        return
    crafters = store.all(
        "SELECT id, name, occupation FROM character WHERE settlement_id=? AND status='alive' "
        "AND occupation IN ('工匠','猎人','牧人','农人') ORDER BY id", (sid,))
    if not crafters:
        return
    c = crafters[int(unit(seed, "mas_c", day) * len(crafters))]
    domain = pick(seed, REPUTE_DOMAINS, "mas_d", day)
    row = store.one("SELECT id, score FROM repute WHERE owner_kind='character' "
                    "AND owner_id=? AND domain=?", (c["id"], domain))
    if row:
        new_score = round(clamp(row["score"] + (0.08 if unit(seed, "mas_u", day) < 0.6 else -0.05),
                               0.1, 1.0), 3)
        store.con.execute("UPDATE repute SET score=?, updated_tick=? WHERE id=?",
                          (new_score, tick, row["id"]))
    else:
        new_score = round(0.5 + unit(seed, "mas_s", day) * 0.2, 3)
        store.insert("repute", {"owner_kind": "character", "owner_id": c["id"],
                                "domain": domain, "score": new_score, "updated_tick": tick})
    cx.emit(tick, "masterwork",
            (f"{c['name']}的{domain}出了名，四邻都来请" if new_score >= 0.55 else
             f"{c['name']}的{domain}出了岔子，口碑掉了些"),
            effects={"domain": domain, "score": new_score, "craftsman": c["name"]},
            place_kind="settlement", place_id=sid, actor_kind="character", actor_id=c["id"])


# ---------- M044 门派宗门公司组织架构演化 ----------
def settle_org_evolution(store, cx, ids, tick, seed, day):
    fid = ids["villager_fid"]
    sid = ids["settlement_id"]
    if not chance(seed, ORG_BASE, "org", day):
        return
    f = store.one("SELECT cohesion, territory, manpower, martial FROM faction WHERE id=?", (fid,))
    s = store.one("SELECT morale FROM settlement WHERE id=?", (sid,))
    if not f or not s:
        return
    # 组织随民心与凝聚力缓慢演化：民心高则扩员、凝聚力升；反之收缩
    d_coh = round(clamp((s["morale"] - 0.6) * 0.04, -0.03, 0.05), 4)
    d_ter = 1 if unit(seed, "org_t", day) < 0.3 + (s["morale"] - 0.5) else 0
    new_coh = round(clamp(f["cohesion"] + d_coh, 0.05, 0.98), 4)
    new_ter = max(1, f["territory"] + d_ter)
    store.update("faction", fid, {"cohesion": new_coh, "territory": new_ter, "updated_tick": tick})
    cx.emit(tick, "org_evolution",
            ("村中事务有了章法，组织愈发紧密，辖地也拓了些" if d_coh >= 0 else
             "村中人心松散，旧有的组织几近涣散"),
            effects={"cohesion": new_coh, "territory": new_ter},
            place_kind="settlement", place_id=sid, actor_kind="faction", actor_id=fid)
