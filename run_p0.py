# -*- coding: utf-8 -*-
"""
《灵途自生》P0 无画面推演原型 —— 命令行入口。

    python run_p0.py --reset --seed 20261015 --days 120

不需要任何画面、不依赖模型与网络。它只做一件事：
让一个世界在没有干预的情况下自己往前走，并如实记下发生了什么。
"""
import argparse
import os
import sqlite3
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine import worldgen, scheduler, sim, chronicle  # noqa: E402
from engine.base import Calendar                      # noqa: E402
from engine.store import Store                        # noqa: E402
from engine.guardrails import Guardrail               # noqa: E402
from engine.causality import Causality                # noqa: E402


def ensure_db(path, reset):
    if reset and os.path.exists(path):
        os.remove(path)
        for s in ("-wal", "-shm"):
            if os.path.exists(path + s):
                os.remove(path + s)
    if os.path.exists(path):
        return False
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf-8") as f:
        sql = f.read()
    con = sqlite3.connect(path)
    con.executescript(sql)
    con.commit()
    con.close()
    return True


def load_ids(store):
    return {
        "settlement_id": store.scalar("SELECT id FROM settlement LIMIT 1"),
        "region_id": store.scalar("SELECT id FROM region LIMIT 1"),
        "villager_fid": store.scalar("SELECT id FROM faction WHERE kind='村民' LIMIT 1"),
        "beast_fid": store.scalar("SELECT id FROM faction WHERE kind='野兽' LIMIT 1"),
    }


def main():
    ap = argparse.ArgumentParser(description="灵途自生 P0 推演原型")
    ap.add_argument("--seed", type=int, default=20261015, help="随机种子（同种子必然复现同一个世界）")
    ap.add_argument("--days", type=int, default=600, help="推演天数（默认 600 日 = 5 个游戏年）")
    ap.add_argument("--theme", default="lishi", help="题材标签 key，见 data/genres.json")
    ap.add_argument("--name", default=None, help="世界名")
    ap.add_argument("--db", default=os.path.join(ROOT, "db", "world.db"))
    ap.add_argument("--reset", action="store_true", help="清空并重建世界")
    ap.add_argument("--quiet", action="store_true", help="不打印逐旬进度")
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    fresh = ensure_db(a.db, a.reset)
    store = Store(a.db)

    if fresh or store.scalar("SELECT COUNT(*) FROM world") == 0:
        log("═" * 62)
        log("  灵途自生 · P0 世界生成")
        log("═" * 62)
        ids = worldgen.generate(store, a.seed, a.theme, a.name, log=log, verbose=not a.quiet)
        counts, label = scheduler.schedule(store, a.theme)
        log(f"  调度器 M094：题材「{label}」→ 完整启用 {counts['on']} · "
            f"简化运行 {counts['simple']} · 关闭 {counts['off']}")
    else:
        ids = load_ids(store)
        label = store.scalar("SELECT theme FROM world WHERE id=1")
        log(f"载入既有世界（{store.scalar('SELECT name FROM world WHERE id=1')}，"
            f"当前 tick={store.scalar('SELECT tick FROM world WHERE id=1')}）")

    guard = Guardrail(store, log=log)
    cx = Causality(store, guard, log=log)
    guard.sync_horizon()          # 续跑旧存档时，先恢复到「历史已经走到哪一天」

    log("")
    log("═" * 62)
    log(f"  无干预推演 {a.days} 个游戏日")
    log("═" * 62)

    t0 = time.time()
    st = sim.run(store, cx, guard, ids, a.days, a.seed, log=log, verbose=not a.quiet)
    cost = time.time() - t0

    # ---- 验收 ----
    checks = sim.verdict(store, st, ids)
    log("")
    log("═" * 62)
    log("  P0 验收判定")
    log("═" * 62)
    ok_all = True
    for name, ok in checks:
        log(f"  {'✔' if ok else '✘'}  {name}")
        ok_all = ok_all and ok

    # ---- 世界末态 ----
    sid = ids["settlement_id"]
    s = store.one("SELECT * FROM settlement WHERE id=?", (sid,))
    r = store.one("SELECT * FROM region WHERE id=?", (ids["region_id"],))
    grain = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                         "AND owner_id=? AND resource_type='grain'", (sid,))
    log("")
    log("═" * 62)
    log("  世界末态")
    log("═" * 62)
    log(f"  时刻        {Calendar.stamp(st.tick)}")
    log(f"  人口        {s['population']} 人（存活角色 "
        f"{store.scalar('SELECT COUNT(*) FROM character WHERE status=\'alive\'')}，"
        f"迁出 {store.scalar('SELECT COUNT(*) FROM character WHERE status=\'left\'')}，"
        f"亡故 {store.scalar('SELECT COUNT(*) FROM character WHERE status=\'dead\'')}）")
    log(f"  民心/秩序   {s['morale']:.2f} / {s['order_value']:.2f}")
    log(f"  存粮        {grain:.0f}")
    log(f"  林木/猎物   {r['forest']:.2f} / {r['game_density']:.2f}")
    log(f"  事件总数    {store.scalar('SELECT COUNT(*) FROM event')}")

    # ---- 历史完整性 ----
    chain_ok, brk, affected = cx.verify_chain()
    anc = cx.verify_anchor()
    head = anc["actual_head"] or "（空）"
    log("")
    log("  历史不可改写的核验")
    log(f"    事件条数  {anc['actual_count']}")
    log(f"    链尾指纹  {head[:32]}…")
    if chain_ok:
        log(f"    ✔ 哈希链  逐条重算吻合 —— 内容没被改过")
    else:
        log(f"    ✘ 哈希链  断于事件 #{brk['id']}（{brk['summary'][:24]}），受影响 {affected} 条")
        ok_all = False
    if anc["ok"]:
        log(f"    ✔ 锚点    条数与链尾一致 —— 没被砍掉一截，也没被塞进新东西")
    else:
        log(f"    ✘ 锚点    声称 {anc['claimed_count']} 条 / 实际 {anc['actual_count']} 条")
        ok_all = False
    log(f"    ↳ 想独立复核：python tools/verify_history.py")

    depth = cx.stats()
    log(f"  因果链深度  " + "　".join(f"第{k}层 {v} 个" for k, v in sorted(depth.items())))

    # ---- 因果链示例 ----
    row = store.one("SELECT id FROM event ORDER BY depth DESC, id DESC LIMIT 1")
    if row:
        log("")
        log("  最长因果链：")
        for i, e in enumerate(cx.chain(row["id"])):
            log(f"    {'└' if i else '●'} 〔{Calendar.stamp(e['tick'])}〕{e['summary']}")

    # ---- 护栏 ----
    rep = guard.report()
    log("")
    log(f"  护栏拦截    {rep['blocked_total']} 次  " +
        ("　".join(f"{k}×{v}" for k, v in sorted(rep["by_rule"].items())) or "（未触发）"))

    # ---- 报告 ----
    out = os.path.join(ROOT, "lingtu-zisheng-sim.html")
    chronicle.html_report(store, st, guard, cx, ids, out, a.seed, a.days, label)
    txt = os.path.join(ROOT, "out", "chronicle.txt")
    os.makedirs(os.path.dirname(txt), exist_ok=True)
    with open(txt, "w", encoding="utf-8") as f:
        f.write(chronicle.text_chronicle(store))

    # 外部锚：把「这段历史有多长、最后停在哪」记在世界文件之外。
    # 库里那份锚点能防「只改事件」，但防不住「连库里的锚点一起改」——
    # 把指纹另存一份（甚至抄在纸上），才谈得上真正的外部见证。
    # 外部锚必须跟着它见证的那个存档走：只有默认存档才写 out/history.anchor，
    # 否则写到该存档同目录 —— 免得用 --db 跑测试时覆盖了 db/world.db 的外部见证。
    anchor_path = os.path.join(ROOT, "out", "history.anchor")
    if os.path.abspath(a.db) != os.path.abspath(os.path.join(ROOT, "db", "world.db")):
        anchor_path = os.path.join(os.path.dirname(os.path.abspath(a.db)), "history.anchor")
    with open(anchor_path, "w", encoding="utf-8") as f:
        f.write(f"世界　{store.scalar('SELECT name FROM world WHERE id=1')}\n")
        f.write(f"种子　{a.seed}\n")
        f.write(f"题材　{label}\n")
        f.write(f"推演　{a.days} 日　至 tick {st.tick}\n")
        f.write(f"事件　{anc['actual_count']} 条\n")
        f.write(f"链尾　{head}\n")
        f.write(f"校验　{cx.verify_chain()[0] and anc['ok']}\n")

    log("")
    log(f"  耗时        {cost:.2f}s　|　种子 {a.seed}　|　推演 {a.days} 日")
    log(f"  纪事报告    {out}")
    log(f"  全文纪事    {txt}")
    log(f"  历史锚点    {anchor_path}")
    log("")
    log("  结论：" + ("世界在自己的规则里稳定运转，未崩坏、未死锁。"
                     if ok_all else "存在未通过的验收项，见上方标记。"))
    log("═" * 62)

    store.close()
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
