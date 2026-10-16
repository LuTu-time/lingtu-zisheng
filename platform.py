# -*- coding: utf-8 -*-
"""Local web platform for the persistent Lingtu world simulation."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

from engine.causality import Causality
from engine.guardrails import Guardrail
from engine import sim, writing, player, local_llm, branches, chronicle, intervention, projection, canon_io, intake
from engine.store import Store
from play import DEFAULT_DB, create, intervene, load_ids, open_world, snapshot

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.join(ROOT, "web")
DB_LOCK = threading.RLock()


class Handler(BaseHTTPRequestHandler):
    server_version = "LingtuLocal/1.0"

    def log_message(self, fmt, *args):
        print("[平台] " + (fmt % args))

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, name):
        path = os.path.join(WEB_ROOT, name)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as source:
            data = source.read()
        if name.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif name.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif name.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, status, text, content_type="text/plain; charset=utf-8", filename=None):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._do_get()
        except Exception as exc:
            print(f"[平台错误] {type(exc).__name__}: {exc}")
            try:
                self.send_json(500, {"error": "读取失败，请检查服务端存档"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _do_get(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send_file("live.html")
        if path == "/write":
            return self.send_file("writer.html")
        if path == "/simulation":
            return self.send_file("index.html")
        if path == "/live":
            return self.send_file("live.html")
        if path == "/app.css":
            return self.send_file("app.css")
        if path == "/api/model":
            return self.send_json(200, local_llm.status())
        if path == "/api/snapshots":
            with DB_LOCK:
                root = self.server.root_db_path
                return self.send_json(200, {
                    "current_db": self.server.db_path,
                    "root_db": root,
                    "snapshots": branches.list_snapshots(root),
                    "branches": branches.list_branches(root),
                })
        if path == "/api/state":
            with DB_LOCK:
                store, _, cx = open_world(self.server.db_path)
                try:
                    return self.send_json(200, projection.verify_state(store, cx))
                finally:
                    store.close()
        if path in ("/api/state/checkpoint", "/api/state/since"):
            qs = parse_qs(urlparse(self.path).query)
            try:
                cid = int(qs["id"][0]) if "id" in qs and qs["id"][0] else None
            except ValueError:
                return self.send_json(400, {"error": "存档点编号无效"})
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    if path == "/api/state/checkpoint":
                        at = projection.state_at(store, cid)
                        return self.send_json(200, {
                            "checkpoint": at.get("checkpoint"), "tick": at.get("tick"),
                            "label": at.get("label"), "verified": at.get("verified"),
                            "attested_by_event": at.get("attested_by_event"),
                            "table_counts": {n: len(t["rows"]) for n, t in (at.get("tables") or {}).items()},
                            "reason": at.get("reason")})
                    return self.send_json(200, projection.since(store, cid))
                finally:
                    store.close()
        if path == "/api/chronicle/annual":
            try:
                year = int(parse_qs(urlparse(self.path).query)["year"][0])
            except (KeyError, IndexError, ValueError):
                return self.send_json(400, {"error": "年份须为正整数"})
            if not 1 <= year <= 10000:
                return self.send_json(400, {"error": "年份须在 1~10000 之间"})
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    return self.send_json(200, chronicle.annual_chronicle(store, year))
                finally:
                    store.close()
        if path == "/api/chronicle":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    limit = 100
                    query = urlparse(self.path).query
                    for part in query.split("&"):
                        if part.startswith("limit="):
                            limit = int(part[6:])
                    if not 1 <= limit <= 500:
                        return self.send_json(400, {"error": "纪事数量须为 1~500"})
                    rows = store.all("SELECT id, tick, kind, summary, cause_event_id, depth "
                                     "FROM event ORDER BY id ASC LIMIT ?", (limit,))
                    return self.send_json(200, [dict(row) for row in rows])
                finally:
                    store.close()
        if path == "/api/player":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    player.ensure_schema(store)
                    return self.send_json(200, {"player": player.current(store)})
                finally:
                    store.close()
        if path == "/api/canon":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    return self.send_json(200, writing.list_canon(store))
                finally:
                    store.close()
        if path == "/api/chapters":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    writing.ensure_schema(store)
                    return self.send_json(200, writing.list_chapters(store))
                finally:
                    store.close()
        if path == "/api/chapters/export":
            fmt = "md"
            for part in urlparse(self.path).query.split("&"):
                if part.startswith("format="):
                    fmt = part[7:]
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    writing.ensure_schema(store)
                    doc = writing.manuscript(store, fmt)
                finally:
                    store.close()
            return self.send_text(200, doc["text"], doc["content_type"], doc["filename"])
        if path.startswith("/api/chapters/"):
            try:
                chapter_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_json(400, {"error": "章节编号无效"})
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    writing.ensure_schema(store)
                    try:
                        chapter = writing.get_chapter(store, chapter_id)
                    except ValueError as exc:
                        return self.send_json(404, {"error": str(exc)})
                    return self.send_json(200, chapter)
                finally:
                    store.close()
        if path == "/api/loans":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    sim.ensure_loan_schema(store)
                    rows = store.all("SELECT id,principal,balance,issued_tick,due_tick,status,issue_event_id "
                                     "FROM grain_loan ORDER BY id DESC LIMIT 200")
                    return self.send_json(200, [dict(row) for row in rows])
                finally:
                    store.close()
        if path == "/api/status":
            with DB_LOCK:
                store, _, cx = open_world(self.server.db_path)
                try:
                    state = snapshot(store)
                    state["history_ok"] = cx.verify_chain()[0] and cx.verify_anchor()["ok"]
                    state["active_factions"] = [dict(row) for row in store.all(
                        "SELECT id, name, kind, goal, manpower FROM faction WHERE status='active' ORDER BY id")]
                    state["daily"] = store.all(
                        "SELECT id, tick, kind, summary FROM event ORDER BY id DESC LIMIT 12")
                    state["daily"] = [dict(row) for row in reversed(state["daily"])]
                    return self.send_json(200, state)
                finally:
                    store.close()
        if path == "/api/people":
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    rows = store.all("SELECT id, name, age, occupation, status, goal_stack, profile "
                                     "FROM character ORDER BY CASE status WHEN 'alive' THEN 0 ELSE 1 END, id LIMIT 200")
                    people = []
                    for row in rows:
                        p = dict(row)
                        p["goal_stack"] = json.loads(p["goal_stack"] or "[]")
                        p["profile"] = json.loads(p["profile"] or "{}")
                        people.append(p)
                    return self.send_json(200, people)
                finally:
                    store.close()
        if path == "/api/people/detail":
            query = urlparse(self.path).query
            person_id = None
            try:
                for part in query.split("&"):
                    if part.startswith("id="):
                        person_id = int(part[3:])
            except ValueError:
                return self.send_json(400, {"error": "人物编号无效"})
            if not isinstance(person_id, int) or person_id <= 0:
                return self.send_json(400, {"error": "人物编号无效"})
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    person = store.one("SELECT id,name,age,occupation,status,profile,traits,needs,goal_stack "
                                       "FROM character WHERE id=?", (person_id,))
                    if not person:
                        return self.send_json(404, {"error": "人物不存在"})
                    result = dict(person)
                    for key in ("profile", "traits", "needs", "goal_stack"):
                        result[key] = json.loads(result[key] or ("[]" if key == "goal_stack" else "{}"))
                    result["memories"] = [dict(row) for row in store.all(
                        "SELECT id,tick,content,importance,event_id FROM memory WHERE owner_id=? ORDER BY tick DESC,id DESC LIMIT 30",
                        (person_id,))]
                    result["events"] = []
                    cursor = store.scalar("SELECT COALESCE(MAX(id),0)+1 FROM event")
                    while len(result["events"]) < 30:
                        recent = store.all(
                            "SELECT id,tick,kind,summary,cause_event_id,actor_kind,actor_id,participants "
                            "FROM event WHERE id<? ORDER BY id DESC LIMIT 200", (cursor,))
                        if not recent:
                            break
                        for row in recent:
                            event = dict(row)
                            participants = json.loads(event.pop("participants") or "[]")
                            involved = event.pop("actor_kind") == "character" and event.pop("actor_id") == person_id
                            if involved or any(isinstance(p, dict) and p.get("kind") == "character" and
                                               type(p.get("id")) is int and p["id"] == person_id for p in participants):
                                result["events"].append(event)
                            if len(result["events"]) == 30:
                                break
                        cursor = recent[-1]["id"]
                    result["relations"] = [dict(row) for row in store.all(
                        "SELECT r.kind,r.intensity,r.note,r.publicity,c.name AS other_name "
                        "FROM relation r JOIN character c ON c.id=r.to_id "
                        "WHERE r.from_kind='character' AND r.from_id=? AND r.to_kind='character' "
                        "ORDER BY ABS(r.intensity) DESC,r.id LIMIT 20", (person_id,))]
                    return self.send_json(200, result)
                finally:
                    store.close()
        if path == "/api/events":
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            try:
                limit = int(query.get("limit", ["100"])[0])
                before = int(query["before"][0]) if "before" in query else None
                ids = [int(value) for value in query["ids"][0].split(",")] if "ids" in query else None
            except ValueError:
                return self.send_json(400, {"error": "事件查询参数无效"})
            if not 1 <= limit <= 200 or before is not None and before <= 0:
                return self.send_json(400, {"error": "事件数量或游标无效"})
            if ids is not None and (before is not None or not 1 <= len(ids) <= 100 or
                                    any(i <= 0 for i in ids) or len(set(ids)) != len(ids)):
                return self.send_json(400, {"error": "事件编号列表无效"})
            with DB_LOCK:
                store, _, _ = open_world(self.server.db_path)
                try:
                    columns = "SELECT id, tick, kind, summary, cause_event_id, depth, effects FROM event "
                    if ids is not None:
                        placeholders = ",".join("?" for _ in ids)
                        rows = store.all(columns + f"WHERE id IN ({placeholders}) ORDER BY id DESC", ids)
                    elif before is not None:
                        rows = store.all(columns + "WHERE id<? ORDER BY id DESC LIMIT ?", (before, limit))
                    else:
                        rows = store.all(columns + "ORDER BY id DESC LIMIT ?", (limit,))
                    return self.send_json(200, [dict(r) for r in rows])
                finally:
                    store.close()
        if path.startswith("/api/why/"):
            try:
                event_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_json(400, {"error": "事件编号无效"})
            with DB_LOCK:
                store, _, cx = open_world(self.server.db_path)
                try:
                    chain = cx.chain(event_id)
                    if not chain:
                        return self.send_json(404, {"error": "事件不存在"})
                    return self.send_json(200, chain)
                finally:
                    store.close()
        return self.send_json(404, {"error": "接口不存在"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/chapters":
                limit = 3 * 1024 * 1024
            elif path in ("/api/canon/preview", "/api/import/preview"):
                limit = 400000
            elif path in ("/api/import/cast", "/api/import/relate"):
                limit = 40000
            else:
                limit = 8192
            if length < 0 or length > limit:
                return self.send_json(413, {"error": "请求体过大"})
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "请求 JSON 无效"})
        with DB_LOCK:
            try:
                if path in ("/api/snapshots", "/api/branches", "/api/branches/switch"):
                    if not isinstance(payload, dict):
                        return self.send_json(400, {"error": "请求内容须为对象"})
                    root = self.server.root_db_path
                    if path == "/api/branches/switch":
                        branch_id = payload.get("branch_id")
                        target = root if branch_id is None else branches.branch_path(root, branch_id)
                        store, _, _ = open_world(target)
                        store.close()
                        self.server.db_path = target
                        return self.send_json(200, {"current_db": target, "root_db": root,
                                                    "switched": target != root})
                    if path == "/api/snapshots":
                        result = branches.create_snapshot(root, payload.get("name"))
                    else:
                        result = {"path": branches.create_branch(
                            root, payload.get("snapshot_id"), payload.get("name")),
                            "current_db": self.server.db_path, "switched": False}
                    return self.send_json(200, result)
                store, guard, cx = open_world(self.server.db_path)
                try:
                    if path == "/api/canon/preview":
                        return self.send_json(200, writing.preview_canon(payload))
                    if path == "/api/canon":
                        return self.send_json(200, writing.confirm_canon(store, cx, payload))
                    if path == "/api/canon/inference":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "请求内容须为对象"})
                        return self.send_json(200, {"fact_id": canon_io.record_inference(
                            store, cx, payload.get("content"), payload.get("basis", ""))})
                    if path == "/api/canon/promote":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "请求内容须为对象"})
                        return self.send_json(200, canon_io.promote_inference(
                            store, cx, payload.get("fact_id"), payload.get("to", "author")))
                    if path == "/api/import/preview":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "请求内容须为对象"})
                        result = intake.extract(payload.get("text", ""))
                        result["candidates"] = intake.to_candidates(result)
                        return self.send_json(200, result)
                    if path == "/api/import/commit":
                        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
                            return self.send_json(400, {"error": "候选须为列表"})
                        cands = [c for c in payload["candidates"] if isinstance(c, dict) and c.get("content")]
                        if not cands or len(cands) > 60:
                            return self.send_json(400, {"error": "请选择 1~60 条候选"})
                        tick = store.scalar("SELECT tick FROM world WHERE id=1")
                        written = canon_io.commit_candidates(store, cx, load_ids(store), tick,
                                                             [{"content": str(c["content"])[:500],
                                                               "source": "author", "mutable": 1}
                                                              for c in cands])
                        return self.send_json(200, {"written": written, "count": len(written)})
                    if path == "/api/import/relate":
                        if not isinstance(payload, dict) or not isinstance(payload.get("links"), list):
                            return self.send_json(400, {"error": "关系须为列表"})
                        return self.send_json(200, intake.declare_relations(
                            store, cx, load_ids(store), payload["links"]))
                    if path == "/api/import/cast":
                        if not isinstance(payload, dict) or not isinstance(payload.get("persons"), list):
                            return self.send_json(400, {"error": "人物须为列表"})
                        return self.send_json(200, intake.cast_characters(
                            store, cx, load_ids(store), payload["persons"]))
                    if path in ("/api/player", "/api/player/action"): 
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "请求内容须为对象"})
                        player.ensure_schema(store)
                        if path == "/api/player":
                            return self.send_json(200, player.create(store, cx, payload))
                        return self.send_json(200, player.act(store, cx, payload))
                    if path == "/api/chapters/suggest":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "写作请求须为对象"})
                        return self.send_json(200, writing.suggest_passage(store, payload))
                    if path == "/api/chapters":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "章节内容须为对象"})
                        writing.ensure_schema(store)
                        try:
                            return self.send_json(200, writing.save_chapter(store, payload))
                        except ValueError as exc:
                            store.con.rollback()
                            status = 409 if "其他窗口" in str(exc) else 400
                            return self.send_json(status, {"error": str(exc)})
                    if path == "/api/inject":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "注入内容须为对象"})
                        return self.send_json(200, intervention.inject_event(store, cx, payload))
                    if path == "/api/seal":
                        if not isinstance(payload, dict):
                            return self.send_json(400, {"error": "请求内容须为对象"})
                        return self.send_json(200, projection.seal_state(store, cx, payload.get("label", "")))
                    if path == "/api/step":
                        days = payload.get("days", 10)
                        if not isinstance(days, int) or not 1 <= days <= 365:
                            return self.send_json(400, {"error": "推进天数须为 1~365 的整数"})
                        ids = load_ids(store)
                        seed = store.scalar("SELECT seed FROM world WHERE id=1")
                        sim.run(store, cx, guard, ids, days, seed, verbose=False)
                        return self.send_json(200, snapshot(store))
                    if path == "/api/loans" :
                        result = sim.issue_grain_loan(store, cx, load_ids(store),
                                                      store.scalar("SELECT tick FROM world WHERE id=1"),
                                                      payload.get("amount"), payload.get("term_days", 30))
                        return self.send_json(200, result)
                    if path == "/api/intervene":
                        action = payload.get("action")
                        if action not in ("relief", "reforest"):
                            return self.send_json(400, {"error": "干预类型无效"})
                        amount = payload.get("amount")
                        if not isinstance(amount, (int, float)):
                            return self.send_json(400, {"error": "数量无效"})
                        event_id = intervene(store, cx, action, float(amount))
                        return self.send_json(200, {"event_id": event_id, "state": snapshot(store)})
                    return self.send_json(404, {"error": "接口不存在"})
                finally:
                    store.close()
            except local_llm.ModelUnavailable as exc:
                return self.send_json(503, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                return self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                print(f"[平台错误] {type(exc).__name__}: {exc}")
                return self.send_json(500, {"error": "操作失败，存档未被接受为成功状态"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="灵途自生本地浏览器平台")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--new-world", action="store_true", help="创建存档；文件存在时拒绝覆盖")
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--theme", default="lishi")
    ap.add_argument("--name")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    a = ap.parse_args()
    if a.port < 1 or a.port > 65535:
        ap.error("port 必须在 1~65535 之间")
    if a.new_world:
        create(a.db, a.seed, a.theme, a.name)
    elif not os.path.isfile(a.db):
        print(f"存档不存在：{a.db}")
        print("请先运行：python platform.py --new-world")
        return 2
    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    httpd.db_path = os.path.abspath(a.db)
    httpd.root_db_path = httpd.db_path
    print(f"灵途自生平台已启动：http://{a.host}:{a.port}")
    print(f"存档：{httpd.db_path}")
    print("仅监听本机回环地址；按 Ctrl+C 停止。")
    if a.open:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{a.host}:{a.port}/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n平台已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
