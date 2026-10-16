# -*- coding: utf-8 -*-
"""P3 输出与交互层验收测试（引擎级，离线，不启服务、不需模型）。

spec 对 P3 的要求：*能把一段推演结果直接生成成可读的小说段落；作者可以注入事件、
修改状态、回滚到任意快照并开出新分支。*

platform_test.py 已用 HTTP 端到端覆盖同一批能力；本测试在引擎层快速复核，
便于在无端口/无模型环境里跑：

  M107 存档 / 快照 / 时间线分支：快照=只读校验备份；分支可独立推进，源存档不变；
       损坏分支被拒；非法 id 被拒。
  M106 作者干预 / 入世：入世角色不计入村民人口；拜访写入双方记忆；拨粮改变库存；
       离线时「交谈」优雅失败且世界不被改动；上帝面板可凭空注入自定义事件
       （作者为根、只追加不改写、与推演历史同权不可改写）。
  M105 叙事 / 草稿：事件→小说段落在有本地模型时生成（离线本测试断言 503 语义）；
       章节草稿可反复修订（版本号防并发覆盖）；生成草稿绝不改动世界记录。
"""
import os

# 强制离线：任何网络请求在发起前就被拒，行为与本地无模型时一致。
os.environ["LINGTU_LLM_URL"] = "https://example.invalid"

import json          # noqa: E402
import sqlite3       # noqa: E402
import sys           # noqa: E402
import tempfile      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from play import create, open_world, load_ids, intervene      # noqa: E402
from engine import branches, writing, player, sim, local_llm, intervention  # noqa: E402

SEED = 20261016

PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


def _dump(path):
    with sqlite3.connect(path) as con:
        return [tuple(r) for r in con.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")]


def test_m107_branch(d):
    db = os.path.join(d, "root.db")
    create(db, SEED, "lishi", "P3分支世界")
    snap = branches.create_snapshot(db, "创世快照")
    check(os.path.isfile(snap["path"]) and len(snap["sha256"]) == 64, "M107 快照=校验过的只读备份")
    listed = branches.list_snapshots(db)
    check(any(s["id"] == snap["id"] for s in listed), "M107 快照登记进 manifest")

    branch_path = branches.create_branch(db, snap["id"], "另一条时间线")
    check(os.path.isfile(branch_path), "M107 由快照开出独立分支")
    with sqlite3.connect(db) as a, sqlite3.connect(branch_path) as b:
        same = [tuple(r) for r in a.execute("SELECT tick,event_count,chain_head FROM world")] == \
               [tuple(r) for r in b.execute("SELECT tick,event_count,chain_head FROM world")]
    check(same, "M107 分支建立时与源存档逐字相同")

    before = _dump(db)
    store, guard, cx = open_world(branch_path)
    try:
        sim.run(store, cx, guard, load_ids(store), 3, SEED, verbose=False)
        branch_tick = store.scalar("SELECT tick FROM world WHERE id=1")
    finally:
        store.close()
    check(branch_tick == 72, "M107 分支可独立推进（推演 3 日 → tick 72）")
    with sqlite3.connect(db) as a:
        src_tick = a.execute("SELECT tick FROM world").fetchone()[0]
    check(src_tick == 0 and _dump(db) == before, "M107 分支推进不改动源存档（历史分叉而非篡改）")

    branch_id = branches.list_branches(db)[0]["id"]
    check(branches.branch_path(db, branch_id) == os.path.abspath(branch_path),
          "M107 已登记分支可解析并通过历史校验")
    # 损坏分支 → 拒绝
    with sqlite3.connect(branch_path) as con:
        con.execute("UPDATE world SET chain_head='bad' WHERE id=1")
    try:
        branches.branch_path(db, branch_id)
        check(False, "M107 损坏分支必须被拒")
    except ValueError:
        check(True, "M107 损坏分支被拒")
    # 非法 id → 拒绝
    try:
        branches.create_branch(db, "../../play.db", "非法")
        check(False, "M107 非法快照 id 被拒")
    except ValueError:
        check(True, "M107 非法快照 id 被拒")


def test_m106_player(d):
    db = os.path.join(d, "player.db")
    create(db, SEED, "lishi", "P3入世世界")
    store, guard, cx = open_world(db)
    try:
        player.ensure_schema(store)
        sid = load_ids(store)["settlement_id"]
        pop_before = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
        me = player.create(store, cx, {"name": "测试入世者", "occupation": "旅人",
                                       "age": 24, "goal": "寻访旧事"})
        check(me and me["name"] == "测试入世者" and me["status"] == "alive", "M106 建立入世角色")
        check(store.scalar("SELECT population FROM settlement WHERE id=?", (sid,)) == pop_before,
              "M106 入世角色不计入村民人口")
        check(store.scalar("SELECT COUNT(*) FROM event WHERE kind='player_arrival'") == 1,
              "M106 入世写入不可改写事件")
        try:
            player.create(store, cx, {"name": "第二人", "occupation": "旅人", "age": 30, "goal": "无"})
            check(False, "M106 二次入世必须被拒")
        except ValueError:
            check(True, "M106 二次入世被拒（历史不可重建）")

        # 拜访 → 双方记忆
        target = next(c for c in store.all("SELECT id,name FROM character WHERE settlement_id=? "
                                           "AND status='alive' AND id<>?", (sid, me["id"])))
        r = player.act(store, cx, {"action": "visit", "target_id": target["id"], "words": "你可见过旧碑？"})
        check(r["event_id"] and store.scalar("SELECT COUNT(*) FROM memory WHERE owner_id=?",
                                             (target["id"],)) >= 1, "M106 拜访写入事件与对方记忆")
        # 帮助 → 柴火 +1
        wood_before = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                   "AND owner_id=? AND resource_type='wood'", (sid,))
        player.act(store, cx, {"action": "help"})
        check(store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' AND owner_id=? "
                           "AND resource_type='wood'", (sid,)) == wood_before + 1,
              "M106 帮助村民增加薪柴")
        # 离线交谈 → 优雅失败，世界不变
        anchor_before = cx.anchor().copy()
        try:
            player.act(store, cx, {"action": "talk", "target_id": target["id"], "words": "近来可好？"})
            check(False, "M106 离线交谈应抛 ModelUnavailable")
        except local_llm.ModelUnavailable:
            check(cx.anchor() == anchor_before, "M106 离线交谈失败且世界未被改动")
        # 作者干预：拨粮改变库存
        grain_before = store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                                    "AND owner_id=? AND resource_type='grain'", (sid,))
        res = intervene(store, cx, "relief", 5)
        check(isinstance(res, int) and res > 0 and
              store.scalar("SELECT amount FROM stock WHERE owner_kind='settlement' "
                           "AND owner_id=? AND resource_type='grain'", (sid,)) == grain_before + 5,
              "M106 作者拨粮：事件 + 库存同步改变")
    finally:
        store.close()


def test_m106_inject(d):
    """M106 上帝面板：凭空注入自定义事件（只追加、不改写）。"""
    db = os.path.join(d, "inject.db")
    create(db, SEED, "lishi", "P3注入世界")
    store, guard, cx = open_world(db)
    try:
        before = store.scalar("SELECT COUNT(*) FROM event")
        res = intervention.inject_event(store, cx, {"summary": "一位游侠自山外而来，在村口打听旧碑"})
        row = store.one("SELECT id,kind,summary,actor_kind,cause_event_id,place_kind,visibility "
                        "FROM event WHERE id=?", (res["event_id"],))
        check(row and row["actor_kind"] == "author" and row["cause_event_id"] is None,
              "M106 注入事件=作者为根的因果链新节点")
        check(row["kind"] == "author_event" and row["place_kind"] == "settlement"
              and row["visibility"] == "public", "M106 默认类型/地点/可见性正确")
        check(store.scalar("SELECT COUNT(*) FROM event") == before + 1,
              "M106 注入事件追加而非替换历史")
        check(cx.verify_chain()[0] and cx.verify_anchor()["ok"], "M106 注入后哈希链与锚点仍然自洽")
        # 自定义类型 / 无地点 / 密写
        res2 = intervention.inject_event(store, cx, {"summary": "密室里藏着一封旧信", "kind": "rumor_note",
                                                     "place_kind": "none", "visibility": "secret"})
        row2 = store.one("SELECT kind,place_kind,place_id,visibility FROM event WHERE id=?", (res2["event_id"],))
        check(row2["kind"] == "rumor_note" and row2["place_kind"] == "none" and row2["place_id"] is None
              and row2["visibility"] == "secret", "M106 支持自定义类型/无地点/密写")
        # 注入的事件同样不可改写
        try:
            store.con.execute("UPDATE event SET summary='篡改' WHERE id=?", (res["event_id"],))
            check(False, "M106 注入的事件必须不可改写")
        except sqlite3.IntegrityError:
            check(True, "M106 注入的事件不可改写（与推演历史同权）")
        # 非法输入被拒，且世界不被改动
        mark = store.scalar("SELECT COUNT(*) FROM event")
        for bad in ({"summary": ""}, {"summary": "x", "kind": "Bad-Kind"},
                    {"summary": "x", "place_kind": "town"},
                    {"summary": "x", "place_kind": "settlement", "place_id": 999999},
                    {"summary": "x", "visibility": "everyone"},
                    {"summary": "x", "effects": "not-a-dict"}):
            try:
                intervention.inject_event(store, cx, bad)
                check(False, f"M106 非法注入必须被拒：{bad}")
            except ValueError:
                pass
        check(store.scalar("SELECT COUNT(*) FROM event") == mark, "M106 非法注入不产生任何事件")
    finally:
        store.close()


def test_m105_narrative(d):
    db = os.path.join(d, "writer.db")
    create(db, SEED, "lishi", "P3写作世界")
    store, guard, cx = open_world(db)
    try:
        writing.ensure_schema(store)
        eid = store.scalar("SELECT id FROM event ORDER BY id LIMIT 1")
        # 生成草稿：离线 → 503 语义，且绝不改动数据库
        before = _dump(db)
        try:
            writing.suggest_passage(store, {"event_ids": [eid], "direction": "侧重人物感受"})
            check(False, "M105 离线生成应抛 ModelUnavailable")
        except local_llm.ModelUnavailable:
            check(_dump(db) == before, "M105 叙事生成是只读的（草稿不落库、事件不改）")
        for refs in ([], [0], [999999], list(range(1, 10))):
            try:
                writing.suggest_passage(store, {"event_ids": refs})
                check(False, f"M105 非法事件引用 {refs} 应被拒")
            except ValueError:
                pass
        check(True, "M105 非法事件引用被拒")
        # 章节草稿可反复修订，事件不可改写
        events_before = store.scalar("SELECT COUNT(*) FROM event")
        ch = writing.save_chapter(store, {"title": "第一章", "body": "河谷起雾了。",
                                          "notes": "埋下伏笔", "event_ids": [eid]})
        check(ch["revision"] == 1, "M105 新建章节草稿 revision=1")
        ch2 = writing.save_chapter(store, {"id": ch["id"], "revision": 1, "title": "第一章 · 溪谷",
                                           "body": "河谷起雾了。有人离开了家。", "event_ids": [eid]})
        check(ch2["revision"] == 2, "M105 章节可反复修订（revision 递增）")
        try:
            writing.save_chapter(store, {"id": ch["id"], "revision": 1, "title": "旧窗口",
                                         "body": "不应覆盖", "event_ids": []})
            check(False, "M105 旧版本号必须被拒")
        except ValueError:
            check(True, "M105 旧版本号被拒（防并发覆盖）")
        check(store.scalar("SELECT COUNT(*) FROM event") == events_before,
              "M105 存草稿不产生/改写世界事件（草稿与事件分离）")
        # 导出全书：Markdown / 纯文本
        md = writing.manuscript(store, "md")
        check(md["filename"].endswith(".md") and "第一章" in md["text"] and "河谷起雾了" in md["text"],
              "M105 可导出全书稿（Markdown 含章节标题与正文）")
        txt = writing.manuscript(store, "txt")
        check(txt["filename"].endswith(".txt") and "第一章" in txt["text"]
              and store.scalar("SELECT COUNT(*) FROM event") == events_before,
              "M105 可导出纯文本稿，且导出只读不改事件")
        try:
            store.con.execute("UPDATE event SET summary='篡改' WHERE id=?", (eid,))
            check(False, "M105 世界事件必须不可改写")
        except sqlite3.IntegrityError:
            check(True, "M105 世界事件不可改写（草稿可改，事件不可改）")
    finally:
        store.close()


def main():
    d = tempfile.mkdtemp(prefix="p3-test-")
    print("=" * 68)
    print("P3 输出与交互层验收测试（离线引擎级）")
    print("=" * 68)
    test_m107_branch(d)
    test_m106_player(d)
    test_m106_inject(d)
    test_m105_narrative(d)
    print("-" * 68)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("P3 验收通过：叙事生成可读、作者可干预与入世、快照分支可回滚并独立推进。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
