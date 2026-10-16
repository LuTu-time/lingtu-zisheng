# -*- coding: utf-8 -*-
"""设定三态（canon/author/inference）与网文导入（实体抽取）验收测试。"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from play import create, open_world, load_ids        # noqa: E402
from engine import writing, canon_io, intake          # noqa: E402

SEED = 20261016
PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


SAMPLE = """张三说道：山里的粮食不多了。
李四问道：那黑风岭上可有出路？
张三冷笑道：黑风岭自古便是死地。
众人都沉默了。名叫王五的汉子说：我愿一试。
李四答道：好，那我们明日从柳坞村出发，渡了清河再说。
张三说：清河渡口有税，需备银钱。
李四叹道：罢了。黑风岭，黑风岭，终究要去的。
王五问：那张兄可愿同往？
张三说道：同往便同往。"""


def main():
    d = tempfile.mkdtemp(prefix="canon-test-")
    db = os.path.join(d, "world.db")
    create(db, SEED, "lishi", "设定三态世界")
    store, guard, cx = open_world(db)
    try:
        writing.ensure_schema(store)
        ids = load_ids(store)

        # --- A 设定三态 ---
        r_auth = writing.confirm_canon(store, cx, {"content": "柳坞村以耕种为生"})
        r_can = writing.confirm_canon(store, cx, {"content": "【原作】黑风岭终年多雾", "source": "canon"})
        r_inf = writing.confirm_canon(store, cx, {"content": "古桥的建造年代：三百年前",
                                                 "subject": "古桥", "attribute": "建造年代",
                                                 "value": "三百年前", "source": "inference"})
        fid = canon_io.record_inference(store, cx, "柳坞的户籍数约为四十户", "据村舍推算")
        states = {f["id"]: f["state"] for f in writing.list_canon(store)}
        check(r_auth["state"] == "author" and r_can["state"] == "canon"
              and r_inf["state"] == "inference", "A1 作者/原作/推断三种状态各自正确")
        check(states.get(fid) == "inference", "A2 引擎记录的推断为 inference 态")

        # --- B 硬度：推断可改，原作/作者只读 ---
        try:
            store.con.execute("UPDATE canon_fact SET note='改推断' WHERE id=?", (fid,))
            store.commit()
            soft_ok = True
        except sqlite3.IntegrityError:
            soft_ok = False
        check(soft_ok, "B1 推断事实可改写（soft）")
        aid = next(i for i, s in states.items() if s == "author")
        cid = next(i for i, s in states.items() if s == "canon")
        blocked = True
        for hid in (aid, cid):
            try:
                store.con.execute("UPDATE canon_fact SET content='篡改' WHERE id=?", (hid,))
                store.commit()
                blocked = False
            except sqlite3.IntegrityError:
                store.con.rollback()
        check(blocked, "B2 原作/作者事实不可改写（hard，受触发器保护）")

        # --- C 提升 ---
        # 重取一条推断（B1 已改 note，重新记录一条干净的）
        gid = canon_io.record_inference(store, cx, "河边老柳树约有百年", "据树围推算")
        pr = canon_io.promote_inference(store, cx, gid, "author")
        check(pr["state"] == "author" and canon_io.fact_state(
            *store.one("SELECT source,mutable,note FROM canon_fact WHERE id=?", (gid,))) == "author",
            "C1 推断可提升为作者确认")
        try:
            canon_io.promote_inference(store, cx, aid, "author")
            check(False, "C2 非推断不得再提升")
        except ValueError:
            check(True, "C2 非推断不得再提升")
        try:
            store.con.execute("UPDATE canon_fact SET content='x' WHERE id=?", (gid,))
            store.commit()
            check(False, "C3 提升后应变为只读")
        except sqlite3.IntegrityError:
            store.con.rollback()
            check(True, "C3 提升后变为只读（受触发器保护）")

        # --- D 一致性：相悖只在硬事实间判定，推断让位 ---
        writing.confirm_canon(store, cx, {"content": "河谷古桥的桥墩是石砌的"})
        writing.confirm_canon(store, cx, {"content": "河谷古桥的桥墩不是石砌的"})
        rep = canon_io.consistency_report(store)
        check(not rep["ok"] and any(c["type"] == "contradiction" for c in rep["conflicts"]),
              "D1 硬事实之间的正/反相悖被检出（「不是」不再被误判为正向）")
        canon_io.record_inference(store, cx, "河谷古桥的桥墩是木头的", "据残迹推断")
        rep2 = canon_io.consistency_report(store)
        check(all(c["type"] != "contradiction" or "木头" not in c["detail"] for c in rep2["conflicts"]),
              "D2 推断事实不参与相悖判定（让位于上位）")
        check(any(n["type"] == "inference_outranked" for n in rep2["notes"]),
              "D3 推断与硬事实同主语时给出让位提示（非违规）")

        # --- E 网文导入：实体抽取（纯函数） + 确认写入 ---
        events_before = store.scalar("SELECT COUNT(*) FROM event")
        ex = intake.extract(SAMPLE, min_person=2, min_place=2)
        names = {p["name"] for p in ex["persons"]}
        places = {p["name"] for p in ex["places"]}
        check({"张三", "李四", "王五"} <= names, f"E1 抽取到主要人物：{sorted(names)}")
        check("黑风岭" in places, f"E2 抽取到地点：{sorted(places)}")
        check(store.scalar("SELECT COUNT(*) FROM event") == events_before,
              "E3 抽取是纯函数：不写库、不产生事件")
        cands = intake.to_candidates(ex)
        check(any(c["content"] == "人物：张三" for c in cands)
              and any(c["content"] == "地点：黑风岭" for c in cands),
              "E4 抽取结果可转为可确认候选")
        written = canon_io.commit_candidates(store, cx, ids,
                                             store.scalar("SELECT tick FROM world WHERE id=1"),
                                             [{"content": "人物：张三", "source": "author", "mutable": 1}])
        check(written and any(f["content"] == "人物：张三" for f in writing.list_canon(store)),
              "E5 作者确认后写入为 author 事实")

        # --- F 把导入的人物一键建成世界角色 ---
        alive_before = store.scalar("SELECT COUNT(*) FROM character WHERE status='alive'")
        pop_before = store.scalar("SELECT population FROM settlement WHERE id=?", (ids["settlement_id"],))
        cast = intake.cast_characters(store, cx, ids, [{"name": "导客甲"},
                                                       {"name": "导客乙", "occupation": "猎户", "age": 30}])
        check(cast["count"] == 2
              and store.scalar("SELECT COUNT(*) FROM character WHERE name='导客甲' AND status='alive'") == 1,
              "F1 导入人物建成世界角色")
        check(store.scalar("SELECT occupation FROM character WHERE name='导客乙'") == "猎户"
              and store.scalar("SELECT age FROM character WHERE name='导客乙'") == 30,
              "F2 可指定身份与年龄（未指定则确定性推导）")
        check(store.scalar("SELECT COUNT(*) FROM character WHERE status='alive'") == alive_before + 2
              and store.scalar("SELECT population FROM settlement WHERE id=?",
                               (ids["settlement_id"],)) == pop_before,
              "F3 具名角色不改变抽象人口（与入世角色同例）")
        check(store.scalar("SELECT COUNT(*) FROM event WHERE kind='character_cast'") == 2,
              "F4 每位引入都写入不可改写事件")
        for bad, label in (([{"name": "导客甲"}], "F5 同名在世角色被拒"),
                           ([], "F6 空批次被拒"),
                           ([{"name": "甲"}], "F7 姓名过短被拒")):
            try:
                intake.cast_characters(store, cx, ids, bad)
                check(False, label)
            except ValueError:
                check(True, label)

        # --- G 人物关系：抽取 + 建立 ---
        rel_text = ("关系甲说：我收你为徒。\n关系甲是关系乙的师父。\n关系乙道：多谢师父。\n"
                    "关系丙问：你二人是旧识？\n关系丙和关系乙有仇。")
        rx = intake.extract(rel_text, min_person=2, min_place=2)
        pairs = {(r["a"], r["b"]) for r in rx["relations"]}
        check(("关系甲", "关系乙") in pairs or len(rx["relations"]) >= 2,
              f"G1 抽取候选关系：{sorted(pairs)}")
        check(any(r["kind"] == "恩" for r in rx["relations"])
              and any(r["kind"] == "怨" for r in rx["relations"]),
              "G2 称谓/动词映射到 kind（师父→恩、有仇→怨）")
        links = [{"a": "关系甲", "b": "关系乙", "role": "师父", "kind": "恩", "intensity": 0.6}]
        try:
            intake.declare_relations(store, cx, ids, links)
            check(False, "G3 双方未建角时必须拒绝")
        except ValueError:
            check(True, "G3 双方未建角时拒绝并提示先建角")
        intake.cast_characters(store, cx, ids, [{"name": "关系甲"}, {"name": "关系乙"}, {"name": "关系丙"}])
        res = intake.declare_relations(store, cx, ids, links)
        check(res["count"] == 1 and not res["skipped"], "G4 建角后可建立关系")
        jia = store.scalar("SELECT id FROM character WHERE name='关系甲'")
        yi = store.scalar("SELECT id FROM character WHERE name='关系乙'")
        rel = store.all("SELECT from_id,to_id,kind FROM relation WHERE from_id IN (?,?) AND to_id IN (?,?)",
                        (jia, yi, jia, yi))
        check(any(r["from_id"] == jia and r["to_id"] == yi for r in rel)
              and any(r["from_id"] == yi and r["to_id"] == jia for r in rel),
              "G5 关系是有向的：双向各写一条")
        check(store.scalar("SELECT COUNT(*) FROM event WHERE kind='relation_declared'") == 1,
              "G6 建立关系写入不可改写事件")
        res2 = intake.declare_relations(store, cx, ids,
                                        [{"a": "关系甲", "b": "关系丙", "role": "旧识"},
                                         {"a": "关系甲", "b": "查无此人", "role": "旧识"}])
        check(res2["count"] == 1 and len(res2["skipped"]) == 1,
              "G7 名字对不上时跳过、其余照常建立")
        try:
            intake.declare_relations(store, cx, ids, [{"a": "关系甲", "b": "查无此人", "role": "旧识"}])
            check(False, "G8 全部无法建立时拒绝")
        except ValueError:
            check(True, "G8 全部无法建立时拒绝")
    finally:
        store.close()

    print("-" * 64)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("设定三态与网文导入验收通过：三态分层、推断让位、抽取只读可确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
