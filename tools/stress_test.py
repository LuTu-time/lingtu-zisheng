# -*- coding: utf-8 -*-
"""
多种子压力测试：验证引擎的鲁棒性。

单一种子跑通不算数 —— 必须确认「换一批世界，规则依然成立」。
每个种子独立建库、独立推演，最后汇总成一张表。

用法: python tools/stress_test.py [--days 600] [--seeds 8]
"""
import argparse
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import worldgen, scheduler, sim        # noqa: E402
from engine.base import Calendar                   # noqa: E402
from engine.store import Store                     # noqa: E402
from engine.guardrails import Guardrail            # noqa: E402
from engine.causality import Causality             # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCHEMA = open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf-8").read()


def run_one(seed, days, theme):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.commit()
        con.close()

        store = Store(path)
        ids = worldgen.generate(store, seed, theme, log=lambda m: None, verbose=False)
        scheduler.schedule(store, theme)
        guard = Guardrail(store)
        cx = Causality(store, guard)
        guard.sync_horizon()
        st = sim.run(store, cx, guard, ids, days, seed, log=lambda m: None, verbose=False)
        checks = sim.verdict(store, st, ids)

        # 每个世界都做一次历史核验：链完整、锚点一致、防线真的拦得住
        chain_ok, _, _ = cx.verify_chain()
        anc = cx.verify_anchor()
        hist_ok = chain_ok and anc["ok"]
        guarded = False
        try:
            store.con.execute("UPDATE event SET summary='X' WHERE id=1")
        except sqlite3.Error:
            guarded = True
        store.con.rollback()

        sid = ids["settlement_id"]
        pop = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
        morale = store.scalar("SELECT morale FROM settlement WHERE id=?", (sid,))
        grain = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                             "AND owner_id=? AND resource_type='grain'", (sid,))
        forest = store.scalar("SELECT forest FROM region WHERE id=?", (ids["region_id"],))
        game = store.scalar("SELECT game_density FROM region WHERE id=?", (ids["region_id"],))
        n_ev = store.scalar("SELECT COUNT(*) FROM event")
        depth_max = store.scalar("SELECT MAX(depth) FROM event") or 0
        rep = guard.report()
        left = store.scalar("SELECT COUNT(*) FROM character WHERE status='left'")
        dead = store.scalar("SELECT COUNT(*) FROM character WHERE status='dead'")
        store.close()

        return {
            "seed": seed, "pop": pop, "morale": morale, "grain": grain,
            "forest": forest, "game": game, "events": n_ev, "depth": depth_max,
            "blocked": rep["blocked_total"], "left": left, "dead": dead,
            "hist_ok": hist_ok, "guarded": guarded,
            "ok": all(o for _, o in checks) and hist_ok and guarded,
        }
    finally:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=600)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--theme", default="lishi")
    ap.add_argument("--base", type=int, default=20261015)
    a = ap.parse_args()

    print("=" * 84)
    print(f"多种子压力测试　推演 {a.days} 日（{a.days / 120:.1f} 年）　题材 {a.theme}")
    print("=" * 84)
    print(f"{'seed':>12} {'人口':>5} {'民心':>6} {'存粮':>7} {'林木':>5} {'猎物':>5} "
          f"{'事件':>5} {'深':>3} {'拦截':>5} {'迁出':>4} {'亡故':>4} {'历史':>5} {'防线':>5}  判定")
    print("-" * 92)

    results = []
    for i in range(a.seeds):
        seed = a.base + i * 7919
        r = run_one(seed, a.days, a.theme)
        results.append(r)
        print(f"{r['seed']:>12} {r['pop']:>5} {r['morale']:>6.2f} {r['grain']:>7.0f} "
              f"{r['forest']:>5.2f} {r['game']:>5.2f} {r['events']:>5} {r['depth']:>3} "
              f"{r['blocked']:>5} {r['left']:>4} {r['dead']:>4} "
              f"{'完好' if r['hist_ok'] else '损坏':>5} "
              f"{'在' if r['guarded'] else '失守':>5}  "
              f"{'✔ 通过' if r['ok'] else '✘ 未通过'}")

    n_ok = sum(1 for r in results if r["ok"])
    pops = [r["pop"] for r in results]
    mor = [r["morale"] for r in results]
    print("-" * 84)
    print(f"通过 {n_ok}/{len(results)}　|　人口 {min(pops)}~{max(pops)}"
          f"（均值 {sum(pops) / len(pops):.1f}）　|　"
          f"民心 {min(mor):.2f}~{max(mor):.2f}　|　"
          f"存活率 {n_ok / len(results) * 100:.0f}%")
    print(f"护栏总拦截 {sum(r['blocked'] for r in results)} 次　|　"
          f"共产生事件 {sum(r['events'] for r in results)} 条")
    print("=" * 84)
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
