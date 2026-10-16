# -*- coding: utf-8 -*-
"""Local HTTP smoke tests for the browser platform."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PYTHON = sys.executable


def request(url, method="GET", payload=None, timeout=5):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            if response.headers.get_content_type() == "application/json":
                return response.status, json.loads(body)
            return response.status, body.decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else {}


def main():
    temp = tempfile.mkdtemp(dir=os.path.join(ROOT, "db"))
    try:
        db = os.path.join(temp, "platform-test.db")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        offline = "--offline" in sys.argv
        if offline:
            # Rejected before any network request, independent of local model availability.
            env["LINGTU_LLM_URL"] = "https://example.invalid"
        proc = subprocess.Popen([PYTHON, os.path.join(ROOT, "platform.py"), "--host", "127.0.0.1",
                                 "--port", str(port), "--db", db, "--new-world", "--seed", "19001"],
                                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise AssertionError("平台进程提前退出：" + (proc.stdout.read() or ""))
                try:
                    status, html = request(base + "/")
                    if status == 200:
                        break
                except (OSError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise AssertionError("平台未能启动")
            assert "成为世界中的一个人" in html
            status, writer_html = request(base + "/write")
            assert status == 200 and "小说写作工作台" in writer_html
            assert 'data-person="${p.id}"' in writer_html and 'id="all-events"' in writer_html
            assert 'for(const event of events)eventCache.set(event.id,event)' in writer_html
            assert 'id="older-events"' in writer_html and 'async function hydrateRefs()' in writer_html
            status, simulation_html = request(base + "/simulation")
            assert status == 200 and "世界观察台" in simulation_html and "时间线存档" in simulation_html
            status, archive = request(base + "/api/snapshots")
            assert status == 200 and archive["snapshots"] == [] and archive["branches"] == []
            assert archive["current_db"] == db and archive["root_db"] == db
            status, chapters = request(base + "/api/chapters")
            assert status == 200 and chapters == []
            status, before_canon = request(base + "/api/status")
            status, candidate = request(base + "/api/canon/preview", "POST",
                                        {"text": "河谷有古桥\n河谷有古桥\n桥下埋着旧碑"})
            assert status == 200 and candidate == {"candidates": ["河谷有古桥", "桥下埋着旧碑"],
                                                   "confirmed": False}
            status, preview_status = request(base + "/api/status")
            assert preview_status["事件数"] == before_canon["事件数"]
            status, too_many = request(base + "/api/canon/preview", "POST", {"text": "行\n" * 201})
            assert status == 400 and too_many["error"]
            status, facts = request(base + "/api/canon")
            assert status == 200 and facts == []
            status, confirmed = request(base + "/api/canon", "POST",
                                        {"content": candidate["candidates"][0], "source": "canon",
                                         "note": "测试原文第一行"})
            assert status == 200 and confirmed["event_id"]
            status, after_canon = request(base + "/api/status")
            assert after_canon["事件数"] == before_canon["事件数"] + 1 and after_canon["history_ok"]
            status, duplicate_fact = request(base + "/api/canon", "POST",
                                             {"content": "河谷有古桥", "source": "canon"})
            assert status == 400 and duplicate_fact["error"]
            status, facts = request(base + "/api/canon")
            assert status == 200 and len(facts) == 1 and facts[0]["mutable"] == 0
            with sqlite3.connect(db) as con:
                for operation in (
                    ("UPDATE canon_fact SET content='篡改' WHERE id=?", (facts[0]["id"],)),
                    ("UPDATE canon_fact SET mutable=1 WHERE id=?", (facts[0]["id"],)),
                    ("DELETE FROM canon_fact WHERE id=?", (facts[0]["id"],)),
                ):
                    try:
                        con.execute(*operation)
                    except sqlite3.IntegrityError:
                        con.rollback()
                    else:
                        raise AssertionError("已确认设定必须由数据库拒绝改写")
                assert con.execute("SELECT content FROM canon_fact WHERE id=?", (facts[0]["id"],)).fetchone()[0] == "河谷有古桥"
                con.execute("DROP TRIGGER trg_canon_fact_protect_update")
                con.execute("DROP TRIGGER trg_canon_fact_protect_delete")
                con.execute("INSERT INTO canon_fact(content,source,mutable) VALUES('旧原作设定','canon',1)")
            status, _ = request(base + "/api/canon")
            assert status == 200
            with sqlite3.connect(db) as con:
                names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
                assert {"trg_canon_fact_protect_update", "trg_canon_fact_protect_delete"} <= names
                try:
                    con.execute("DELETE FROM canon_fact WHERE content='旧原作设定'")
                except sqlite3.IntegrityError:
                    con.rollback()
                else:
                    raise AssertionError("旧原作设定即使 mutable=1 也须保护")
            status, migrated = request(base + "/api/canon", "POST", {"content": "河谷有渡口"})
            assert status == 200 and migrated["event_id"]
            status, claim = request(base + "/api/canon", "POST", {
                "subject": "古桥", "attribute": "建造年代", "value": "三百年前"})
            assert status == 200 and claim["event_id"] and claim["content"] == "古桥的建造年代：三百年前"
            status, normalized = request(base + "/api/canon", "POST", {
                "content": "与字段不符", "subject": "柳坞", "attribute": "地形", "value": "丘陵"})
            assert status == 200 and normalized["content"] == "柳坞的地形：丘陵"
            status, before_conflict = request(base + "/api/status")
            status, conflict = request(base + "/api/canon", "POST", {
                "content": "古桥为一百年前建造", "subject": "古桥", "attribute": "建造年代", "value": "一百年前"})
            assert status == 400 and "设定冲突" in conflict["error"]
            status, repeat = request(base + "/api/canon", "POST", {
                "content": "古桥约三百年前建立", "subject": "古桥", "attribute": "建造年代", "value": "三百年前"})
            assert status == 400 and "已经存在" in repeat["error"]
            status, invalid_claim = request(base + "/api/canon", "POST", {
                "content": "残缺设定", "subject": "古桥"})
            assert status == 400 and "对象、属性和取值" in invalid_claim["error"]
            status, after_conflict = request(base + "/api/status")
            assert after_conflict["事件数"] == before_conflict["事件数"] and after_conflict["history_ok"]
            status, claims = request(base + "/api/canon")
            assert any(f["subject"] == "古桥" and f["value"] == "三百年前" for f in claims)
            with sqlite3.connect(db) as con:
                try:
                    con.execute("UPDATE canon_claim SET value='一百年前' WHERE subject='古桥'")
                except sqlite3.IntegrityError:
                    con.rollback()
                else:
                    raise AssertionError("结构化设定不得直接改写")
            from play import open_world
            from engine import writing
            store, _, cx = open_world(db)
            try:
                before = cx.anchor().copy()
                store.con.execute("CREATE TRIGGER fail_canon_insert BEFORE INSERT ON canon_fact "
                                  "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
                store.commit()
                try:
                    writing.confirm_canon(store, cx, {"content": "本应回滚的设定"})
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError("设定插入失败时应回滚事件")
                assert cx.anchor() == before and cx.verify_anchor()["ok"]
                assert not store.scalar("SELECT COUNT(*) FROM canon_fact WHERE content='本应回滚的设定'")
                store.con.execute("DROP TRIGGER fail_canon_insert")
                store.commit()
                after = writing.confirm_canon(store, cx, {"content": "回滚后继续确认"})
                assert after["event_id"] and cx.verify_chain()[0] and cx.verify_anchor()["ok"]
            finally:
                store.close()
            status, invalid_loan = request(base + "/api/loans", "POST", {"amount": 0, "term_days": 30})
            assert status == 400 and invalid_loan["error"]
            status, reserve_state = request(base + "/api/status")
            status, issued_loan = request(base + "/api/loans", "POST", {"amount": 5, "term_days": 30})
            assert status == 200 and issued_loan["event_id"] and issued_loan["loan_id"]
            status, loan_rows = request(base + "/api/loans")
            assert status == 200 and loan_rows[0]["id"] == issued_loan["loan_id"]
            with sqlite3.connect(db) as con:
                assert con.execute("SELECT amount FROM stock WHERE owner_kind='faction' AND resource_type='grain'").fetchone()[0] == reserve_state["势力储粮"] - 5
                assert con.execute("SELECT amount FROM stock WHERE owner_kind='settlement' AND resource_type='grain'").fetchone()[0] == reserve_state["聚落库存"]["grain"] + 5
            status, live = request(base + "/live")
            assert status == 200 and "成为世界中的一个人" in live
            status, person = request(base + "/api/player")
            assert status == 200 and person["player"] is None
            status, bad_player = request(base + "/api/player", "POST",
                                         {"name": "", "occupation": "旅人", "age": 20, "goal": "寻亲"})
            assert status == 400 and "姓名" in bad_player["error"]
            status, person = request(base + "/api/player", "POST",
                                     {"name": "测试旅人", "occupation": "旅人", "age": 25, "goal": "寻找故人"})
            assert status == 200 and person["name"] == "测试旅人"
            status, duplicate = request(base + "/api/player", "POST",
                                        {"name": "另一个人", "occupation": "旅人", "age": 26, "goal": "寻亲"})
            assert status == 400 and "已有入世角色" in duplicate["error"]
            status, people = request(base + "/api/people")
            target = next(p for p in people if p["id"] != person["id"] and p["status"] == "alive")
            status, invalid_visit = request(base + "/api/player/action", "POST",
                                             {"action": "visit", "target_id": -999, "words": "你好"})
            assert status == 400
            status, visit = request(base + "/api/player/action", "POST",
                                    {"action": "visit", "target_id": target["id"], "words": "你可见过故人？"})
            assert status == 200 and visit["event_id"]
            status, target_detail = request(base + f"/api/people/detail?id={target['id']}")
            assert status == 200 and any(e["id"] == visit["event_id"] for e in target_detail["events"])
            status, person_chapter = request(base + "/api/chapters", "POST",
                                             {"title": "人物经历", "body": "村人见证了拜访。",
                                              "notes": "区分主观记忆与世界事实",
                                              "event_ids": [target_detail["events"][0]["id"]]})
            assert status == 200 and json.loads(person_chapter["event_ids"]) == [target_detail["events"][0]["id"]]
            status, long_chapter = request(base + "/api/chapters", "POST",
                                           {"title": "长章节", "body": "文" * 3000})
            assert status == 200 and len(long_chapter["body"]) == 3000
            status, long_reloaded = request(base + f"/api/chapters/{long_chapter['id']}")
            assert status == 200 and long_reloaded["body"] == "文" * 3000
            status, non_chapter_large = request(base + "/api/player/action", "POST",
                                                {"action": "visit", "words": "文" * 3000})
            assert status == 413 and "请求体过大" in non_chapter_large["error"]
            status, too_large = request(base + "/api/chapters", "POST",
                                        {"title": "超限章节", "body": "文" * 210000})
            assert status == 400 and "20 万" in too_large["error"]
            status, person_reloaded = request(base + f"/api/chapters/{person_chapter['id']}")
            assert status == 200 and person_reloaded["body"] == "村人见证了拜访。"
            assert json.loads(person_reloaded["event_ids"]) == [visit["event_id"]]
            assert any(m["event_id"] == visit["event_id"] for m in target_detail["memories"])
            status, bad_detail = request(base + "/api/people/detail?id=invalid")
            assert status == 400 and bad_detail["error"]
            status, model = request(base + "/api/model")
            assert status == 200 and model["model"]
            if "--require-model" in sys.argv:
                assert model["available"], "本轮要求真实本地模型可用，不允许跳过"
            status, before_talk = request(base + "/api/status")
            status, talked = request(base + "/api/player/action", "POST",
                                     {"action": "talk", "target_id": target["id"], "words": "山里的粮食还够吗？"}, timeout=110)
            if model["available"]:
                assert status == 200 and talked["reply"] and talked["event_id"]
            else:
                assert status == 503 and talked["error"]
                status, after_talk = request(base + "/api/status")
                assert after_talk["事件数"] == before_talk["事件数"]
                assert after_talk["history_ok"]
            status, help_result = request(base + "/api/player/action", "POST", {"action": "help"})
            assert status == 200 and help_result["event_id"]
            status, again = request(base + "/api/player")
            assert status == 200 and len(again["player"]["experiences"]) == 3 + int(model["available"])
            assert again["player"]["id"] == person["id"]
            status, initial = request(base + "/api/status")
            assert initial["人口"] == 19, "到访者不得计入村民人口"
            assert status == 200 and initial["history_ok"]
            assert initial["daily"] and all(isinstance(e["id"], int) for e in initial["daily"])
            status, people = request(base + "/api/people")
            assert status == 200 and len(people) >= 10
            status, bad = request(base + "/api/step", "POST", {"days": 0})
            assert status == 400 and "1~365" in bad["error"]
            status, stepped = request(base + "/api/step", "POST", {"days": 3})
            assert status == 200 and stepped["第几日"] == 3
            status, intervention = request(base + "/api/intervene", "POST",
                                           {"action": "relief", "amount": 5})
            assert status == 200 and intervention["event_id"]
            status, chain = request(base + f"/api/why/{intervention['event_id']}")
            assert status == 200 and chain[-1]["id"] == intervention["event_id"]
            status, annual = request(base + "/api/chronicle/annual?year=1")
            assert status == 200 and annual["year"] == 1 and not annual["complete"]
            assert any(item["topic"] == "物价" for item in annual["facts"])
            status, invalid_year = request(base + "/api/chronicle/annual?year=abc")
            assert status == 400 and invalid_year["error"]
            status, events = request(base + "/api/events?limit=20")
            assert status == 200 and any(e["kind"] == "author_relief" for e in events)
            status, first_page = request(base + "/api/events?limit=2")
            assert status == 200 and len(first_page) == 2 and first_page[0]["id"] > first_page[1]["id"]
            status, older_page = request(base + f"/api/events?limit=2&before={first_page[-1]['id']}")
            assert status == 200 and all(e["id"] < first_page[-1]["id"] for e in older_page)
            status, referenced = request(base + f"/api/events?ids={visit['event_id']},{first_page[0]['id']}")
            assert status == 200 and {e["id"] for e in referenced} == {visit["event_id"], first_page[0]["id"]}
            for suffix in ("?before=0", "?before=abc", "?ids=1,1", "?ids=0", "?ids=1&before=2"):
                status, bad_events = request(base + "/api/events" + suffix)
                assert status == 400 and bad_events["error"]
            for invalid_refs in ([], [True], [0], [999999], list(range(1, 10))):
                status, invalid_draft = request(base + "/api/chapters/suggest", "POST",
                                                {"event_ids": invalid_refs})
                assert status == 400 and invalid_draft["error"]
            with sqlite3.connect(db) as con:
                before_draft = list(con.iterdump())
            status, draft = request(base + "/api/chapters/suggest", "POST",
                                    {"event_ids": [intervention["event_id"]], "direction": "侧重人物感受"}, timeout=110)
            if model["available"]:
                assert status == 200 and draft["text"] and draft["event_ids"] == [intervention["event_id"]]
            else:
                assert status == 503 and draft["error"]
            with sqlite3.connect(db) as con:
                assert list(con.iterdump()) == before_draft, "生成草稿不得改变任何数据库记录"
            status, invalid = request(base + "/api/chapters", "POST",
                                      {"title": "第一章", "body": "开端", "event_ids": [999999]})
            assert status == 400 and "不存在" in invalid["error"]
            status, chapter = request(base + "/api/chapters", "POST",
                                      {"title": "第一章", "body": "河谷起雾了。", "notes": "埋下伏笔",
                                       "event_ids": [intervention["event_id"]]})
            assert status == 200 and chapter["revision"] == 1
            status, saved = request(base + f"/api/chapters/{chapter['id']}")
            assert status == 200 and saved["body"] == "河谷起雾了。"
            status, revised = request(base + "/api/chapters", "POST",
                                      {"id": chapter["id"], "revision": 1, "title": "第一章 · 溪谷",
                                       "body": "河谷起雾了。有人离开了家。", "notes": "埋下伏笔",
                                       "event_ids": [intervention["event_id"]]})
            assert status == 200 and revised["revision"] == 2
            status, stale = request(base + "/api/chapters", "POST",
                                    {"id": chapter["id"], "revision": 1, "title": "旧窗口",
                                     "body": "不应覆盖", "event_ids": []})
            assert status == 409
            status, listing = request(base + "/api/chapters")
            assert status == 200 and listing[0]["title"] == "第一章 · 溪谷"
            status, export_md = request(base + "/api/chapters/export?format=md")
            assert status == 200 and "第一章 · 溪谷" in export_md and "河谷起雾了" in export_md
            status, export_txt = request(base + "/api/chapters/export?format=txt")
            assert status == 200 and "第一章 · 溪谷" in export_txt
            status, final = request(base + "/api/status")
            assert status == 200 and final["history_ok"] and final["第几日"] == 3
            assert final["事件数"] == stepped["事件数"] + 1, "写作草稿不得改写世界事件"
            status, invalid_snapshot = request(base + "/api/snapshots", "POST", {"name": ""})
            assert status == 400 and invalid_snapshot["error"]
            status, saved_snapshot = request(base + "/api/snapshots", "POST", {"name": "第三日"})
            assert status == 200 and os.path.isfile(saved_snapshot["path"])
            status, archive = request(base + "/api/snapshots")
            assert status == 200 and any(s["id"] == saved_snapshot["id"] for s in archive["snapshots"])
            status, invalid_branch = request(base + "/api/branches", "POST",
                                             {"snapshot_id": "../../play.db", "name": "无效"})
            assert status == 400 and invalid_branch["error"]
            status, branch = request(base + "/api/branches", "POST",
                                     {"snapshot_id": saved_snapshot["id"], "name": "另一条时间线"})
            assert status == 200 and branch["switched"] is False and os.path.isfile(branch["path"])
            with sqlite3.connect(db) as source, sqlite3.connect(branch["path"]) as other:
                assert list(source.iterdump()) == list(other.iterdump())
            status, current = request(base + "/api/snapshots")
            assert status == 200 and current["current_db"] == db and len(current["branches"]) == 1
            branch_id = current["branches"][0]["id"]
            status, invalid_switch = request(base + "/api/branches/switch", "POST", {"branch_id": "../bad"})
            assert status == 400 and invalid_switch["error"]
            status, switched = request(base + "/api/branches/switch", "POST", {"branch_id": branch_id})
            assert status == 200 and switched["current_db"] == branch["path"] and switched["switched"]
            status, branch_step = request(base + "/api/step", "POST", {"days": 1})
            assert status == 200 and branch_step["第几日"] == 4
            with sqlite3.connect(db) as source, sqlite3.connect(branch["path"]) as other:
                assert source.execute("SELECT tick FROM world").fetchone()[0] == 72
                assert other.execute("SELECT tick FROM world").fetchone()[0] == 96
            status, returned = request(base + "/api/branches/switch", "POST", {"branch_id": None})
            assert status == 200 and returned["current_db"] == db and not returned["switched"]
            status, final_root = request(base + "/api/status")
            assert status == 200 and final_root["第几日"] == 3 and final_root["history_ok"]
            with sqlite3.connect(branch["path"]) as damaged:
                damaged.execute("UPDATE world SET chain_head='bad' WHERE id=1")
            status, rejected = request(base + "/api/branches/switch", "POST", {"branch_id": branch_id})
            assert status == 400 and rejected["error"]
            status, after_reject = request(base + "/api/snapshots")
            assert after_reject["current_db"] == db
            # M106 上帝面板：凭空注入自定义事件（只追加、不改写）
            status, bad_inject = request(base + "/api/inject", "POST", {"summary": ""})
            assert status == 400 and bad_inject["error"]
            status, bad_place = request(base + "/api/inject", "POST",
                                        {"summary": "无中生有", "place_kind": "town"})
            assert status == 400 and bad_place["error"]
            status, injected = request(base + "/api/inject", "POST",
                                       {"summary": "一场秋雨落了整夜，谷物受了潮",
                                        "kind": "author_weather"})
            assert status == 200 and injected["event_id"] and injected["kind"] == "author_weather"
            with sqlite3.connect(db) as con:
                actor, cause = con.execute("SELECT actor_kind,cause_event_id FROM event WHERE id=?",
                                           (injected["event_id"],)).fetchone()
                assert actor == "author" and cause is None
            status, after_inject = request(base + "/api/status")
            assert status == 200 and after_inject["history_ok"]
            # 状态锚定：把可变状态表指纹封印进不可改写历史
            status, state0 = request(base + "/api/state")
            assert status == 200 and state0["sealed"] is False
            status, sealed = request(base + "/api/seal", "POST", {"label": "验收封印"})
            assert status == 200 and sealed["event_id"] and len(sealed["digest"]) == 64
            status, state1 = request(base + "/api/state")
            assert status == 200 and state1["match"] is True and state1["drifted"] == []
            assert state1["seal_event_id"] == sealed["event_id"]
            with sqlite3.connect(db) as con:
                con.execute("UPDATE stock SET amount=amount+123 WHERE owner_kind='settlement' "
                            "AND resource_type='grain'")
            status, state2 = request(base + "/api/state")
            assert status == 200 and state2["match"] is False and "stock" in state2["drifted"]
            status, cp = request(base + "/api/state/checkpoint")
            assert status == 200 and cp["checkpoint"] and cp["verified"] is True \
                and cp["attested_by_event"] is True and cp["table_counts"]
            status, since = request(base + "/api/state/since")
            assert status == 200 and isinstance(since["drifted"], list) and "stock" in since["drifted"]
            status, inf = request(base + "/api/canon/inference", "POST",
                                  {"content": "柳坞的户籍数约为四十户", "basis": "据村舍推算"})
            assert status == 200 and inf["fact_id"]
            status, facts2 = request(base + "/api/canon")
            assert any(f["id"] == inf["fact_id"] and f["state"] == "inference" for f in facts2)
            status, promoted = request(base + "/api/canon/promote", "POST",
                                       {"fact_id": inf["fact_id"], "to": "author"})
            assert status == 200 and promoted["state"] == "author"
            status, ex = request(base + "/api/import/preview", "POST",
                                 {"text": "张三说：好。李四问：如何？张三道：且看。"})
            assert status == 200 and "candidates" in ex and "persons" in ex
            status, cast = request(base + "/api/import/cast", "POST",
                                   {"persons": [{"name": "验收角色甲", "occupation": "镖师"}]})
            assert status == 200 and cast["count"] == 1
            status, cast_people = request(base + "/api/people")
            assert any(p["name"] == "验收角色甲" and p["occupation"] == "镖师" for p in cast_people)
            status, dup_cast = request(base + "/api/import/cast", "POST", {"persons": [{"name": "验收角色甲"}]})
            assert status == 400 and dup_cast["error"]
            status, _ = request(base + "/api/import/cast", "POST", {"persons": [{"name": "验收角色乙"}]})
            assert status == 200
            status, rel = request(base + "/api/import/relate", "POST",
                                  {"links": [{"a": "验收角色甲", "b": "验收角色乙", "role": "旧识",
                                              "kind": "attitude", "intensity": 0.2}]})
            assert status == 200 and rel["count"] == 1
            status, rel_bad = request(base + "/api/import/relate", "POST",
                                      {"links": [{"a": "验收角色甲", "b": "不存在的人", "role": "旧识"}]})
            assert status == 400 and rel_bad["error"]
            print("PASS: 状态存档点可重建、设定三态、网文抽取/建角/建关系；", end="")
            print("PASS: 状态封印可查出并定位绕过事件的状态篡改；", end="")
            print("PASS:快照与独立分支切换、损坏分支拒绝、人物参与事件、作者注入事件；", end="")
            print("PASS: 入世、交谈、草稿只读、输入边界、章节修订、推演和历史校验；模型=" + ("真实本地模型" if model["available"] else "不可用503路径"))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    finally:
        # The host test shim may intercept recursive deletion; leave the
        # temporary database directory for the workspace cleanup mechanism.
        pass


if __name__ == "__main__":
    main()
