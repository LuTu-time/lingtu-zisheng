# -*- coding: utf-8 -*-
"""
角色生命周期（M104 生老病死）。

society.py 已承载了「出生 / 死亡 / 成年 / 外流」这一层最硬的逻辑 —— 这些直接决定
人口（人口恒等于存活角色数）。本模块在它之上补两层「让世界更像一个活的社会」的
生命周期细节，二者不冲突、不重复：

  · 老弱过渡：年迈劳力（≥56 岁）自然退出重劳力行列，转为「老弱」，以居家轻劳计。
    这是真实的代际更替 —— 也意味着壮年劳力会随世代自然递减。
  · 婚配羁绊：低概率由两名适婚成年人结成伴侣，写入有向「恩」关系，作为后续家族、
    继承、人口内增长（自然添丁）的人际基础。

确定性：所有判定只依赖 (seed, 维度, day)，不依赖内存态或调用次数；状态变更一律
经不可变事件 + 确定性落库，整跑与分段续跑逐条一致。
"""
from .base import clamp, chance, unit, pick, Calendar

ELDER_AGE = 56          # 转为「老弱」的年龄阈
MARRIAGE_BASE = 0.05    # 每季婚配基础概率
ELDER_TRANSITION_BASE = 0.5  # 达到阈值后每季过渡概率（避免同一天全体扎堆）


def _alive_adults(store, sid, exclude=()):
    return store.all(
        "SELECT id, name, age, occupation FROM character "
        "WHERE settlement_id=? AND status='alive' "
        "AND occupation NOT IN ('幼儿','村长','老弱') ORDER BY id", (sid,))


def _has_relation(store, a, b, kind):
    return store.one(
        "SELECT 1 FROM relation WHERE from_kind='character' AND from_id=? "
        "AND to_kind='character' AND to_id=? AND kind=?", (a, b, kind))


def settle_lifecycle(store, cx, ids, tick, seed, day):
    """
    M104：生命周期过渡。每季（d%30）结算一次。
      · 老弱过渡（只改 occupation，不触动人口计数）
      · 婚配羁绊（写入恩关系 + marriage 事件）
    """
    sid = ids["settlement_id"]

    # ---- 老弱过渡：年迈者退出重劳力 ----
    elders = store.all(
        "SELECT id, name, age, occupation FROM character "
        "WHERE settlement_id=? AND status='alive' AND age>=? "
        "AND occupation NOT IN ('老弱','幼儿','村长') ORDER BY id", (sid, ELDER_AGE))
    transitioned = 0
    for person in elders:
        if not chance(seed, ELDER_TRANSITION_BASE, "elder_t", person["id"], day):
            continue
        store.update("character", person["id"],
                     {"occupation": "老弱", "updated_tick": tick})
        transitioned += 1
    if transitioned:
        cx.emit(tick, "elder_infirm",
                f"村中有 {transitioned} 位老者放下重活，转由儿孙奉养",
                effects={"count": transitioned, "threshold": ELDER_AGE},
                place_kind="settlement", place_id=sid, actor_kind="settlement")

    # ---- 婚配羁绊 ----
    if not chance(seed, MARRIAGE_BASE, "marriage", day):
        return
    adults = _alive_adults(store, sid)
    if len(adults) < 2:
        return
    i = int(unit(seed, "mar_a", day) * len(adults))
    j = int(unit(seed, "mar_b", day) * len(adults))
    if i == j:
        return
    a, b = adults[i], adults[j]
    if _has_relation(store, a["id"], b["id"], "恩") or \
       _has_relation(store, b["id"], a["id"], "恩"):
        return
    store.con.execute(
        "INSERT OR IGNORE INTO relation "
        "(from_kind, from_id, to_kind, to_id, kind, intensity, publicity, note, updated_tick) "
        "VALUES ('character', ?, 'character', ?, '恩', ?, ?, ?, ?)",
        (a["id"], b["id"], round(unit(seed, "mar_v", day) * 0.4 + 0.5, 2),
         round(unit(seed, "mar_p", day), 2), "结为伴侣，共撑门户", tick))
    cx.emit(tick, "marriage",
            f"{a['name']}与{b['name']}结为伴侣，两家人并作一家",
            effects={"parties": [a["name"], b["name"]]},
            place_kind="settlement", place_id=sid, actor_kind="character",
            actor_id=a["id"],
            participants=[{"kind": "character", "id": a["id"]},
                          {"kind": "character", "id": b["id"]}])
