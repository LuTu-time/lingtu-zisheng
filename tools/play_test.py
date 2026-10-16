# -*- coding: utf-8 -*-
"""Integration checks for continuation, intervention and history safety."""
import json
import os
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import sim, chronicle, ecology
from engine.causality import Causality
from engine.guardrails import Guardrail
from play import create, intervene, load_ids, open_world, snapshot


def advance(path, days):
    store, guard, cx = open_world(path)
    try:
        sim.run(store, cx, guard, load_ids(store), days, 20261015, verbose=False)
        valid, _, _ = cx.verify_chain()
        assert valid and cx.verify_anchor()["ok"]
        return snapshot(store), [tuple(row) for row in store.all(
            "SELECT tick, kind, summary, hash FROM event ORDER BY id")]
    finally:
        store.close()


def dump_state(path):
    with sqlite3.connect(path) as con:
        world = con.execute("SELECT tick,event_count,chain_head FROM world").fetchone()
        stocks = con.execute("SELECT owner_kind,owner_id,resource_type,amount FROM stock ORDER BY id").fetchall()
        loans = con.execute("SELECT principal,balance,issued_tick,due_tick,status,last_settled_tick FROM grain_loan ORDER BY id").fetchall()
        events = con.execute("SELECT tick,kind,summary,hash FROM event ORDER BY id").fetchall()
        return world, stocks, loans, events


def main():
    directory = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "loan-test-" + uuid.uuid4().hex)
    os.makedirs(directory, exist_ok=False)
    try:
        run_tests(directory)
    finally:
        print("测试存档保留于：" + directory)


def run_tests(directory):
        whole = os.path.join(directory, "whole.db")
        chunks = os.path.join(directory, "chunks.db")
        create(whole, 20261015, "lishi", "测试世界")
        create(chunks, 20261015, "lishi", "测试世界")
        final_a, events_a = advance(whole, 30)
        advance(chunks, 10)
        advance(chunks, 10)
        final_b, events_b = advance(chunks, 10)
        assert final_a == final_b, (final_a, final_b)
        assert events_a == events_b, "分段续跑不得重发创世事件或重复日期"

        store, guard, cx = open_world(chunks)
        try:
            before = snapshot(store)
            try:
                intervene(store, cx, "relief", 100)
            except ValueError:
                pass
            else:
                raise AssertionError("储粮不足的干预未被拒绝")
            assert snapshot(store) == before
            eid = intervene(store, cx, "relief", 10)
            after = snapshot(store)
            assert after["势力储粮"] == round(before["势力储粮"] - 10, 1)
            assert after["聚落库存"]["grain"] == round(before["聚落库存"]["grain"] + 10, 1)
            assert store.one("SELECT kind FROM event WHERE id=?", (eid,))["kind"] == "author_relief"
            wood_before = store.scalar("SELECT amount FROM stock WHERE resource_type='wood' AND owner_kind='settlement'")
            forest_before = store.scalar("SELECT forest FROM region LIMIT 1")
            intervene(store, cx, "reforest", 10)
            assert store.scalar("SELECT amount FROM stock WHERE resource_type='wood' AND owner_kind='settlement'") == wood_before - 10
            assert store.scalar("SELECT forest FROM region LIMIT 1") > forest_before
            assert cx.verify_chain()[0] and cx.verify_anchor()["ok"]
            try:
                store.con.execute("UPDATE event SET summary='rewritten' WHERE id=?", (eid,))
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("事件 UPDATE 未被数据库拦截")
            store.con.rollback()
        finally:
            store.close()
        result, _ = advance(chunks, 10)
        assert result["第几日"] == 40
        annual_full = os.path.join(directory, "annual-full.db")
        annual_chunks = os.path.join(directory, "annual-chunks.db")
        create(annual_full, 20261015, "lishi", "年度世界")
        create(annual_chunks, 20261015, "lishi", "年度世界")
        full_state, full_events = advance(annual_full, 120)
        advance(annual_chunks, 40)
        advance(annual_chunks, 40)
        chunk_state, chunk_events = advance(annual_chunks, 40)
        assert full_state == chunk_state and full_events == chunk_events
        store, _, cx = open_world(annual_full)
        try:
            report = chronicle.annual_chronicle(store, 1)
            assert report["complete"]
            facts = {f["topic"]: f for f in report["facts"]}
            assert facts["收成"]["count"] == 12
            assert facts["物价"]["count"] == 12
            assert facts["赋税"]["count"] > 0
            assert facts["人口与势力"]["count"] == 1
            assert all(cx.chain(eid) for topic in ("收成", "物价", "赋税")
                       for eid in facts[topic]["event_ids"])
            assert cx.verify_chain()[0] and cx.verify_anchor()["ok"]
        finally:
            store.close()

        # ---- P1 治理模块（M046/M048/M050/M051/M052/M056/M059/M062/M103）确定性 ----
        civic_kinds = ("household_register", "reclamation", "grain_reserve",
                       "storage_decay", "water_dispute", "trial", "corvee",
                       "community", "burial", "ledger_reconcile")
        with sqlite3.connect(annual_full) as fc, sqlite3.connect(annual_chunks) as cc:
            for k in civic_kinds:
                fcount = fc.execute("SELECT COUNT(*) FROM event WHERE kind=?", (k,)).fetchone()[0]
                ccount = cc.execute("SELECT COUNT(*) FROM event WHERE kind=?", (k,)).fetchone()[0]
                assert fcount == ccount, (k, fcount, ccount)
            for tbl in ("ledger", "household", "grave"):
                frows = fc.execute(f"SELECT * FROM {tbl} ORDER BY id").fetchall()
                crows = cc.execute(f"SELECT * FROM {tbl} ORDER BY id").fetchall()
                assert frows == crows, (tbl, len(frows), len(crows))
        loan_full = os.path.join(directory, "loan-full.db")
        loan_chunks = os.path.join(directory, "loan-chunks.db")
        create(loan_full, 20261015, "lishi", "借贷测试")
        create(loan_chunks, 20261015, "lishi", "借贷测试")
        for path in (loan_full, loan_chunks):
            store, _, cx = open_world(path)
            try:
                sim.run(store, cx, Guardrail(store), load_ids(store), 60, 20261015, verbose=False)
                ids = load_ids(store)
                reserve = store.scalar("SELECT amount FROM stock WHERE owner_kind='faction' AND owner_id=? AND resource_type='grain'", (ids['villager_fid'],))
                amount = min(5.0, max(1.0, reserve / 4))
                sim.issue_grain_loan(store, cx, ids, 60 * 24, amount, 120)
            finally:
                store.close()
        advance(loan_full, 120)
        advance(loan_chunks, 40)
        advance(loan_chunks, 40)
        advance(loan_chunks, 40)
        assert dump_state(loan_full) == dump_state(loan_chunks)
        with sqlite3.connect(loan_full) as full_con, sqlite3.connect(loan_chunks) as chunk_con:
            assert full_con.execute("SELECT id,tick,kind,summary,hash FROM event ORDER BY id").fetchall() == chunk_con.execute("SELECT id,tick,kind,summary,hash FROM event ORDER BY id").fetchall()
        with sqlite3.connect(loan_full) as con:
            loan = con.execute("SELECT principal,balance,status FROM grain_loan").fetchone()
            loan_events = con.execute("SELECT kind,effects FROM event WHERE kind LIKE 'loan_%' ORDER BY id").fetchall()
            assert loan[0] > 0 and loan[1] >= 0 and loan[2] in ('active','overdue','repaid')
            assert any(row[0] == 'loan_interest' for row in loan_events)
            assert all(json.loads(row[1]).get('loan_id') for row in loan_events if row[0] != 'loan_issued')
            assert (loan[2] == 'repaid') == any(row[0] == 'loan_repaid' for row in loan_events)
        repayment_db = os.path.join(directory, "loan-repayment.db")
        create(repayment_db, 20261015, "lishi", "借粮还款测试")
        store, _, cx = open_world(repayment_db)
        try:
            ids = load_ids(store)
            sid, fid = ids["settlement_id"], ids["villager_fid"]
            population = store.scalar("SELECT population FROM settlement WHERE id=?", (sid,))
            reserve = round(population * ecology.FOOD_PER_HEAD * 10, 3)
            settlement = store.one("SELECT id FROM stock WHERE owner_kind='settlement' AND owner_id=? AND resource_type='grain'", (sid,))
            lender = store.one("SELECT id FROM stock WHERE owner_kind='faction' AND owner_id=? AND resource_type='grain'", (fid,))
            issued = sim.issue_grain_loan(store, cx, ids, 0, 10, 10)
            store.con.execute("UPDATE stock SET amount=? WHERE id=?", (reserve + 4, settlement["id"]))
            store.commit()
            lender_before = store.scalar("SELECT amount FROM stock WHERE id=?", (lender["id"],))
            sim.settle_loans(store, cx, ids, 240)
            store.commit()
            repayment = store.one("SELECT balance,status FROM grain_loan WHERE id=?", (issued["loan_id"],))
            assert repayment["status"] == "overdue" and repayment["balance"] == 6.1
            assert abs(store.scalar("SELECT amount FROM stock WHERE id=?", (settlement["id"],)) - reserve) < 1e-8
            assert abs(store.scalar("SELECT amount FROM stock WHERE id=?", (lender["id"],)) - lender_before - 4) < 1e-8
            event = store.one("SELECT effects FROM event WHERE kind='loan_overdue' ORDER BY id DESC LIMIT 1")
            effects = json.loads(event["effects"])
            assert effects["payment"] == 4 and effects["lender_grain"][1] == round(lender_before + 4, 3)
            before_events = cx.count()
            sim.settle_loans(store, cx, ids, 240)
            assert cx.count() == before_events and cx.verify_chain()[0] and cx.verify_anchor()["ok"]
        finally:
            store.close()
        print("PASS: 部分还款保留 10 日口粮、公仓收到还款且不重复结算")
        print("PASS: 一年整跑=三段续跑、收成物价赋税事件可追溯")
        print("PASS: 借粮放款、旬末计息、借据余额与事件链整跑/分段一致")
        print("PASS: 30 日整跑=10+10+10 分段（状态与事件逐条相同）")
        print("PASS: 资源有偿干预、库存不足拦截、事件不可改、历史校验、介入后续跑")


if __name__ == "__main__":
    main()
