# -*- coding: utf-8 -*-
"""状态锚定（state sealing）验收测试。

把可变状态表（stock/region/character…）也纳入不可篡改的历史见证：
  · 封印（seal）把当前状态指纹写成一条不可改写事件；
  · 核验（verify）重算指纹并与最近一次封印比对，精确指出漂移了哪几张表；
  · 绕过事件直接改状态表（哈希链不会因此断裂）也能被查出；
  · 快照同时记录状态指纹，便于分支比对。
"""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from play import create, open_world, load_ids        # noqa: E402
from engine import projection, branches, sim          # noqa: E402

SEED = 20261016
PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


def main():
    d = tempfile.mkdtemp(prefix="state-test-")
    db = os.path.join(d, "world.db")
    create(db, SEED, "lishi", "封印世界")
    store, guard, cx = open_world(db)
    try:
        # 1 封印
        r = projection.seal_state(store, cx, "创世后")
        check(r["event_id"] and len(r["digest"]) == 64 and len(r["tables"]) >= 8,
              f"封印写入不可改写事件，覆盖 {len(r['tables'])} 张状态表")
        row = store.one("SELECT kind,actor_kind,cause_event_id FROM event WHERE id=?", (r["event_id"],))
        check(row["kind"] == "state_seal" and row["actor_kind"] == "author" and row["cause_event_id"] is None,
              "封印=作者为根的事件，进入哈希链")

        # 2 核验一致
        v = projection.verify_state(store, cx)
        check(v["sealed"] and v["match"] and v["drifted"] == [], "封印后核验：状态逐表一致")
        check(v.get("chain_ok") and v.get("anchor_ok"), "封印后哈希链与锚点仍自洽")

        # 3 封印事件不可改写
        try:
            store.con.execute("UPDATE event SET summary='篡改' WHERE id=?", (r["event_id"],))
            check(False, "封印事件必须不可改写")
        except sqlite3.IntegrityError:
            check(True, "封印事件不可改写（与推演历史同权）")

        # 4 绕过事件篡改状态表 → 哈希链不断，但封印能查出并定位
        chain_before = cx.verify_chain()[0]
        store.con.execute("UPDATE stock SET amount=amount+777 WHERE resource_type='grain'")
        store.commit()
        check(cx.verify_chain()[0] is True and chain_before is True,
              "直接改 stock 不会让事件哈希链断裂（这正是需要状态封印的原因）")
        v2 = projection.verify_state(store, cx)
        check((not v2["match"]) and "stock" in v2["drifted"], f"封印能查出状态篡改并定位到表：{v2['drifted']}")

        # 5 合法推演后再封印 → 重新一致
        sim.run(store, cx, guard, load_ids(store), 20, SEED, verbose=False)
        r2 = projection.seal_state(store, cx, "推演 20 日后")
        v3 = projection.verify_state(store, cx)
        check(v3["match"] and v3["seal_event_id"] == r2["event_id"], "合法变更后再次封印：重新一致")
        check(chain_before and cx.verify_chain()[0] and cx.verify_anchor()["ok"],
              "多轮封印后历史链与锚点仍完好")

        # 6 非法标签被拒
        try:
            projection.seal_state(store, cx, "x" * 200)
            check(False, "超长封印标签必须被拒")
        except ValueError:
            check(True, "超长封印标签被拒")

        # 6b 状态存档点：可精确重建，且受不可改写事件见证
        at = projection.state_at(store)
        check(bool(at.get("checkpoint")) and at["verified"] and at["attested_by_event"] is True,
              "存档点可精确重建，且与不可改写事件里的指纹一致")
        old_cp = store.one("SELECT id,tick FROM state_checkpoint ORDER BY id LIMIT 1")
        sim.run(store, cx, guard, load_ids(store), 15, SEED, verbose=False)
        s = projection.since(store, old_cp["id"])
        check(s["events_since"] >= 1 and "stock" in s["drifted"],
              f"自存档点以来的漂移可定位：{s['drifted'][:6]}…")
        a_old = projection.state_at(store, old_cp["id"])
        check(a_old["verified"] and a_old["tick"] == old_cp["tick"],
              "可只读重建到更早的存档点")

        # 7 快照携带状态指纹
        snap = branches.create_snapshot(db, "带指纹快照")
        check(len(snap.get("state_digest", "")) == 64, "快照记录状态指纹")
        listed = [s for s in branches.list_snapshots(db) if s["id"] == snap["id"]]
        check(listed and listed[0]["state_digest"] == snap["state_digest"],
              "快照列表回读状态指纹一致")
    finally:
        store.close()

    print("-" * 64)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("状态锚定验收通过：状态表篡改可检测、可定位，且不破坏既有历史链。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
