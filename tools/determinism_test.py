# -*- coding: utf-8 -*-
"""
确定性回归测试。

引擎立下的第一条承诺是：**同一个种子必然复现同一个世界。**
这条承诺是历史验证的地基 —— 如果过去不能被重放，那"历史未被改写"就无从证明。

但这个承诺曾经是假的。2026-10-15 的压力测试连跑三次得到 991 / 994 / 1004 条事件，
追下去发现 migration 那条查询里藏了一个 SQL 的 `RANDOM()`：
它绕过了 base.py 那套 (seed, 维度, 序号) 的哈希派生，每次跑都挑中不同的人离乡。

所以需要这个测试。它做两件事：

  1. **同进程重复** —— 同一进程内把一个种子跑两遍，逐条比对事件流与末态。
  2. **跨进程重复** —— 用两个不同的 PYTHONHASHSEED 子进程各跑一遍。
     这一项能揪出「靠 Python 字符串 hash / set 迭代顺序」的不确定性，
     那类问题在同进程内是看不见的。

用法:
  python tools/determinism_test.py                    # 默认 12 个种子
  python tools/determinism_test.py --seeds 40 --days 600
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import worldgen, scheduler, sim        # noqa: E402
from engine.store import Store                     # noqa: E402
from engine.guardrails import Guardrail            # noqa: E402
from engine.causality import Causality             # noqa: E402

SCHEMA = open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf-8").read()

FINGERPRINT_SQL = """
SELECT id, tick, kind, place_kind, IFNULL(place_id,-1), actor_kind, IFNULL(actor_id,-1),
       cause_event_id, depth, summary, hash
FROM event ORDER BY id
"""


def run_world(seed, days, theme):
    """跑一个世界，返回（事件流指纹，末态指纹）。"""
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
        sim.run(store, cx, guard, ids, days, seed, log=lambda m: None, verbose=False)
        store.commit()

        events = tuple(tuple(r) for r in store.all(FINGERPRINT_SQL))
        sid = ids["settlement_id"]
        s = store.one("SELECT population, morale, order_value FROM settlement WHERE id=?", (sid,))
        r = store.one("SELECT forest, game_density FROM region WHERE id=?", (ids["region_id"],))
        grain = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                             "AND owner_id=? AND resource_type='grain'", (sid,))
        chars = tuple(tuple(x) for x in store.all(
            "SELECT id, name, status, age FROM character ORDER BY id"))
        state = (round(s["population"], 6), round(s["morale"], 6),
                 round(s["order_value"], 6), round(r["forest"], 9),
                 round(r["game_density"], 9), round(grain or 0, 6), chars)
        store.close()
        return events, state
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)
            except OSError:
                pass


def fingerprint_in_subprocess(seed, days, theme, hashseed):
    """在子进程里跑一遍，回传一个整体指纹。PYTHONHASHSEED 不同则字符串 hash 不同。"""
    code = (
        "import sys,hashlib;"
        f"sys.path.insert(0,r'{ROOT}');"
        "sys.argv=['determinism_test'];"
        "import tools.determinism_test as D;"
        f"e,s=D.run_world({seed},{days},'{theme}');"
        "h=hashlib.sha256(repr((e,s)).encode()).hexdigest();"
        "print(h)"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hashseed)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, cwd=ROOT)
    if out.returncode != 0:
        return None, out.stderr.strip()[-300:]
    return out.stdout.strip(), None


def main():
    ap = argparse.ArgumentParser(description="确定性回归测试")
    ap.add_argument("--days", type=int, default=600)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--theme", default="lishi")
    ap.add_argument("--base", type=int, default=20261015)
    ap.add_argument("--cross-process", action="store_true", default=True)
    ap.add_argument("--no-cross-process", dest="cross_process", action="store_false")
    a = ap.parse_args()

    seeds = [a.base + i * 7919 for i in range(a.seeds)]

    print("=" * 76)
    print(f"确定性回归测试　{len(seeds)} 个种子 × {a.days} 日　题材 {a.theme}")
    print("=" * 76)
    print(f"{'seed':>12} {'事件':>5} {'同进程':>7} {'跨进程':>7}   说明")
    print("-" * 76)

    all_ok = True
    n_bad = 0
    for seed in seeds:
        e1, s1 = run_world(seed, a.days, a.theme)
        e2, s2 = run_world(seed, a.days, a.theme)
        same_in = (e1 == e2) and (s1 == s2)

        note = ""
        if not same_in:
            n = min(len(e1), len(e2))
            pos = next((i for i in range(n) if e1[i] != e2[i]), n)
            note = f"同进程第 {pos} 条起不同（{len(e1)} vs {len(e2)}）"

        same_cross, err = True, ""
        if a.cross_process:
            h1, err1 = fingerprint_in_subprocess(seed, a.days, a.theme, 0)
            h2, err2 = fingerprint_in_subprocess(seed, a.days, a.theme, 12345)
            if err1 or err2:
                same_cross, note = False, (err1 or err2)
            else:
                same_cross = (h1 == h2)
                if not same_cross:
                    note = "跨 PYTHONHASHSEED 结果不同 —— 存在依赖字符串 hash 的逻辑"

        ok = same_in and same_cross
        all_ok = all_ok and ok
        n_bad += 0 if ok else 1
        print(f"{seed:>12} {len(e1):>5} "
              f"{'✔ 一致' if same_in else '✘ 不同':>7} "
              f"{'✔ 一致' if same_cross else '✘ 不同':>7}   "
              f"{note or '同一世界，逐条相同'}")

    print("-" * 76)
    if all_ok:
        print(f"通过 {len(seeds)}/{len(seeds)}　—— 同种子必然复现同一个世界。")
        print("这正是「历史可被重放」的前提，也是工具 verify_history.py 的立论基础。")
    else:
        print(f"通过 {len(seeds) - n_bad}/{len(seeds)}　—— {n_bad} 个种子不确定。")
        print("确定性是引擎的第一承诺，必须先修复再看别的。")
    print("=" * 76)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
