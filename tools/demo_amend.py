# -*- coding: utf-8 -*-
"""
勘误演示：写错了历史怎么办？

答案：**不回改，只追加。**

这个演示在一份**副本**上操作，不碰你的 db/world.db。

流程：
  1. 挑一条事件，假装它的措辞记错了
  2. 试着直接改它 —— 会被拒绝（第一道防线）
  3. 改用 Causality.amend() 追加一条 correction 事件
  4. 验证：原事件一字未动，哈希链与锚点依旧完整

用法: python tools/demo_amend.py [--event 39]
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.store import Store, HistoryRewriteAttempt  # noqa: E402
from engine.guardrails import Guardrail                # noqa: E402
from engine.causality import Causality                 # noqa: E402

DB = os.path.join(ROOT, "db", "world.db")


def main():
    ap = argparse.ArgumentParser(description="勘误机制演示")
    ap.add_argument("--event", type=int, default=39, help="要勘误的事件 id")
    a = ap.parse_args()

    if not os.path.exists(DB):
        print(f"找不到数据库：{DB}\n先跑 python run_p0.py --reset")
        sys.exit(1)

    tmp = tempfile.mkdtemp(prefix="ltz-amend-")
    path = os.path.join(tmp, "copy.db")
    src, dst = sqlite3.connect(DB), sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()

    try:
        store = Store(path)
        guard = Guardrail(store)
        cx = Causality(store, guard)
        guard.sync_horizon()

        target = a.event
        row = store.one("SELECT id, tick, kind, summary FROM event WHERE id = ?", (target,))
        if not row:
            print(f"事件 #{target} 不存在")
            sys.exit(1)

        print("═" * 66)
        print("  勘误演示：写错了历史怎么办")
        print("═" * 66)
        print(f"  原始记录　#{row['id']}〔tick {row['tick']}〕{row['kind']}")
        print(f"　　　　　　{row['summary']}")
        print(f"  假设　　　这条的措辞记错了 —— 想把它改掉。")

        before = cx.anchor()

        # ---- 第一步：试着直接改 ----
        print()
        print("─" * 66)
        print("  第一步：直接改（这是大多数人的第一反应）")
        print("─" * 66)
        try:
            store.update("event", target, {"summary": "（被改写过的措辞）"})
            print("  ✘ 竟然改成功了 —— 防线失效")
            sys.exit(1)
        except HistoryRewriteAttempt as e:
            print(f"  ✔ 被拒绝：{e}")

        # ---- 第二步：走勘误 ----
        print()
        print("─" * 66)
        print("  第二步：走勘误（追加一条，指向被更正的那条）")
        print("─" * 66)
        # 关键：勘误发生在**当下**，而不是插回被更正的那一天。
        # 往历史中段塞事件同样会被 G8 拒绝 —— 过去只有一个方向。
        now_tick = store.scalar("SELECT MAX(tick) FROM event", default=row["tick"])
        eid = cx.amend(
            target, now_tick,
            "该条措辞有误，需更正",
            correction="（此处填写更正后的说法）",
            actor_kind="author")
        store.commit()
        if eid is None:
            print(f"  ✘ 勘误也未通过护栏（suppressed={cx.suppressed}）")
            sys.exit(1)
        print(f"  ✔ 已追加勘误事件 #{eid}（kind=correction，写在当下 tick {now_tick}）")
        print(f"    注意：它是写在「今天」的，不是插回被更正的那一天 ——")
        print(f"    过去只有一个方向，连更正也不例外。")

        # ---- 验收 ----
        print()
        print("─" * 66)
        print("  验收")
        print("─" * 66)
        now = store.one("SELECT summary FROM event WHERE id = ?", (target,))
        same = now["summary"] == row["summary"]
        print(f"  {'✔' if same else '✘'} 原事件一字未动：{now['summary']}")

        ok, brk, affected = cx.verify_chain()
        print(f"  {'✔' if ok else '✘'} 哈希链完整"
              + ("" if ok else f"（断于 #{brk['id']}）"))

        anc = cx.verify_anchor()
        print(f"  {'✔' if anc['ok'] else '✘'} 长度锚点自洽："
              f"{before['count']} → {anc['actual_count']} 条"
              f"（追加是正当操作，锚点随之推进）")

        print()
        print("  历史现在的样子（因 → 果）：")
        for e in cx.chain(eid):
            print(f"    〔tick {e['tick']}〕{e['kind']}　{e['summary']}")
        print()
        print("═" * 66)
        print("  结论：错误留在历史里，更正也留在历史里。")
        print("        真实世界就是这样运转的 —— 报纸登更正启事，")
        print("        从不重印昨天的报纸。")
        print("═" * 66)
        store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
