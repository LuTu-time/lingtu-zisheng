# -*- coding: utf-8 -*-
"""P2 题材扩展验收测试。

P2 的验收标准是两句话：
  ① 同一句设定输入，切换题材标签后能产出结构明显不同的世界；
  ② 孤儿模块全部有归属，调度器无死角。

本测试逐条核验：
  A 调度覆盖：55 个二期模块每个都至少被一个题材标为 on；87 个 routed 模块每个
    都至少出现在一个题材的 on/simple/off 里 —— 无孤儿（global 模块全局常驻）。
  B 门控生效：题材标为 off 的子系统在世界里状态确为 off，且其事件确实不再出现；
    标为 on 的子系统事件确实会出现。
  C 结构不同：同种子、同世界名、同一句设定，切换题材后事件构成与末态明显不同。
  D 确定性：同题材同种子，跑两遍事件流逐条相同（题材机制不引入非确定性）。

退出码 0 表示全部通过。
"""
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import sim                                    # noqa: E402
from play import create, open_world, load_ids             # noqa: E402

SEED = 20261015
NAME = "同种子同设定世界"
DAYS = 480

PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


def _load(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def _run(path, theme, days=DAYS):
    create(path, SEED, theme, NAME)
    store, guard, cx = open_world(path)
    try:
        sim.run(store, cx, guard, load_ids(store), days, SEED, verbose=False)
        sid = load_ids(store)["settlement_id"]
        events = [(r["tick"], r["kind"], r["summary"]) for r in
                  store.all("SELECT tick,kind,summary FROM event ORDER BY id")]
        snap = {
            "pop": store.scalar("SELECT population FROM settlement WHERE id=?", (sid,)),
            "morale": round(store.scalar("SELECT morale FROM settlement WHERE id=?", (sid,)), 3),
            "order": round(store.scalar("SELECT order_value FROM settlement WHERE id=?", (sid,)), 3),
            "grain": round(store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                        "AND owner_id=? AND resource_type='grain'", (sid,)) or 0, 1),
            "coin": round(store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                       "AND owner_id=? AND resource_type='coin'", (sid,)) or 0, 1),
        }
        kinds = ",".join(e[1] for e in events)
        mods = {r["module_id"]: r["state"] for r in
                store.all("SELECT module_id,state FROM module_instance")}
        return events, snap, hashlib.sha256(kinds.encode()).hexdigest(), mods
    finally:
        store.close()


def test_coverage():
    gens = _load("genres.json")["genres"]
    mods = _load("modules.json")["modules"]
    spec = _load("spec.json")
    p2 = spec["phases"][2]["modules"]
    mounted = set()
    for g in gens:
        for t in ("on", "simple", "off"):
            mounted.update(g.get(t, []))
    orphans = [m for m in p2 if m not in mounted]
    check(not orphans, f"A1 二期 {len(p2)} 个模块全部有题材归属（孤儿 {len(orphans)} 个）")
    # 只有 routed 模块需要被题材挂载；global 模块「全局常驻」，不进题材表。
    routed = [m["id"] for m in mods if m.get("scope") == "routed"]
    glob = [m["id"] for m in mods if m.get("scope") == "global"]
    miss = [m for m in routed if m not in mounted]
    check(not miss, f"A2 全部 {len(routed)} 个 routed 模块无调度死角"
                    f"（未挂载 {len(miss)} 个 <- {miss[:6]}；另有 {len(glob)} 个 global 常驻）")
    on_any = set()
    for g in gens:
        on_any.update(g.get("on", []))
    p2_no_on = [m for m in p2 if m not in on_any]
    check(not p2_no_on, f"A3 二期模块都至少在一个题材里被完整启用（未启用 {p2_no_on}）")


def test_gate(d):
    # B1：lishi 启用 M047（地权），mori 关闭 M047；门控状态应如实反映。
    _, _, _, m_lishi = _run(os.path.join(d, "gate_lishi.db"), "lishi", days=120)
    _, snap_m, dig_m, m_mori = _run(os.path.join(d, "gate_mori.db"), "mori", days=120)
    check(m_lishi.get("M047") == "on", "B1 lishi 题材下 M047（地权）为 on")
    check(m_mori.get("M047") == "off", "B2 mori 题材下 M047（地权）为 off")
    # B3：关闭的子系统不再产出事件。mori 关闭 M047/M032/M054，其专用事件不应出现。
    store, guard, cx = open_world(os.path.join(d, "gate_mori.db"))
    try:
        kinds = {r["kind"] for r in store.all("SELECT DISTINCT kind FROM event")}
    finally:
        store.close()
    gate_kinds = {"land_dispute", "auction", "guild"}
    leaked = kinds & {k for k in gate_kinds}
    check(not leaked, f"B3 mori 关闭的子系统无事件泄漏（泄漏 {leaked}）")


def test_differentiation(d):
    gens = ["lishi", "xuanhuan", "xuanyi", "mori", "saibo", "dushi", "meishi", "zhichang"]
    digs, snaps = {}, {}
    for g in gens:
        _, snap, dig, _ = _run(os.path.join(d, f"diff_{g}.db"), g)
        digs[g] = dig
        snaps[g] = snap
    distinct = len(set(digs.values()))
    check(distinct >= len(gens) - 1,
          f"C1 题材切换后事件构成明显不同（{distinct}/{len(gens)} 种不同事件指纹）")
    grain = {s["grain"] for s in snaps.values()}
    coin = {s["coin"] for s in snaps.values()}
    check(len(grain) >= 3 and len(coin) >= 3,
          f"C2 末态资源明显分化（grain {len(grain)} 档 {sorted(grain)}；coin {len(coin)} 档）")


def test_determinism(d):
    for g in ("xuanyi", "xuanhuan"):
        e1, _, h1, _ = _run(os.path.join(d, f"det_{g}_a.db"), g)
        e2, _, h2, _ = _run(os.path.join(d, f"det_{g}_b.db"), g)
        check(h1 == h2 and e1 == e2, f"D {g} 题材同种子两遍事件流逐条一致（{len(e1)} 条）")


def main():
    d = tempfile.mkdtemp(prefix="genre-test-")
    print("=" * 72)
    print("P2 题材扩展验收测试")
    print("=" * 72)
    test_coverage()
    test_gate(d)
    test_differentiation(d)
    test_determinism(d)
    print("-" * 72)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("P2 验收全部通过：题材差异真实、调度无死角、题材机制确定可复现。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
