# -*- coding: utf-8 -*-
"""
历史完整性验证器。

回答一个问题：**这个世界的过去，有没有被动过手脚？**

四项检查，逐层收紧：

  1. 防线在位   数据库里的拒绝改写触发器是否还在？拿真语句撞一下，看它拦不拦。
  2. 哈希链完整 从创世到今天，逐条重算指纹，与存档比对。
  3. 确定性重放 用同一个种子从头再跑一遍，两条历史必须逐字相同。
                （这一条最硬 —— 它不依赖任何自证，而是让世界自己复述一遍）
  4. 篡改演练   故意破坏一份副本：删触发器、改事件、删事件。
                验证「即使有人绕过第一道防线，第二道照样抓得住」。

用法:
  python tools/verify_history.py                 # 全跑（重放用当前库的种子/题材/天数）
  python tools/verify_history.py --no-replay     # 跳过重放（快）
  python tools/verify_history.py --html out.html # 另外输出一份可视化报告
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import worldgen, scheduler, sim          # noqa: E402
from engine.store import Store, HistoryRewriteAttempt  # noqa: E402
from engine.guardrails import Guardrail              # noqa: E402
from engine.causality import Causality               # noqa: E402

DB = os.path.join(ROOT, "db", "world.db")
SQL = os.path.join(ROOT, "schema", "schema.sql")
ANCHOR_FILE = os.path.join(ROOT, "out", "history.anchor")

CMP_FIELDS = ("tick", "kind", "place_kind", "place_id", "actor_kind", "actor_id",
              "cause_event_id", "depth", "summary", "hash")


# ---------------------------------------------------------------- 检查 1
def check_guards():
    """
    触发器是否在位，且真的会拦。

    用**真实的行**去试探（碰不存在的 id 时 SQLite 不会触发 BEFORE 触发器，
    那样探测不到东西）。整个试探包在手工事务里，无论成败最后一律 ROLLBACK，
    真实数据不会被碰到。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    triggers = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_event_no_%'")]
    row = con.execute("SELECT id FROM event ORDER BY id LIMIT 1").fetchone()
    real_id = row["id"] if row else -1

    con.isolation_level = None                      # 手工控制事务
    probe = []
    for op, sql in (("UPDATE", "UPDATE event SET summary='X' WHERE id=?"),
                    ("DELETE", "DELETE FROM event WHERE id=?")):
        blocked, detail = False, ""
        con.execute("BEGIN")
        try:
            cur = con.execute(sql, (real_id,))
            if cur.rowcount == 0:
                detail = "（无匹配行，未触发检查）"
        except sqlite3.Error as e:
            blocked, detail = True, str(e)
        finally:
            try:
                con.execute("ROLLBACK")             # 无论结果如何，什么也没留下
            except sqlite3.Error:
                pass
        probe.append((op, blocked, detail))
    con.isolation_level = ""
    con.close()

    # 第二道防线：应用层守卫。就算有人绕开 SQL、直接调 Store 也不行。
    app_blocked, app_detail = False, ""
    st = Store(DB)
    try:
        st.update("event", real_id, {"summary": "X"})
        app_detail = "竟然放行"
    except HistoryRewriteAttempt as e:
        app_blocked, app_detail = True, str(e)
    try:
        st.delete("event", real_id)
    except HistoryRewriteAttempt:
        pass
    st.close()

    # G8：倒填日期。把一条「新事件」写到历史水位线之前，必须被拒。
    g = Guardrail(Store(DB))
    g.sync_horizon()
    backdate_blocked = not g.allow_append(0, "回溯事件")

    return triggers, probe, real_id, app_blocked, app_detail, backdate_blocked


# ---------------------------------------------------------------- 检查 2
def check_chain():
    store = Store(DB)
    cx = Causality(store, Guardrail(store))
    ok, brk, affected = cx.verify_chain()
    anc = cx.verify_anchor()
    n = store.scalar("SELECT COUNT(*) FROM event")
    head = store.scalar("SELECT hash FROM event ORDER BY id DESC LIMIT 1")
    ticks = store.one("SELECT MIN(tick) AS a, MAX(tick) AS b FROM event")
    # 顺带统计有多少条事件被引用为「因」，是链条真正的骨架
    roots = store.scalar("SELECT COUNT(*) FROM event WHERE cause_event_id IS NULL")
    # worldgen 之前可能没有世界行，兜底
    anchor_written = anc.get("claimed_count", 0) > 0 or anc.get("claimed_head", "")
    store.close()
    return {"ok": ok and anc["ok"], "chain_ok": ok, "anchor_ok": anc["ok"],
            "anchor": anc, "anchor_written": bool(anchor_written),
            "first_break": brk, "affected": affected, "n": n,
            "head": head, "t0": ticks["a"], "t1": ticks["b"], "roots": roots}


# ---------------------------------------------------------------- 检查 2b
def check_external_anchor():
    """
    外部锚：推演结束时另行抄录的一份「历史有多长、链尾是谁」。

    库里的锚点只能防「改了事件但忘了改锚点」。如果动手的人连锚点一并重算，
    库内就自洽了 —— 这时唯一的见证就是世界文件之外的那份抄本。
    """
    if not os.path.exists(ANCHOR_FILE):
        return {"present": False}
    rec = {}
    with open(ANCHOR_FILE, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\u3000", 1)
            if len(parts) == 2:
                rec[parts[0]] = parts[1]
    claimed_head = rec.get("链尾", "")
    digits = "".join(ch for ch in rec.get("事件", "0") if ch.isdigit())
    claimed_n = int(digits or 0)

    con = sqlite3.connect(DB)
    actual_n = con.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    actual_head = con.execute(
        "SELECT hash FROM event ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    actual_head = actual_head[0] if actual_head else ""
    return {"present": True, "path": ANCHOR_FILE, "record": rec,
            "claimed_count": claimed_n, "claimed_head": claimed_head,
            "actual_count": actual_n, "actual_head": actual_head,
            "ok": claimed_n == actual_n and claimed_head == actual_head}


# ---------------------------------------------------------------- 检查 3
def check_replay(seed=None, theme=None, days=None, quiet=True):
    """同种子从头再跑一遍，比对两条事件流。"""
    store0 = Store(DB)
    w = store0.one("SELECT * FROM world WHERE id=1")
    seed = seed if seed is not None else w["seed"]
    theme = theme if theme is not None else w["theme"]
    max_tick = store0.scalar("SELECT MAX(tick) FROM event", default=0) or 0
    days = days if days is not None else max(1, max_tick // 24)
    gold = [tuple(r[f] for f in CMP_FIELDS) for r in store0.all(
        f"SELECT {', '.join(CMP_FIELDS)} FROM event ORDER BY id")]
    store0.close()

    tmp = tempfile.mkdtemp(prefix="ltz-replay-")
    path = os.path.join(tmp, "replay.db")
    try:
        with open(SQL, encoding="utf-8") as f:
            sql = f.read()
        con = sqlite3.connect(path)
        con.executescript(sql)
        con.commit()
        con.close()

        st = Store(path)
        ids = worldgen.generate(st, seed, theme, w["name"],
                                log=lambda m: None, verbose=False)
        scheduler.schedule(st, theme)
        guard = Guardrail(st)
        cx = Causality(st, guard)
        guard.sync_horizon()
        sim.run(st, cx, guard, ids, days, seed, log=lambda m: None, verbose=False)
        got = [tuple(r[f] for f in CMP_FIELDS) for r in st.all(
            f"SELECT {', '.join(CMP_FIELDS)} FROM event ORDER BY id")]
        st.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    diff = []
    for i in range(max(len(gold), len(got))):
        a = gold[i] if i < len(gold) else None
        b = got[i] if i < len(got) else None
        if a != b:
            diff.append((i + 1, a, b))
    return {"ok": not diff, "seed": seed, "theme": theme, "days": days,
            "n_gold": len(gold), "n_got": len(got), "diff": diff[:5],
            "n_diff": len(diff)}


# ---------------------------------------------------------------- 检查 4
def _fresh_copy():
    """用 SQLite 自己的 backup 接口克隆一份，避免直接拷文件时漏掉 WAL 里的内容。"""
    tmp = tempfile.mkdtemp(prefix="ltz-tamper-")
    path = os.path.join(tmp, "tamper.db")
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()
    return tmp, path


def _strip_guards(path):
    """模拟「有人铁了心要改」：先把第一道防线拆了。"""
    con = sqlite3.connect(path)
    for (n,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_event_no_%'"):
        con.execute(f'DROP TRIGGER "{n}"')
    con.commit()
    con.close()


def check_tamper():
    """
    篡改演练：复制一份库，先删掉触发器，再动真实数据，看哈希链能不能抓出来。

    每一种手法用**一份全新的副本**，彼此不干扰 —— 否则第一次改动留下的
    断点会污染后面几次的判定。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id FROM event ORDER BY id").fetchall()
    caused = con.execute("SELECT id FROM event WHERE cause_event_id IS NOT NULL "
                         "ORDER BY id LIMIT 1").fetchone()
    con.close()
    if len(rows) < 4:
        return {"ok": False, "reason": "事件太少，无法演练", "cases": []}

    mid_id = rows[len(rows) // 2]["id"]
    last_id = rows[-1]["id"]

    cases = [
        ("改写中间某条事件的措辞（改内容）",
         "UPDATE event SET summary = summary || '（后被追记）' WHERE id = ?", (mid_id,)),
        ("删掉末尾一条事件（砍尾巴）",
         "DELETE FROM event WHERE id = ?", (last_id,)),
        ("倒填事件的时刻（改时间）",
         "UPDATE event SET tick = tick + 1 WHERE id = ?", (mid_id,)),
        ("往历史里塞一条假事件（无中生有）",
         "INSERT INTO event (tick, kind, summary) VALUES (999999, 'forged', '凭空捏造的事件')",
         ()),
    ]
    # 只有存在「有父节点」的事件时，斩断溯源才是个有意义的手法
    if caused:
        cases.append(("斩断某条事件的溯源（抹掉它的因）",
                      "UPDATE event SET cause_event_id = NULL WHERE id = ?", (caused["id"],)))

    results = []
    for label, sql, args in cases:
        tmp, path = _fresh_copy()
        try:
            _strip_guards(path)
            c = sqlite3.connect(path)
            before = c.execute("SELECT COUNT(*) FROM event").fetchone()[0]
            applied, detail = True, ""
            try:
                c.execute(sql, args)
                c.commit()
            except sqlite3.Error as e:
                applied, detail = False, str(e)
            c.close()

            st = Store(path)
            cx = Causality(st, Guardrail(st))
            chain_ok, brk, affected = cx.verify_chain()
            anc = cx.verify_anchor()
            n = st.scalar("SELECT COUNT(*) FROM event")
            st.close()
            detected = (not chain_ok) or (not anc["ok"])
            results.append({"label": label, "applied": applied, "detail": detail,
                            "detected": detected if applied else None,
                            "via_chain": not chain_ok, "via_anchor": not anc["ok"],
                            "first_break": brk, "affected": affected,
                            "anchor": anc, "delta": n - before})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    caught = sum(1 for r in results if r.get("applied") and r.get("detected"))
    return {"ok": caught == len(results) and caught > 0, "cases": results}


# ---------------------------------------------------------------- 输出
def hr(title):
    print("\n" + "─" * 66)
    print(f"  {title}")
    print("─" * 66)


def main():
    ap = argparse.ArgumentParser(description="历史完整性验证器")
    ap.add_argument("--no-replay", action="store_true", help="跳过确定性重放")
    ap.add_argument("--days", type=int, default=None, help="重放天数，默认取当前库的长度")
    ap.add_argument("--html", default=None, help="另存一份可视化报告")
    a = ap.parse_args()

    if not os.path.exists(DB):
        print(f"找不到数据库：{DB}\n先跑 python run_p0.py --reset")
        sys.exit(1)

    print("═" * 66)
    print("  灵途自生 · 历史完整性验证")
    print("═" * 66)
    print(f"  对象　{DB}")
    print(f"  校验　{time.strftime('%Y-%m-%d %H:%M:%S')}")

    all_ok = True

    # --- 1 ---
    hr("第 1 道：改写防线在位情况")
    triggers, probe, real_id, app_blocked, app_detail, backdate_blocked = check_guards()
    print(f"  数据库触发　{', '.join(triggers) if triggers else '（缺失！）'}")
    print(f"  试探对象　　拿真实事件 #{real_id} 去撞（全程包在事务里，最后回滚，不留痕迹）")
    for op, blocked, detail in probe:
        print(f"  {'✔' if blocked else '✘'}  {op} 撞库 → "
              + (f"被拒绝：{detail}" if blocked else f"竟然放行 —— {detail or '防线失效'}"))
        all_ok = all_ok and blocked
    print(f"  {'✔' if app_blocked else '✘'}  应用层守卫 → "
          + (f"绕过 SQL 直接调接口，同样被拒：{app_detail[:30]}"
             if app_blocked else "竟然放行 —— 第二道防线失效"))
    all_ok = all_ok and app_blocked
    print(f"  {'✔' if backdate_blocked else '✘'}  G8 倒填拦截 → "
          + ("把新事件写回历史水位线之前，被拒绝"
             if backdate_blocked else "竟然放行 —— 可以往过去塞事了"))
    all_ok = all_ok and backdate_blocked
    if len(triggers) < 2:
        all_ok = False
        print("  ✘ 触发器不全，数据库未受保护。重跑 tools/build_schema.py --init --force")

    # --- 2 ---
    hr("第 2 道：哈希链 + 历史锚点")
    ch = check_chain()
    print(f"  事件总数　{ch['n']} 条　（源头事件 {ch['roots']} 条，其余挂在因果链上）")
    print(f"  覆盖时段　tick {ch['t0']} → {ch['t1']}")
    print(f"  链尾指纹　{ch['head'][:40]}…")
    if ch["chain_ok"]:
        print(f"  ✔ 哈希链　逐条重算指纹，全部吻合 —— 从创世到今天，一个字都没被动过")
    else:
        b = ch["first_break"]
        print(f"  ✘ 哈希链　断于事件 #{b['id']}：{b['summary'][:36]}")
        print(f"     存档指纹 {str(b['stored'])[:20]}…  实算指纹 {b['expect'][:20]}…")
        print(f"     自该条起共 {ch['affected']} 条失配")
        all_ok = False

    anc = ch["anchor"]
    if not ch["anchor_written"]:
        print("  · 锚点　（尚未写入 —— 旧存档或首次推演前。跑一次 run_p0.py 即会写入）")
    elif ch["anchor_ok"]:
        print(f"  ✔ 锚点　声称 {anc['claimed_count']} 条 / 实际 {anc['actual_count']} 条，"
              f"链尾一致 —— 历史没有缺一截，也没有多出一截")
    else:
        print(f"  ✘ 锚点　声称 {anc['claimed_count']} 条 / 实际 {anc['actual_count']} 条"
              + ("（末尾被砍）" if anc["truncated"] else "")
              + ("（被塞入新事件）" if anc["appended"] else "")
              + ("（链尾指纹不符）" if anc["head_moved"] else ""))
        all_ok = False

    # --- 2b ---
    ext = check_external_anchor()
    if ext["present"]:
        if ext["ok"]:
            print(f"\n  ✔ 外部锚　{os.path.relpath(ext['path'], ROOT)} 与库中记录一致"
                  f"（{ext['actual_count']} 条 / {ext['actual_head'][:16]}…）")
        else:
            print(f"\n  ✘ 外部锚　{os.path.relpath(ext['path'], ROOT)} 与库中记录不符："
                  f"抄本 {ext['claimed_count']} 条，库中 {ext['actual_count']} 条")
            all_ok = False
    else:
        print("\n  · 外部锚　（尚无 out/history.anchor —— 跑一次 run_p0.py 会生成）")

    # --- 3 ---
    if not a.no_replay:
        hr("第 3 道：确定性重放（让世界自己复述一遍）")
        print("  同种子、同题材、同天数重跑一遍，逐条比对新旧两条历史……")
        t0 = time.time()
        rp = check_replay(days=a.days)
        cost = time.time() - t0
        print(f"  种子 {rp['seed']}　题材 {rp['theme']}　推演 {rp['days']} 日　耗时 {cost:.2f}s")
        if rp["ok"]:
            print(f"  ✔ 两条历史完全一致：{rp['n_gold']} 条事件逐字相同")
            print("     —— 过去是可以被复现的，这说明它从未被改动过")
        else:
            print(f"  ✘ 重放结果与存档不符，{rp['n_diff']} 处差异")
            for i, x, y in rp["diff"]:
                print(f"     第 {i} 条：存档 {str(x)[:40]}　重放 {str(y)[:40]}")
            all_ok = False

    # --- 4 ---
    hr("第 4 道：篡改演练（假定有人连防线都拆了）")
    print("  做法：复制一份库，先删掉触发器，再动真实数据。")
    tp = check_tamper()
    for c in tp["cases"]:
        if not c.get("applied"):
            print(f"  · {c['label']}")
            print(f"      → 操作直接被拒绝（{c['detail'][:44]}）")
            continue
        mark = "✔" if c.get("detected") else "✘"
        print(f"  {mark} {c['label']}")
        print(f"      → 操作成功了，但随即被抓住："
              + ("哈希链断裂" if c["via_chain"] else "")
              + ("／" if c["via_chain"] and c["via_anchor"] else "")
              + ("锚点对不上" if c["via_anchor"] else ""))
        if c["via_chain"] and c["first_break"]:
            print(f"        哈希链：断在 #{c['first_break']['id']}，波及 {c['affected']} 条")
        if c["via_anchor"]:
            a = c["anchor"]
            print(f"        锚点：声称 {a['claimed_count']} 条 / 实际 {a['actual_count']} 条"
                  + ("，末尾被砍" if a["truncated"] else "")
                  + ("，被塞入新事件" if a["appended"] else ""))
        all_ok = all_ok and c.get("detected")
    if tp["ok"]:
        print(f"  ✔ {len(tp['cases'])} 种篡改手法全部被检出"
              f" —— 拆掉第一道防线，第二道照样抓得住")
    else:
        all_ok = False

    # --- 结论 ---
    print("\n" + "═" * 66)
    if all_ok:
        print("  结论：这个世界的过去是可信的。")
        print("        事件一经写下即为事实，既改不掉（数据库拒绝），")
        print("        也瞒不住（哈希链与重放双重校验）。")
    else:
        print("  结论：存在未通过的检查项，见上方标记。")
    print("═" * 66)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
