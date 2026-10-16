# -*- coding: utf-8 -*-
"""M045 聚落营造（住房动态增长）验收测试。

要点：住房上限（settlement.housing）原本开局固定，是「人口撞上承载力」的马尔萨斯
陷阱的直接成因。本子系统让人满且木料/存粮宽裕时村里盖新房，使上限可增长。
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from play import create, open_world, load_ids        # noqa: E402
from engine import infra, sim                        # noqa: E402

SEED = 20261016
PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


def _set(store, sid, housing, population, morale, wood, grain):
    store.con.execute("UPDATE settlement SET housing=?, population=?, morale=? WHERE id=?",
                      (housing, population, morale, sid))
    for resource, amount in (("wood", wood), ("grain", grain)):
        store.con.execute("UPDATE stock SET amount=? WHERE owner_kind='settlement' "
                          "AND owner_id=? AND resource_type=?", (amount, sid, resource))
    store.commit()


def _wood_grain(store, sid):
    return (store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                         "AND resource_type='wood'", (sid,)),
            store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                         "AND resource_type='grain'", (sid,)))


def _events(store, kind):
    return store.scalar("SELECT COUNT(*) FROM event WHERE kind=?", (kind,))


def main():
    d = tempfile.mkdtemp(prefix="housing-test-")
    db = os.path.join(d, "world.db")
    create(db, SEED, "lishi", "营造世界")
    store, guard, cx = open_world(db)
    original_rate = infra.HOUSE_BASE
    try:
        ids = load_ids(store)
        sid = ids["settlement_id"]
        infra.HOUSE_BASE = 1.0   # 测试期把动工概率拉满，去掉随机性

        # A 未满员：不兴土木
        _set(store, sid, housing=40, population=19, morale=0.9, wood=500, grain=500)
        check(infra.settle_housing(store, cx, ids, 0, SEED, 120) is None
              and _events(store, "house_built") == 0, "A 住房宽裕时不动工")

        # B 满员但木料不足：不兴土木
        _set(store, sid, housing=19, population=19, morale=0.9, wood=1, grain=500)
        check(infra.settle_housing(store, cx, ids, 0, SEED, 120) is None
              and _events(store, "house_built") == 0, "B 木料不足时不动工")

        # C 满员但民心过低：不兴土木
        _set(store, sid, housing=19, population=19, morale=0.20, wood=500, grain=500)
        check(infra.settle_housing(store, cx, ids, 0, SEED, 120) is None
              and _events(store, "house_built") == 0, "C 民心过低时不动工")

        # D 满员 + 木料存粮宽裕 + 民心尚可：动工，并付出代价
        _set(store, sid, housing=19, population=19, morale=0.80, wood=500, grain=500)
        wood0, grain0 = _wood_grain(store, sid)
        new_housing = infra.settle_housing(store, cx, ids, 0, SEED, 120)
        store.commit()
        wood1, grain1 = _wood_grain(store, sid)
        check(new_housing == 19 + infra.HOUSES_PER_BUILD
              and store.scalar("SELECT housing FROM settlement WHERE id=?", (sid,)) == new_housing,
              f"D1 满员时动工，住房上限 {19} → {new_housing}")
        check(abs((wood0 - wood1) - infra.HOUSE_WOOD) < 1e-6
              and abs((grain0 - grain1) - infra.HOUSE_GRAIN) < 1e-6,
              f"D2 动工消耗木料 {infra.HOUSE_WOOD}、存粮 {infra.HOUSE_GRAIN}")
        row = store.one("SELECT tick, effects FROM event WHERE kind='house_built' ORDER BY id DESC LIMIT 1")
        check(row is not None and f'"housing": [19, {new_housing}]' in row["effects"].replace("  ", " "),
              "D3 事件记录住房上限的前后值（可回溯）")

        # E 事件不可改写
        ev = store.scalar("SELECT id FROM event WHERE kind='house_built' ORDER BY id DESC LIMIT 1")
        try:
            store.con.execute("UPDATE event SET summary='篡改' WHERE id=?", (ev,))
            check(False, "E 营造事件必须不可改写")
        except sqlite3.IntegrityError:
            store.con.rollback()
            check(True, "E 营造事件不可改写（与推演历史同权）")

        # F 端到端：住房被压到人口之下，跑过年界后应自行加盖
        store.close()
        db2 = os.path.join(d, "e2e.db")
        create(db2, SEED, "lishi", "营造端到端")
        store, guard, cx = open_world(db2)
        ids = load_ids(store)
        sid = ids["settlement_id"]
        _set(store, sid, housing=5, population=19, morale=0.95, wood=800, grain=5000)
        sim.run(store, cx, guard, load_ids(store), 240, SEED, verbose=False)
        built = _events(store, "house_built")
        housing = store.scalar("SELECT housing FROM settlement WHERE id=?", (sid,))
        check(built >= 1 and housing > 5,
              f"F 端到端：满员推演 240 日后自发加盖（{built} 次，上限 5 → {housing}）")
    finally:
        infra.HOUSE_BASE = original_rate
        store.close()

    print("-" * 64)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("聚落营造验收通过：住房上限可在约束下增长，代价与事件都可回溯。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
