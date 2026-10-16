# -*- coding: utf-8 -*-
"""
因果链与事件溯源（M096）。

引擎里没有任何状态可以凭空变化 —— 每一次变更都必须挂在一个事件上，
事件再挂向它的原因。这样「为什么这个村子的人跑光了」永远可以一路追问到
「因为三月初有人多砍了两棵树」。

无因之果不允许建立（全局约束第 7 条）。

------------------------------------------------------------
事件是不可改写的历史记录（状态表尚非事件投影）
------------------------------------------------------------
事件一旦写下，就是**已发生的事实**，不可修改、不可删除。

这不是"约定"，是双重硬约束：
  · 数据库层：event 表的 UPDATE / DELETE 触发器直接 ABORT；
  · 应用层：Store 拒绝对不可变表的一切改写。

在此之上还有第三层 —— **哈希链**。每条事件的指纹 =
    sha256(前一条的指纹 + 本条的全部字段)
于是所有事件首尾相连成一条链：创世 → 今天。改动链上任何一个字符，
从那一条往后所有指纹全部失配，篡改无处藏身。

那写错了怎么办？**勘误，而不是回改。**
追加一条 kind='correction' 的事件指向被更正的那条 —— 原来的错误
仍然留在历史里，因为它是真实发生过的认知。报纸登更正启事，从不重印
昨天的报纸。
"""
import hashlib
import json

from .base import Calendar

# 参与指纹计算的字段。故意包含全部业务字段 —— 少一个就是一个可篡改的口子。
_FIELDS = ("tick", "kind", "place_kind", "place_id", "actor_kind", "actor_id",
           "participants", "cause_event_id", "depth", "effects", "visibility", "summary")


def _canonical(row):
    """把事件行规范化成稳定字节串。sort_keys + 紧凑分隔符保证同内容同指纹。"""
    return json.dumps({k: row.get(k) for k in _FIELDS},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(prev_hash, row):
    blob = (prev_hash or "") + "\u241f" + _canonical(row)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Causality:
    def __init__(self, store, guard, log=None):
        self.store = store
        self.guard = guard
        self.log = log or (lambda msg: None)
        self.suppressed = 0
        self.emitted = 0
        self._last_hash = None        # 链尾指纹缓存；None 表示尚未从库中加载
        self._count = None            # 已发生事件条数缓存

    # ---- 链 ----
    def tail_hash(self):
        if self._last_hash is None:
            r = self.store.one("SELECT hash FROM event ORDER BY id DESC LIMIT 1")
            self._last_hash = (r["hash"] if r and r["hash"] else "")
        return self._last_hash

    def count(self):
        if self._count is None:
            self._count = self.store.scalar("SELECT COUNT(*) FROM event", default=0) or 0
        return self._count

    # ---- 锚点 ----
    def anchor(self):
        """历史走到哪了：条数 + 链尾指纹。"""
        return {"count": self.count(), "head": self.tail_hash()}

    def _set_anchor(self):
        """
        把「历史一共走了这么远、最后停在哪个指纹」写进世界表。

        为什么链尾指纹还不够：哈希链是**向前链** —— 每条事件的指纹依赖前一条。
        砍掉末尾几条，剩下部分的指纹依然全部自洽，链校验会通过。
        所以必须另外独立地记一笔条数，尾部截断才藏不住。
        """
        if self.store.scalar("SELECT COUNT(*) FROM world") == 0:
            return
        self.store.con.execute(
            "UPDATE world SET event_count = ?, chain_head = ? WHERE id = 1",
            (self.count(), self.tail_hash()))

    def verify_anchor(self):
        """比对「声称的历史长度与链尾」和「库里的实际情况」。"""
        r = self.store.one("SELECT event_count, chain_head FROM world WHERE id = 1")
        if not r:
            return {"ok": False, "reason": "世界表缺少锚点字段"}
        actual_n = self.store.scalar("SELECT COUNT(*) FROM event", default=0) or 0
        actual_head = self.store.scalar(
            "SELECT hash FROM event ORDER BY id DESC LIMIT 1") or ""
        claimed_n = r["event_count"] or 0
        claimed_head = r["chain_head"] or ""
        return {
            "ok": claimed_n == actual_n and claimed_head == actual_head,
            "claimed_count": claimed_n, "actual_count": actual_n,
            "claimed_head": claimed_head, "actual_head": actual_head,
            "truncated": actual_n < claimed_n,      # 末尾被砍
            "appended": actual_n > claimed_n,       # 被塞了东西进来
            "head_moved": claimed_head != actual_head,
        }

    def emit(self, tick, kind, summary, cause=None, effects=None,
             place_kind="none", place_id=None,
             actor_kind="engine", actor_id=None,
             participants=None, visibility="public"):
        """
        发布一个事件。cause 为上游事件 id。返回新事件 id，被护栏拦截时返回 None。
        """
        depth = 0
        if cause:
            row = self.store.one("SELECT depth FROM event WHERE id = ?", (cause,))
            depth = (row["depth"] + 1) if row else 0

        if not self.guard.allow_depth(depth, kind):
            self.suppressed += 1
            return None
        if not self.guard.allow_append(tick, kind):
            self.suppressed += 1
            return None
        if not self.guard.allow_event(tick):
            self.suppressed += 1
            return None

        row = {
            "tick": tick,
            "kind": kind,
            "place_kind": place_kind,
            "place_id": place_id,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "participants": json.dumps(participants or [], ensure_ascii=False),
            "cause_event_id": cause,
            "depth": depth,
            "effects": json.dumps(effects or {}, ensure_ascii=False),
            "visibility": visibility,
            "summary": summary,
        }
        prev = self.tail_hash()
        row["prev_hash"] = prev
        row["hash"] = digest(prev, row)

        n_before = self.count()          # 在插入之前取基线，否则会把新行数两遍
        eid = self.store.insert("event", row)
        self._last_hash = row["hash"]
        self._count = n_before + 1
        self.emitted += 1
        self._set_anchor()
        return eid

    def amend(self, event_id, tick, reason, correction, actor_kind="engine", actor_id=None):
        """
        勘误：对已发生的事实做更正。

        注意它**不修改**任何东西 —— 只是追加一条 correction 事件，指向被更正的那条。
        原来的记录原样留在链上，因为「当时的认知」本身也是真实发生过的事。
        """
        return self.emit(
            tick, "correction",
            f"勘误：{reason}",
            cause=event_id,
            effects={"amends_event": event_id, "correction": correction},
            place_kind="none", actor_kind=actor_kind, actor_id=actor_id)

    # ---- 校验 ----
    def verify_chain(self):
        """
        重算整条哈希链，与库中存档逐条比对。

        返回 (是否完整, 首个断点, 受影响条数)。
        用「期望指纹」往下滚，而不是用库里存的指纹 —— 这样一旦某条被改，
        从它往后的整段都会失配，篡改范围一目了然。
        """
        rows = self.store.all("SELECT * FROM event ORDER BY id")
        expect_prev, first_break, affected = "", None, 0
        for r in rows:
            d = dict(r)
            want = digest(expect_prev, d)
            if d.get("hash") != want or (d.get("prev_hash") or "") != expect_prev:
                affected += 1
                if first_break is None:
                    first_break = {
                        "id": d["id"], "tick": d["tick"], "summary": d["summary"],
                        "stored": d.get("hash"), "expect": want,
                        "stored_prev": d.get("prev_hash"), "expect_prev": expect_prev,
                    }
            expect_prev = want
        return (affected == 0), first_break, affected

    def chain(self, event_id, limit=8):
        """向上回溯因果链，返回从根因到当前事件的列表。"""
        out, cur, seen = [], event_id, set()
        while cur and cur not in seen and len(out) < limit:
            seen.add(cur)
            row = self.store.one(
                "SELECT id, tick, kind, summary, depth, cause_event_id FROM event WHERE id = ?",
                (cur,))
            if not row:
                break
            out.append(dict(row))
            cur = row["cause_event_id"]
        out.reverse()
        return out

    def format_chain(self, event_id):
        parts = []
        for e in self.chain(event_id):
            parts.append(f"〔{Calendar.stamp(e['tick'])}〕{e['summary']}")
        return "  →  ".join(parts)

    def root_of(self, event_id):
        c = self.chain(event_id)
        return c[0] if c else None

    def stats(self):
        rows = self.store.all(
            "SELECT depth, COUNT(*) AS n FROM event GROUP BY depth ORDER BY depth")
        return {r["depth"]: r["n"] for r in rows}
