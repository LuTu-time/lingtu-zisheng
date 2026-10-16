# -*- coding: utf-8 -*-
"""
叙事文本生成层的最小占位实现（M105）。

P0 不做 LLM 文风润色 —— 先保证「把事件流翻译成人能读的历史」这件事是通的。
文笔留给后续接模型，结构先立住。
"""
import html
import json
import os

from .base import Calendar


def esc(s):
    return html.escape(str(s), quote=True)


def annual_chronicle(store, year):
    """只从不可变事件读取年度事实，缺证据时不编造结论。"""
    if type(year) is not int or year < 1:
        raise ValueError("年份须为正整数")
    begin = (year - 1) * 120 * 24
    end = year * 120 * 24
    rows = store.all("SELECT id,tick,kind,summary,cause_event_id,effects FROM event "
                     "WHERE tick>? AND tick<=? ORDER BY id", (begin, end))
    kinds = {key: [] for key in ("harvest", "market_price", "tax_levy", "birth",
                                 "migration", "death", "coming_of_age", "disaster", "beast_raid", "annual_balance", "loan_issued", "loan_interest",
                                 "loan_overdue", "loan_repaid")}
    for row in rows:
        if row["kind"] in kinds:
            kinds[row["kind"]].append(row)
    harvest = sum(json.loads(r["effects"]).get("grain_produced", 0) for r in kinds["harvest"])
    prices = [json.loads(r["effects"])["price"] for r in kinds["market_price"]]
    levy = sum(json.loads(r["effects"])["grain_levied"] for r in kinds["tax_levy"])
    balance = json.loads(kinds["annual_balance"][-1]["effects"]) if kinds["annual_balance"] else None
    facts = []
    for kind, label in (("harvest", "收成"), ("market_price", "物价"), ("tax_levy", "赋税"),
                        ("birth", "出生"), ("migration", "迁出"), ("death", "死亡"),
                        ("coming_of_age", "成年"), ("disaster", "灾害"), ("beast_raid", "势力冲突"),
                        ("annual_balance", "人口与势力"), ("loan_issued", "借粮"),
                        ("loan_interest", "借粮利息"), ("loan_overdue", "借粮逾期"),
                        ("loan_repaid", "借粮偿还")):
        evidence = kinds[kind]
        facts.append({"topic": label, "count": len(evidence),
                      "event_ids": [e["id"] for e in evidence],
                      "summary": (f"{len(evidence)} 旬产粮合计 {harvest:.1f}" if kind == "harvest" else
                                  f"{len(evidence)} 次，首末粮价 {prices[0]:.2f} → {prices[-1]:.2f}" if kind == "market_price" and prices else
                                  f"{len(evidence)} 次，转入公仓 {levy:.1f}" if kind == "tax_levy" else
                                  f"在村 {balance['population']} 人，势力劳力 {balance['manpower']}，公仓 {balance['faction_grain']:.1f}" if kind == "annual_balance" and balance else
                                  f"发放本金 {sum(json.loads(e['effects']).get('principal', 0) for e in evidence):.1f}" if kind == "loan_issued" else
                                  f"计息 {sum(json.loads(e['effects']).get('interest', 0) for e in evidence):.1f}" if kind == "loan_interest" else
                                  f"未偿余额记录 {len(evidence)} 条" if kind == "loan_overdue" else
                                  f"偿还本息 {sum(json.loads(e['effects']).get('payment', 0) for e in evidence):.1f}" if kind == "loan_repaid" else
                                  f"{len(evidence)} 条真实记录")})
    return {"year": year, "complete": store.scalar("SELECT tick FROM world WHERE id=1") >= end,
            "facts": facts, "total_events": len(rows)}


def text_chronicle(store, limit=400):
    rows = store.all(
        "SELECT tick, kind, summary, depth, cause_event_id FROM event "
        "ORDER BY tick, id LIMIT ?", (limit,))
    out = []
    for e in rows:
        out.append(f"[{Calendar.stamp(e['tick'])}] ({e['kind']}) {e['summary']}")
    return "\n".join(out)


def _series_html(series, title, color, unit=""):
    n = len(series)
    if n < 2:
        return ""
    vals = [p[1] for p in series]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    W, H, PAD = 960, 150, 30
    pts = []
    for i, v in enumerate(vals):
        x = PAD + (W - 2 * PAD) * i / (n - 1)
        y = H - PAD - (H - 2 * PAD) * (v - lo) / rng
        pts.append(f"{x:.1f},{y:.1f}")
    area = f"{PAD},{H - PAD} " + " ".join(pts) + f" {W - PAD},{H - PAD}"
    return f"""
<div class="chart">
  <div class="ct"><span>{esc(title)}</span><span class="cr">{lo:.2f} → {hi:.2f}{unit}</span></div>
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    <polygon points="{area}" fill="{color}" opacity="0.10"/>
    <polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
    <line x1="{PAD}" y1="{H - PAD}" x2="{W - PAD}" y2="{H - PAD}" stroke="#E4E2DB" stroke-width="1"/>
  </svg>
  <div class="cx"><span>第 0 日</span><span>第 {series[-1][0]} 日</span></div>
</div>"""


CSS = """
:root{--bg:#F7F6F3;--card:#fff;--line:#E4E2DB;--line2:#EFEEE9;--tx:#2C2C2A;--tx2:#6B6A65;
--tx3:#96958E;--pur:#534AB7;--pur-d:#3C3489;--tea:#0F6E56;--red:#A32D2D;--amb:#854F0B;
--red-l:#FCEBEB;--tea-l:#E1F5EE;--amb-l:#FAEEDA;--pur-l:#EEEDFE;--blu:#185FA5;--blu-l:#E6F1FB}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font-size:14px;line-height:1.7;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1060px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:25px;font-weight:600;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:17px;font-weight:600;margin:0}
.sub{color:var(--tx2);font-size:13px;margin:0}
.sec{margin-top:40px}
.hd{display:flex;align-items:baseline;gap:12px;border-bottom:2px solid var(--line);padding-bottom:10px;margin-bottom:18px}
.hd p{color:var(--tx2);font-size:12.5px;margin:0 0 0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:24px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 17px}
.kpi .v{font-size:24px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .k{font-size:12px;color:var(--tx2);margin-top:3px}
.kpi .d{font-size:11.5px;margin-top:4px}
.up{color:var(--red)}.down{color:var(--tea)}.flat{color:var(--tx3)}
.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 8px;margin-bottom:12px}
.ct{display:flex;justify-content:space-between;font-size:12.5px;color:var(--tx2);margin-bottom:4px}
.cr{font-variant-numeric:tabular-nums;color:var(--tx3)}
.cx{display:flex;justify-content:space-between;font-size:11px;color:var(--tx3);padding:0 2px 4px}
.chart svg{width:100%;height:150px;display:block}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{background:#FAF9F6;text-align:left;font-size:12px;color:var(--tx2);font-weight:500;padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid var(--line2);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:none}
td.t{color:var(--tx3);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;background:var(--pur-l);color:var(--pur-d);white-space:nowrap}
.tag.warn{background:var(--amb-l);color:var(--amb)}
.tag.bad{background:var(--red-l);color:var(--red)}
.tag.good{background:var(--tea-l);color:var(--tea)}
.tag.info{background:var(--blu-l);color:var(--blu)}
.chain{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--pur);border-radius:10px;padding:14px 18px}
.chain ol{margin:0;padding-left:20px}
.chain li{margin-bottom:6px;font-size:13.5px}
.chain li b{color:var(--pur-d)}
.note{background:var(--blu-l);border:1px solid #B5D4F4;border-radius:10px;padding:13px 17px;font-size:13px;color:#0C447C;margin-bottom:12px;line-height:1.75}
.ok{background:var(--tea-l);border:1px solid #9FE1CB;border-radius:10px;padding:13px 17px;font-size:13px;color:#04342C;margin-bottom:12px}
.foot{margin-top:50px;padding-top:16px;border-top:1px solid var(--line);color:var(--tx3);font-size:12px}
code{background:var(--line2);padding:1px 5px;border-radius:4px;font-size:12.5px;font-family:ui-monospace,Consolas,monospace}
"""

KIND_LABEL = {
    "genesis": "开端", "harvest": "旬报", "game_scarce": "猎物稀少",
    "game_collapse": "猎物绝迹", "deforest": "林木退化", "shortage": "粮食短缺",
    "birth": "添丁", "migration": "迁出", "death": "死亡", "feud": "纠纷",
    "disaster": "天灾", "beast_raid": "兽袭", "correction": "勘误",
}
KIND_TAG = {
    "game_scarce": "warn", "game_collapse": "bad", "deforest": "warn",
    "shortage": "bad", "migration": "warn", "death": "bad", "feud": "warn",
    "disaster": "bad", "beast_raid": "bad", "birth": "good", "harvest": "info",
}


def html_report(store, st, guard, cx, ids, out_path, seed, days, theme_label):
    sid = ids["settlement_id"]
    tl = st.timeline

    def series(key):
        return [(p["day"], p[key]) for p in tl if key in p and p[key] is not None]

    s_pop, s_mor = series("pop"), series("morale")
    s_grain, s_forest, s_game = series("grain"), series("forest"), series("game")

    def delta_html(key):
        if len(tl) < 2:
            return ""
        a, b = tl[0].get(key), tl[-1].get(key)
        if a is None or b is None:
            return ""
        d = b - a
        cls = "flat" if abs(d) < 1e-9 else ("up" if d > 0 else "down")
        sign = "+" if d > 0 else ""
        return f'<div class="d {cls}">{sign}{d:.2f} 相较开局</div>'

    # 最长因果链
    row = store.one("SELECT id FROM event ORDER BY depth DESC, id DESC LIMIT 1")
    chain = cx.chain(row["id"]) if row else []

    # 事件统计
    kinds = store.all("SELECT kind, COUNT(*) n FROM event GROUP BY kind ORDER BY n DESC")
    total_events = store.scalar("SELECT COUNT(*) FROM event")
    by_depth = cx.stats()

    # 时间线
    events = store.all(
        "SELECT tick, kind, summary, depth FROM event ORDER BY tick DESC, id DESC LIMIT 46")

    rep = guard.report()

    # 历史完整性（第五节要展示，先算好）
    chain_ok, brk, affected = cx.verify_chain()
    anc = cx.verify_anchor()

    P = []
    A = P.append
    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<title>灵途自生 · 推演纪事</title><style>' + CSS + '</style></head><body><div class="wrap">')
    A('<h1>推演纪事 · 无干预世界演化</h1>')
    A(f'<p class="sub">seed={seed}　|　题材 {esc(theme_label)}　|　推演 {days} 个游戏日（1 日 = 24 tick）'
      f'　|　由 engine/ P0 内核自动生成</p>')

    A('<div class="kpis">')
    A(f'<div class="kpi"><div class="v">{tl[-1]["pop"]}</div><div class="k">存活人口</div>'
      + delta_html("pop") + '</div>')
    A(f'<div class="kpi"><div class="v">{tl[-1]["morale"]:.2f}</div><div class="k">民心</div>'
      + delta_html("morale") + '</div>')
    A(f'<div class="kpi"><div class="v">{tl[-1]["grain"]:.0f}</div><div class="k">存粮</div>'
      + delta_html("grain") + '</div>')
    A(f'<div class="kpi"><div class="v">{tl[-1]["forest"]:.2f}</div><div class="k">林木覆盖率</div>'
      + delta_html("forest") + '</div>')
    A(f'<div class="kpi"><div class="v">{tl[-1]["game"]:.2f}</div><div class="k">猎物密度</div>'
      + delta_html("game") + '</div>')
    A(f'<div class="kpi"><div class="v">{total_events}</div><div class="k">事件总数</div>'
      '<div class="d flat">因果链节点</div></div>')
    hist_ok = chain_ok and anc.get("ok")
    A('<div class="kpi"><div class="v">' + ("已核验" if hist_ok else "异常") + '</div>'
      '<div class="k">历史完整性</div>'
      '<div class="d flat">' + ("哈希链与长度锚点均通过" if hist_ok else "见第五节")
      + '</div></div>')
    A('</div>')

    # ---- 曲线 ----
    A('<div class="sec"><div class="hd"><h2>一、指标演化</h2>'
      f'<span></span><p>每 5 日采样，共 {len(tl)} 个点</p></div>')
    A(_series_html(s_pop, "人口", "#534AB7"))
    A(_series_html(s_mor, "民心", "#0F6E56"))
    A(_series_html(s_grain, "存粮", "#185FA5"))
    A(_series_html(s_forest, "林木覆盖率", "#854F0B"))
    A(_series_html(s_game, "猎物密度", "#A32D2D"))
    A('</div>')

    # ---- 因果链 ----
    A('<div class="sec"><div class="hd"><h2>二、最长因果链</h2>'
      f'<span></span><p>深度 {len(chain) - 1} 层（G2 上限 4）</p></div>')
    A('<div class="note">这是引擎自己长出来的链条 —— 没有任何一条规则写着'
      '「砍树会导致人口流失」，它是一层层推出来的。</div>')
    A('<div class="chain"><ol>')
    for e in chain:
        A(f'<li><b>{esc(Calendar.stamp(e["tick"]))}</b>（{esc(KIND_LABEL.get(e["kind"], e["kind"]))}）'
          f'{esc(e["summary"])}</li>')
    A('</ol></div></div>')

    # ---- 事件分布 ----
    A('<div class="sec"><div class="hd"><h2>三、事件分布</h2>'
      f'<span></span><p>按因果链深度：'
      + "　".join(f'第 {k} 层 {v} 个' for k, v in sorted(by_depth.items())) + '</p></div>')
    A('<table><thead><tr><th>事件类型</th><th>数量</th><th>说明</th></tr></thead><tbody>')
    desc = {
        "genesis": "世界起点，因果链的根", "harvest": "每旬汇总的产出与消耗",
        "game_scarce": "猎物密度跌破 0.30", "game_collapse": "猎物密度跌破 0.15",
        "deforest": "林木覆盖率跌破 0.40", "shortage": "当日缺口超过日需求 6%",
        "birth": "民心高于 0.56 且住房充足", "migration": "民心低于 0.42 导致人口外流",
        "death": "年迈、寒冬、缺粮共同作用", "feud": "秩序低于 0.40 时的分粮纠纷",
        "disaster": "天灾，外生事件（因果链起点）", "beast_raid": "猎物稀少时野兽下山",
    }
    for k in kinds:
        tag = KIND_TAG.get(k["kind"], "")
        A(f'<tr><td><span class="tag {tag}">{esc(KIND_LABEL.get(k["kind"], k["kind"]))}</span></td>'
          f'<td class="t">{k["n"]}</td><td>{esc(desc.get(k["kind"], ""))}</td></tr>')
    A('</tbody></table></div>')

    # ---- 护栏 ----
    A('<div class="sec"><div class="hd"><h2>四、护栏触发记录</h2>'
      f'<span></span><p>共拦截 {rep["blocked_total"]} 次</p></div>')
    if rep["blocked_total"] == 0:
        A('<div class="ok">本次推演未触发任何护栏 —— 世界在自己的规则内稳定运转。</div>')
    else:
        A('<div class="ok">护栏不是错误，而是「规则确实在起作用」的证据。'
          '被拦截的变更没有写进世界，只留下记录。</div>')
        A('<table><thead><tr><th>规则</th><th>次数</th><th>说明</th></tr></thead><tbody>')
        rule_desc = {
            "G1": "人口下限锁：聚落人口不得低于 5",
            "G2": "因果链深度上限：超过 4 层的推导降级为日志",
            "G3": "资源再生约束：可再生资源不得为负，超采后再生递减",
            "G4": "事件密度上限：单 tick 事件数封顶",
            "G5": "势力存续保护：至少保留 2 个活跃势力",
            "G6": "死亡因果完整性：无因死亡一律拦截",
            "G7": "设定一致性硬约束：不得与 canon_fact 中的既定事实冲突",
            "G8": "历史不可改写：事件不可修改、不可删除、不可倒填",
        }
        for k, v in sorted(rep["by_rule"].items()):
            A(f'<tr><td><code>{esc(k)}</code></td><td class="t">{v}</td>'
              f'<td>{esc(rule_desc.get(k, ""))}</td></tr>')
        A('</tbody></table>')
    A('</div>')

    # ---- 历史不可改写 ----
    head = anc.get("actual_head") or ""
    A('<div class="sec"><div class="hd"><h2>五、历史不可改写</h2>'
      '<span>事件产生了，就不能修改</span><p>这是真实世界的运行法则</p></div>')
    A('<div class="note">本页记录的不是「作者写了什么」，而是「这个世界发生过什么」。'
      '事件一经写下即为事实：既改不掉（数据库触发器与存储层守卫直接拒绝），'
      '也瞒不住（哈希链逐条重算 + 长度锚点双重校验）。'
      '想改变过去，唯一的办法是在某个历史点上另开一条时间线。</div>')
    A('<table><thead><tr><th style="width:120px">检查项</th><th style="width:80px">结果</th>'
      '<th>说明</th></tr></thead><tbody>')
    chain_tag = "good" if chain_ok else "bad"
    chain_word = "完整" if chain_ok else "断裂"
    chain_why = ("全部吻合" if chain_ok
                 else "不符（断于第 %d 条，波及 %d 条）" % (brk["id"], affected))
    anc_tag = "good" if anc.get("ok") else "bad"
    anc_word = "一致" if anc.get("ok") else "不符"
    anc_why = ("没有缺一截，也没有多出一截" if anc.get("ok") else "历史长度对不上")
    A('<tr><td><b>哈希链</b></td>'
      f'<td><span class="tag {chain_tag}">{chain_word}</span></td>'
      f'<td>{anc["actual_count"]} 条事件首尾相扣，逐条重算指纹与存档{chain_why}</td></tr>')
    A('<tr><td><b>长度锚点</b></td>'
      f'<td><span class="tag {anc_tag}">{anc_word}</span></td>'
      f'<td>声称 {anc["claimed_count"]} 条 / 实际 {anc["actual_count"]} 条 —— {anc_why}</td></tr>')
    A('</tbody></table>')
    A('<div class="note" style="margin-top:10px">链尾指纹 <code>'
      + esc(head[:48]) + '…</code><br>'
      '独立复核：<code>python tools/verify_history.py</code>'
      '　（它会拿真语句去撞这些防线，并故意篡改一份副本看能否被抓出来）</div>')
    A('</div>')

    # ---- 时间线 ----
    A('<div class="sec"><div class="hd"><h2>六、事件时间线</h2>'
      f'<span></span><p>最近 {len(events)} 条（倒序）</p></div>')
    A('<table><thead><tr><th>时刻</th><th>层</th><th>类型</th><th>纪事</th></tr></thead><tbody>')
    for e in events:
        tag = KIND_TAG.get(e["kind"], "")
        A(f'<tr><td class="t">{esc(Calendar.stamp(e["tick"]))}</td>'
          f'<td class="t">{e["depth"]}</td>'
          f'<td><span class="tag {tag}">{esc(KIND_LABEL.get(e["kind"], e["kind"]))}</span></td>'
          f'<td>{esc(e["summary"])}</td></tr>')
    A('</tbody></table></div>')

    A('<div class="foot">由 <code>engine/</code> 的 P0 内核推演产生，'
      '<code>tools/</code> 下无参与。所有状态存于 <code>db/world.db</code>，'
      '关掉程序重开，世界仍是此刻的样子。</div>')
    A('</div></body></html>')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(P))
    return out_path
