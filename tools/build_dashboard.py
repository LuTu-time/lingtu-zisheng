# -*- coding: utf-8 -*-
"""
《灵途自生》审计与覆盖看板生成器
读取 data/modules.json + data/genres.json + data/spec.json，
产出单文件 HTML（无外部依赖）: lingtu-zisheng-audit.html
用法: python tools/build_dashboard.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(ROOT, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


MANUAL_ANOMALIES = [
    ("70（第二次出现）", "M070", "自然灾害动态事件",
     "原清单编号 70 被连续占用两次，属纯编号事故，内容完全重复"),
    ("85", "M084", "人质挟持与交换博弈",
     "与第 84 条同义，合并为一个模块"),
]

RAMP = ["#FFFFFF", "#F3F1FC", "#E8E5F9", "#D5D0F2", "#BDB5E9", "#A199DE", "#8479D2", "#6A5FC5"]
RAMP_TX = ["var(--tx3)", "#3C3489", "#3C3489", "#3C3489", "#fff", "#fff", "#fff", "#fff"]

CSS = """
:root{
  --bg:#F7F6F3; --card:#FFFFFF; --line:#E4E2DB; --line2:#EFEEE9;
  --tx:#2C2C2A; --tx2:#6B6A65; --tx3:#96958E;
  --pur:#534AB7; --pur-l:#EEEDFE; --pur-d:#3C3489;
  --tea:#0F6E56; --tea-l:#E1F5EE;
  --amb:#854F0B; --amb-l:#FAEEDA;
  --red:#A32D2D; --red-l:#FCEBEB;
  --blu:#185FA5; --blu-l:#E6F1FB;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:26px;font-weight:600;margin:0 0 6px;letter-spacing:-0.3px}
h2{font-size:17px;font-weight:600;margin:0 0 4px}
h3{font-size:14px;font-weight:600;margin:0 0 8px}
.sub{color:var(--tx2);font-size:13px;margin:0}
.sec{margin-top:42px}
.sec>.hd{display:flex;align-items:baseline;gap:12px;border-bottom:2px solid var(--line);
  padding-bottom:10px;margin-bottom:18px}
.sec>.hd .n{font-size:12px;color:var(--tx3);font-variant-numeric:tabular-nums}
.sec>.hd p{color:var(--tx2);font-size:12.5px;margin:0;margin-left:auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:26px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.kpi .v{font-size:27px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .k{font-size:12px;color:var(--tx2);margin-top:3px}
.kpi.p .v{color:var(--pur)} .kpi.r .v{color:var(--red)} .kpi.t .v{color:var(--tea)}
.kpi.a .v{color:var(--amb)} .kpi.b .v{color:var(--blu)}
.issue{background:var(--card);border:1px solid var(--line);border-left-width:3px;
  border-radius:10px;padding:14px 18px;margin-bottom:10px}
.issue.p0{border-left-color:var(--red)} .issue.p1{border-left-color:var(--amb)}
.issue.p2{border-left-color:var(--blu)} .issue.p3{border-left-color:var(--tx3)}
.issue.fix{border-left-color:var(--tea)}
.issue .t{font-weight:600;font-size:13.5px;margin-bottom:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.issue .d{color:var(--tx2);font-size:13px;line-height:1.75;word-break:break-word}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;font-weight:500;
  vertical-align:middle;white-space:nowrap}
.t-p0{background:var(--red-l);color:var(--red)} .t-p1{background:var(--amb-l);color:var(--amb)}
.t-p2{background:var(--blu-l);color:var(--blu)} .t-p3{background:var(--line2);color:var(--tx2)}
.t-fix{background:var(--tea-l);color:var(--tea)}
.t-gl{background:var(--tea-l);color:var(--tea)} .t-ro{background:var(--pur-l);color:var(--pur-d)}
.arrow{color:var(--tx3);font-size:12px}
.gcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.gcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.gcard .gt{font-weight:600;font-size:14.5px;margin-bottom:2px}
.gcard .gtags{color:var(--tx3);font-size:11.5px;margin-bottom:12px;line-height:1.5}
.grow{margin-bottom:9px}
.grow .gl{font-size:11.5px;color:var(--tx2);margin-bottom:4px}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{font-size:11.5px;padding:2px 8px;border-radius:6px;background:var(--pur-l);
  color:var(--pur-d);white-space:nowrap;font-variant-numeric:tabular-nums}
.chip.sg{background:var(--amb-l);color:var(--amb);border:1px dashed #EF9F27}
.chip.dis{background:var(--red-l);color:var(--red);text-decoration:line-through}
.chip em{font-style:normal;opacity:.55;margin-right:3px}
table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{background:#FAF9F6;text-align:left;font-size:12px;color:var(--tx2);font-weight:500;
  padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line2);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:none}
td.id{font-variant-numeric:tabular-nums;color:var(--pur-d);font-weight:500;white-space:nowrap}
td.c{color:var(--tx3);font-size:12px}
.sw{display:flex;gap:6px;flex-wrap:wrap;font-size:11.5px}
.sw span{background:var(--line2);color:var(--tx2);border-radius:5px;padding:1px 6px}
.sw span.none{background:transparent;color:var(--tx3);padding:0}
.hmwrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:0}
table.hm{min-width:820px;border:none;border-radius:0}
table.hm th,table.hm td{text-align:center;border-bottom:1px solid var(--line2);padding:7px 6px;font-size:12px}
table.hm thead th{background:#FAF9F6;font-size:11.5px;color:var(--tx2);position:sticky;top:0}
table.hm th.g,table.hm td.g{text-align:left;padding-left:14px;white-space:nowrap;
  position:sticky;left:0;background:var(--card);z-index:1;font-size:12.5px;color:var(--tx2)}
table.hm thead th.g{background:#FAF9F6;z-index:2}
table.hm td.cell{font-variant-numeric:tabular-nums;font-weight:500}
table.hm td.tot{font-variant-numeric:tabular-nums;font-weight:600;color:var(--pur-d);
  border-left:2px solid var(--line)}
.legend{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--tx2);margin-top:12px;flex-wrap:wrap}
.legend i{width:26px;height:16px;border-radius:4px;display:inline-block;border:1px solid var(--line)}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.tools input{flex:1;min-width:200px;padding:8px 12px;border:1px solid var(--line);
  border-radius:9px;font-size:13px;font-family:inherit;background:var(--card);color:var(--tx)}
.tools input:focus{outline:none;border-color:var(--pur)}
.fbtn{padding:7px 13px;border:1px solid var(--line);background:var(--card);border-radius:9px;
  font-size:12.5px;cursor:pointer;font-family:inherit;color:var(--tx2)}
.fbtn.on{background:var(--pur);border-color:var(--pur);color:#fff}
.note{background:var(--blu-l);border:1px solid #B5D4F4;border-radius:10px;
  padding:13px 17px;font-size:13px;color:#0C447C;margin-bottom:12px;line-height:1.75}
.lock{background:var(--red-l);border:1px solid #F09595;border-radius:10px;
  padding:13px 17px;font-size:13px;color:#501313;margin-bottom:12px}
.ok-note{background:var(--tea-l);border:1px solid #9FE1CB;border-radius:10px;
  padding:13px 17px;font-size:13px;color:#04342C;margin-bottom:12px;line-height:1.75}
ol.rules{margin:0;padding-left:20px;color:var(--tx2);font-size:13px}
ol.rules li{margin-bottom:5px}
.grow-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.gbox{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px 16px}
.gbox .gid{font-size:11px;color:var(--pur);font-weight:600;font-variant-numeric:tabular-nums}
.gbox .gn{font-weight:600;font-size:13.5px;margin:1px 0 4px}
.gbox .gd{color:var(--tx2);font-size:12.5px;line-height:1.65}
.stage{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:15px 18px;margin-bottom:10px}
.stage .sh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.stage .sh b{font-size:14.5px}
.stage .sh em{font-style:normal;color:var(--tx3);font-size:12px;font-variant-numeric:tabular-nums}
.stage .gd{color:var(--tx2);font-size:13px;margin-bottom:8px}
.stage .ac{font-size:12.5px;color:var(--tea);background:var(--tea-l);border-radius:7px;
  padding:6px 11px;display:inline-block}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--tx3);font-size:12px}
code{background:var(--line2);padding:1px 5px;border-radius:4px;font-size:12.5px;
  font-family:ui-monospace,Consolas,monospace}
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def heat_style(n):
    idx = 0 if n <= 0 else min(n, len(RAMP) - 1)
    return f'style="background:{RAMP[idx]};color:{RAMP_TX[idx]}"'


def build():
    mods, gen, spec = load("modules.json"), load("genres.json"), load("spec.json")
    modules, genres, cats = mods["modules"], gen["genres"], mods["categories"]
    by_id = {m["id"]: m for m in modules}
    cat_label = {c["key"]: c["label"] for c in cats}
    phase_of = {p["key"]: p for p in mods["phases"]}
    glabel = {g["key"]: g["label"].split(" / ")[0] for g in genres}

    # 阶段（开发阶段 P0~P3）归属
    spec_stage = {}
    for p in spec["phases"]:
        for i in p["modules"]:
            spec_stage[i] = p["key"]

    referenced, on_only = set(), set()
    for g in genres:
        referenced |= set(g["on"]) | set(g.get("simple", []))
        on_only |= set(g["on"])

    orphans = [m for m in modules if m["scope"] == "routed" and m["id"] not in referenced]

    # 每个模块被哪些题材挂载
    holders = {m["id"]: [] for m in modules}
    for g in genres:
        for i in list(g["on"]) + list(g.get("simple", [])):
            if i in holders:
                holders[i].append(g["key"])

    # 重复映射
    merged = []
    for m in modules:
        for extra in m["src"][1:]:
            merged.append((str(extra), m["id"], m["name"], "与前置模块内容重复，已合并"))
    for a in MANUAL_ANOMALIES:
        merged.append(a)
    merged.sort(key=lambda r: int(str(r[0]).split("（")[0])
                if str(r[0]).split("（")[0].isdigit() else 999)

    # 热力图
    heat_rows = []
    for g in genres:
        act = set(g["on"]) | set(g.get("simple", []))
        row = {c["key"]: sum(1 for i in act if by_id[i]["cat"] == c["key"]) for c in cats}
        heat_rows.append((g, row, len(act)))
    col_tot = {c["key"]: sum(r[1][c["key"]] for r in heat_rows) for c in cats}

    P = []
    A = P.append

    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<title>灵途自生 · 覆盖热力图与审计报告</title><style>' + CSS + '</style></head><body><div class="wrap">')
    A('<h1>灵途自生 · 覆盖热力图与审计报告</h1>')
    A('<p class="sub">写实底层逻辑世界观生成引擎　|　v'
      + esc(spec["meta"]["version"]) + '　|　数据版本 '
      + esc(mods["meta"]["version"]) + '　|　' + esc(spec["meta"]["date"]) + '</p>')

    # ---- KPI ----
    A('<div class="kpis">')
    A(f'<div class="kpi p"><div class="v">{len(modules)}</div><div class="k">独立模块</div></div>')
    A(f'<div class="kpi b"><div class="v">{sum(1 for m in modules if m.get("new"))}</div>'
      '<div class="k">其中引擎新增</div></div>')
    A(f'<div class="kpi a"><div class="v">{len(merged)}</div><div class="k">合并的重复条目</div></div>')
    A(f'<div class="kpi t"><div class="v">{len(orphans)}</div><div class="k">孤儿模块</div></div>')
    A(f'<div class="kpi p"><div class="v">{sum(1 for m in modules if m["scope"] == "global")}</div>'
      '<div class="k">全局常驻</div></div>')
    A(f'<div class="kpi b"><div class="v">{len(genres)}</div><div class="k">题材分组</div></div>')
    A('</div>')

    # ---- 一、审计结论 ----
    A('<div class="sec" id="findings"><div class="hd"><h2>一、审计结论</h2>'
      '<span class="n">v0.1 → v1.0</span><p>三个致命问题已全部修复</p></div>')

    A('<div class="issue p0"><div class="t"><span class="tag t-p0">原·严重</span>'
      '编号体系失效，「134 个模块」不成立</div><div class="d">'
      '原清单标注 001~134 连续编号，实际列出 <b>135 条</b>（第 70 条被占用两次）；'
      '其中 096~126 全部 31 条、127~134 全部 8 条，<b>均为 001~095 中已有模块的重新表述</b>，'
      '并非新增功能。去重后真实独立模块 <b>94 个</b>，重复 <b>41 条</b>。<br>'
      '若照抄原编号建表，会产生 40 余条功能重复的表，且编号与功能永远对不上。</div></div>')

    A('<div class="issue fix"><div class="t"><span class="tag t-fix">已修复</span>'
      '重编为 M001~M107，原文编号全量留痕</div><div class="d">'
      '按分类连续重排为 <code>M001~M094</code>（对应去重后的 94 个业务模块），'
      '并新增 <code>M095~M107</code> 共 <b>13 个引擎内核模块</b>（世界时钟、因果链、导演、护栏、'
      '货币、供需总账、生命周期、叙事生成、上帝面板、存档分支等）。'
      '原清单编号存入每条的 <code>src</code> 字段，任意模块可一键回溯到原文第几条。'
      '完整映射见第四节。</div></div>')

    A('<div class="issue p0"><div class="t"><span class="tag t-p0">原·严重</span>'
      '24 个模块是死代码，调度器永远够不着</div><div class="d">'
      '项目核心机制是「题材标签 → 自动加载模块」，但原表中 M073~M084 整块（副本空间、赛博身份、'
      '星际航道、灵气循环、功法武技、妖兽异兽、丹药法器、宗门任务、战场损伤、俘虏处置、人质挟持）'
      '以及赋税、渡口、通信、药品、毒药、陷阱、交通工具、向导雇佣、旅店、食材、风俗、丧葬等模块，'
      '<b>既非全局常驻、也未被任何题材点名</b>。<br>'
      '最刺眼的是：<b>玄幻题材的启用列表里有「预言预兆」「信物羁绊」，却没有功法、灵气、妖兽、丹药、宗门</b>——'
      '一个没有修炼体系的仙侠世界跑不起来。</div></div>')

    A('<div class="issue fix"><div class="t"><span class="tag t-fix">已修复</span>'
      '逐题材补齐挂载，孤儿数 24 → 0</div><div class="d">'
      '为每个题材重新审定启用清单：玄幻补齐 M077~M081 修炼体系，游戏题材挂上 M073 副本空间，'
      '赛博挂上 M074/M075/M076，末日挂上 M085/M087/M091。'
      '当前审计结果：<b>孤儿模块 0 个</b>，调度器无死角。'
      '各题材最终挂载见第三节调度矩阵。</div></div>')

    A('<div class="issue p1"><div class="t"><span class="tag t-p1">原·中等</span>'
      '题材表存在三条不可执行的模糊规则</div><div class="d">')
    for r in gen["meta"]["resolved"]:
        A(f'<b>· {esc(r["issue"])}</b><br>　→ {esc(r["fix"])}<br>')
    A('</div></div>')

    A('<div class="issue fix"><div class="t"><span class="tag t-fix">已修复</span>'
      '二元开关升级为三档调度（on / simple / off）</div><div class="d">'
      '原来的「启用 / 禁用」太粗：悬疑故事不需要种田逻辑，但需要城镇作为舞台。'
      '现在引入中间档 <b>simple —— 实体存在、可被描写，但不启动完整推演循环</b>。'
      '悬疑题材下 M045 聚落城镇、M048 私垦拓殖、M050 粮食仓储 均降为 simple，'
      '既保留舞台又不让生产循环抢走叙事焦点。当前共 '
      + str(sum(len(g.get("simple", [])) for g in genres)) + ' 处简化标记。</div></div>')

    A('<div class="issue fix"><div class="t"><span class="tag t-fix">已修复</span>'
      '「无 RPG 数值面板」与成长体系模块的张力</div><div class="d">'
      '全局约束声明不内置战斗属性与等级，但 M078 功法、M079 妖兽、M080 丹药本质上是成长体系，'
      '落到推演层必然要比较强弱。<br>解决方案：定义 <b>power_scale 相对描述符</b>——'
      '判定输入是影响因子集合（功法层级、器械、人数、地形、状态、士气、外援），'
      '输出是序数而非基数：<b>碾压 / 压制 / 势均力敌 / 不敌 / 悬殊</b>。'
      '因为不产生可累加数值，所以不会退化成等级制。见第七节。</div></div>')
    A('</div>')

    # ---- 二、覆盖热力图 ----
    A('<div class="sec" id="heat"><div class="hd"><h2>二、题材覆盖热力图</h2>'
      f'<span class="n">{len(genres)} × {len(cats)}</span>'
      '<p>单元格 = 该题材在该分类下启用的模块数</p></div>')
    A('<div class="hmwrap"><table class="hm"><thead><tr><th class="g">题材 \\ 分类</th>')
    for c in cats:
        A(f'<th>{esc(c["label"])}</th>')
    A('<th class="tot">合计</th></tr></thead><tbody>')
    for g, row, tot in heat_rows:
        A(f'<tr><td class="g">{esc(g["label"])}</td>')
        for c in cats:
            n = row[c["key"]]
            cls = "cell" + (" zero" if n == 0 else "")
            A(f'<td class="{cls}" {heat_style(n)}>{n if n else "·"}</td>')
        A(f'<td class="tot">{tot}</td></tr>')
    A('<tr><td class="g" style="font-weight:600;color:var(--tx)">合计</td>')
    for c in cats:
        A(f'<td class="tot" style="border-left:none">{col_tot[c["key"]]}</td>')
    A(f'<td class="tot">{sum(col_tot.values())}</td></tr>')
    A('</tbody></table></div>')
    A('<div class="legend"><span>启用模块数：</span>')
    for i, lab in enumerate(["0", "1", "2", "3", "4", "5", "6", "7+"]):
        A(f'<i style="background:{RAMP[i]}"></i><span>{lab}</span>')
    A('</div>')
    A('<div class="note" style="margin-top:12px">读法：横看一行 = 这个题材覆盖了哪些领域，'
      '行末合计 = 该题材启用的模块总数（含简化运行），空白格代表该题材在该分类下没有启用模块。'
      '例如「美食 / 鉴宝」在势力、生态两栏为空属正常（不需要灾害与派系模块）；'
      '而玄幻一行如果在「题材专属」为空，就说明修炼体系没接上——目前该格为 5。<br>'
      '<b>「引擎内核」与「系统调度」两列恒为 0 是正常的</b>：这两类共 '
      + str(sum(1 for m in modules if m["cat"] in ("engine", "core"))) +
      ' 个模块属于全局常驻，由引擎默认加载、不依赖题材标签，因此不出现在任何题材的挂载清单里。</div>')
    A('</div>')

    # ---- 三、调度矩阵 ----
    A('<div class="sec" id="genres"><div class="hd"><h2>三、题材调度矩阵</h2>'
      f'<span class="n">{len(genres)} 组</span>'
      '<p>实心紫＝完整启用 · 橙色虚线＝简化运行 · 红色删除线＝关闭</p></div>')
    A('<div class="gcards">')
    for g in genres:
        on, sm, off = g["on"], g.get("simple", []), g["off"]
        A('<div class="gcard">')
        A(f'<div class="gt">{esc(g["label"])}</div>')
        A(f'<div class="gtags">{" · ".join(esc(t) for t in g["tags"])}</div>')
        A(f'<div class="grow"><div class="gl">完整启用（{len(on)}）</div><div class="chips">')
        for i in on:
            A(f'<span class="chip"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
        A('</div></div>')
        if sm:
            A(f'<div class="grow"><div class="gl">简化运行（{len(sm)}）</div><div class="chips">')
            for i in sm:
                A(f'<span class="chip sg"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
            A('</div></div>')
        if off:
            A(f'<div class="grow"><div class="gl">关闭（{len(off)}）</div><div class="chips">')
            for i in off:
                A(f'<span class="chip dis"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
            A('</div></div>')
        if g.get("canon_pack"):
            A(f'<div class="note" style="margin:4px 0 0;padding:9px 12px;font-size:12px">'
              f'<b>canon 包：</b>{esc(g["canon_pack"])}</div>')
        A('</div>')
    A('</div></div>')

    # ---- 四、重复映射 ----
    A('<div class="sec" id="merge"><div class="hd"><h2>四、原文编号映射表</h2>'
      f'<span class="n">{len(merged)} 条重复</span><p>原编号 → 新编号</p></div>')
    A('<table><thead><tr><th>原编号</th><th>合并至</th><th>模块名称</th><th>说明</th></tr></thead><tbody>')
    for orig, nid, name, why in merged:
        A(f'<tr><td class="c">{esc(orig)}</td><td class="id">{nid}</td>'
          f'<td>{esc(name)}</td><td class="c">{esc(why)}</td></tr>')
    A('</tbody></table>')
    A('<div class="note" style="margin-top:12px">原编号 096~126（31 条）与 127~134（8 条）'
      '为整体重述，上表已逐条列出对应关系。原编号 001~095 中未被列出的条目与 M 编号一一对应，'
      '可直接按 <code>src</code> 字段回溯。</div>')
    A('</div>')

    # ---- 五、开发阶段 ----
    A('<div class="sec" id="stages"><div class="hd"><h2>五、开发阶段路线</h2>'
      f'<span class="n">{len(spec["phases"])} 个阶段</span><p>按顺序推进，不跳阶段</p></div>')
    for p in spec["phases"]:
        ids = p["modules"]
        bycat = {}
        for i in ids:
            bycat[by_id[i]["cat"]] = bycat.get(by_id[i]["cat"], 0) + 1
        dist = "、".join(f'{cat_label[k]} {v}' for k, v in
                        sorted(bycat.items(), key=lambda x: -x[1]))
        A('<div class="stage">')
        A(f'<div class="sh"><b>{esc(p["label"])}</b>'
          f'<em>{len(ids)} 个模块</em></div>')
        A(f'<div class="gd">{esc(p["goal"])}</div>')
        A(f'<div class="gd" style="font-size:12.5px;color:var(--tx3)">模块构成：{esc(dist)}</div>')
        A(f'<div class="ac">验收：{esc(p["acceptance"])}</div>')
        A('</div>')
    A('</div>')

    # ---- 六、护栏 ----
    A('<div class="sec" id="guard"><div class="hd"><h2>六、规则护栏</h2>'
      f'<span class="n">{len(spec["guardrails"]["rules"])} 条</span>'
      '<p>防止推演退化成全灭或无限连锁</p></div>')
    A('<div class="ok-note">' + esc(spec["guardrails"]["note"]) + '</div>')
    A('<div class="grow-grid">')
    for r in spec["guardrails"]["rules"]:
        A(f'<div class="gbox"><div class="gid">{esc(r["id"])}</div>'
          f'<div class="gn">{esc(r["name"])}</div>'
          f'<div class="gd">{esc(r["desc"])}</div></div>')
    A('</div></div>')

    # ---- 七、全局约束 ----
    A('<div class="sec" id="constraints"><div class="hd"><h2>七、全局约束与强弱判定</h2>'
      '<span class="n">底层规则</span><p>全部题材生效</p></div>')
    A('<div class="lock"><b>永久锁定禁用：</b>'
      + '、'.join(esc(x) for x in gen["global_constraints"]["locked_disabled"])
      + '　—— 这三个功能应在代码层硬编码拒绝，不依赖配置。</div>')
    A('<ol class="rules">')
    for r in gen["global_constraints"]["rules"]:
        A(f'<li>{esc(r)}</li>')
    A('</ol>')
    ps = gen["global_constraints"]["power_scale"]
    A('<h3 style="margin-top:18px">强弱判定 · power_scale（替代 RPG 数值面板）</h3>')
    A(f'<div class="note">{esc(ps["note"])}</div>')
    A('<table><thead><tr><th>序数</th><th>描述符</th><th>含义</th></tr></thead><tbody>')
    for lv in ps["levels"]:
        A(f'<tr><td class="id">{lv["lv"]}</td><td><b>{esc(lv["label"])}</b></td>'
          f'<td class="c">{esc(lv["desc"])}</td></tr>')
    A('</tbody></table>')
    A('<div class="note" style="margin-top:12px"><b>判定输入因子：</b>'
      + '、'.join(esc(x) for x in ps["factors"]) + '</div>')
    A('<div class="note" style="margin-top:10px"><b>叙事修饰标签（不改变模块加载）：</b><br>')
    for m in gen["narrative_modifiers"]:
        A(f'· <b>{esc(m["label"])}</b>：{esc(m["effect"])}<br>')
    A('</div></div>')

    # ---- 八、模块总表 ----
    A('<div class="sec" id="modules"><div class="hd"><h2>八、模块总表</h2>'
      f'<span class="n">{len(modules)} 项</span><p>可搜索 / 按分类筛选</p></div>')
    A('<div class="tools">')
    A('<input id="q" placeholder="搜索编号或名称…" oninput="flt()">')
    A('<button class="fbtn on" data-c="all" onclick="pick(this)">全部</button>')
    for c in cats:
        A(f'<button class="fbtn" data-c="{c["key"]}" onclick="pick(this)">{esc(c["label"])}</button>')
    A('</div>')
    A('<table><thead><tr><th>编号</th><th>模块名称</th><th>分类</th><th>类型</th>'
      '<th>批次</th><th>阶段</th><th>挂载题材数</th><th>原编号</th></tr></thead><tbody>')
    for m in modules:
        scope = ('<span class="tag t-gl">全局常驻</span>' if m["scope"] == "global"
                 else '<span class="tag t-ro">题材挂载</span>')
        ph = phase_of[m["phase"]]["label"]
        hs = holders[m["id"]]
        ntex = len(hs)
        st = spec_stage.get(m["id"], "—")
        srcs = "、".join(str(s) for s in m["src"]) or ("<span class='c'>新增</span>")
        A(f'<tr data-c="{m["cat"]}" data-s="{esc(m["id"] + " " + m["name"])}">'
          f'<td class="id">{m["id"]}</td><td>{esc(m["name"])}</td>'
          f'<td class="c">{esc(cat_label[m["cat"]])}</td><td>{scope}</td>'
          f'<td class="c">{esc(ph)}</td><td class="c">{esc(st)}</td>'
          f'<td class="c">{ntex if ntex else "—"}</td>'
          f'<td class="c">{srcs}</td></tr>')
    A('</tbody></table></div>')

    A('<div class="foot">由 <code>tools/build_dashboard.py</code> 从 '
      '<code>data/modules.json</code>、<code>data/genres.json</code>、<code>data/spec.json</code> '
      '自动生成。运行 <code>python tools/validate.py</code> 可在终端复现审计结论。</div>')
    A('</div>')

    A('<script>')
    A('var cat="all";')
    A('function pick(b){document.querySelectorAll(".fbtn").forEach(function(x){x.classList.remove("on")});'
      'b.classList.add("on");cat=b.dataset.c;flt();}')
    A('function flt(){var q=document.getElementById("q").value.trim().toLowerCase();'
      'document.querySelectorAll("#modules tbody tr").forEach(function(r){'
      'var okc=(cat==="all"||r.dataset.c===cat);'
      'var oks=(!q||r.dataset.s.toLowerCase().indexOf(q)>=0);'
      'r.style.display=(okc&&oks)?"":"none";});}')
    A('</script>')
    A('</body></html>')

    out = os.path.join(ROOT, "lingtu-zisheng-audit.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(P))
    return out, len(modules), len(merged), len(orphans)


if __name__ == "__main__":
    p, nm, nmg, nor = build()
    print(f"OK  {p}")
    print(f"    模块 {nm} 个 / 重复映射 {nmg} 条 / 孤儿 {nor} 个")
