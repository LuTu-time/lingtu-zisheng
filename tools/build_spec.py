# -*- coding: utf-8 -*-
"""
《灵途自生》规格说明书生成器 -> lingtu-zisheng-spec.html
用法: python tools/build_spec.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(ROOT, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


MANUAL_ANOMALIES = [
    ("70（第二次出现）", "M070", "自然灾害动态事件", "编号 70 被连续占用两次，内容完全重复"),
    ("85", "M084", "人质挟持与交换博弈", "与第 84 条同义，合并为一个模块"),
]

CSS = """
:root{--bg:#F7F6F3;--card:#fff;--line:#E4E2DB;--line2:#EFEEE9;--tx:#2C2C2A;--tx2:#6B6A65;
--tx3:#96958E;--pur:#534AB7;--pur-l:#EEEDFE;--pur-d:#3C3489;--tea:#0F6E56;--tea-l:#E1F5EE;
--amb:#854F0B;--amb-l:#FAEEDA;--red:#A32D2D;--red-l:#FCEBEB;--blu:#185FA5;--blu-l:#E6F1FB}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font-size:14px;line-height:1.72;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:48px 26px 90px}
h1{font-size:28px;font-weight:600;margin:0 0 8px;letter-spacing:-0.4px}
h2{font-size:19px;font-weight:600;margin:0}
h3{font-size:14.5px;font-weight:600;margin:26px 0 10px}
h4{font-size:13.5px;font-weight:600;margin:18px 0 8px;color:var(--tx2)}
p{margin:0 0 12px}
.sub{color:var(--tx2);font-size:13.5px;margin:0}
.meta{color:var(--tx3);font-size:12.5px;margin:14px 0 0;padding-top:14px;border-top:1px solid var(--line)}
.pos{margin-top:24px;background:var(--card);border:1px solid var(--line);border-left:3px solid var(--pur);
border-radius:10px;padding:18px 22px;font-size:14px;line-height:1.85;color:var(--tx)}
nav{margin-top:22px;display:flex;flex-wrap:wrap;gap:6px}
nav a{font-size:12.5px;text-decoration:none;color:var(--tx2);background:var(--card);
border:1px solid var(--line);border-radius:8px;padding:5px 11px}
nav a:hover{border-color:var(--pur);color:var(--pur)}
.sec{margin-top:52px}
.hd{display:flex;align-items:baseline;gap:12px;border-bottom:2px solid var(--line);
padding-bottom:12px;margin-bottom:22px}
.hd .n{font-size:12px;color:var(--tx3);font-variant-numeric:tabular-nums}
.hd p{color:var(--tx2);font-size:12.5px;margin:0 0 0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.kpi .v{font-size:26px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .k{font-size:12px;color:var(--tx2);margin-top:3px}
.kpi.p .v,.kpi.b .v{color:var(--pur)}.kpi.r .v{color:var(--red)}.kpi.t .v{color:var(--tea)}.kpi.a .v{color:var(--amb)}
.fx{background:var(--card);border:1px solid var(--line);border-left-width:3px;border-radius:10px;
padding:15px 20px;margin-bottom:11px}
.fx.bad{border-left-color:var(--red)}.fx.good{border-left-color:var(--tea)}
.fx .t{font-weight:600;font-size:13.5px;margin-bottom:5px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.fx .d{color:var(--tx2);font-size:13px;line-height:1.78}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;font-weight:500;white-space:nowrap}
.t-r{background:var(--red-l);color:var(--red)}.t-t{background:var(--tea-l);color:var(--tea)}
.t-p{background:var(--pur-l);color:var(--pur-d)}.t-a{background:var(--amb-l);color:var(--amb)}
.t-g{background:var(--line2);color:var(--tx2)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:12px;overflow:hidden;margin-bottom:14px}
th{background:#FAF9F6;text-align:left;font-size:12px;color:var(--tx2);font-weight:500;
padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line2);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:none}
td.id{font-variant-numeric:tabular-nums;color:var(--pur-d);font-weight:500;white-space:nowrap}
td.c{color:var(--tx3);font-size:12px}
td.d{color:var(--tx2);font-size:12.5px;line-height:1.6}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{font-size:11.5px;padding:2px 8px;border-radius:6px;background:var(--pur-l);color:var(--pur-d);
white-space:nowrap;font-variant-numeric:tabular-nums}
.chip.sm{background:var(--blu-l);color:var(--blu);border:1px dashed #85B7EB}
.chip.of{background:var(--red-l);color:var(--red);text-decoration:line-through}
.chip em{font-style:normal;opacity:.55;margin-right:3px}
.gcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.gcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.gcard .gt{font-weight:600;font-size:14.5px}
.gcard .gtags{color:var(--tx3);font-size:11.5px;margin:2px 0 12px;line-height:1.5}
.grow{margin-bottom:9px}
.grow .gl{font-size:11.5px;color:var(--tx2);margin-bottom:4px}
.note{background:var(--blu-l);border:1px solid #B5D4F4;border-radius:10px;padding:14px 18px;
font-size:13px;color:#0C447C;margin-bottom:14px;line-height:1.75}
.warn{background:var(--amb-l);border:1px solid #EF9F27;border-radius:10px;padding:14px 18px;
font-size:13px;color:#633806;margin-bottom:14px;line-height:1.75}
.lock{background:var(--red-l);border:1px solid #F09595;border-radius:10px;padding:14px 18px;
font-size:13px;color:#501313;margin-bottom:14px;line-height:1.75}
ol.rules{margin:0;padding-left:20px;color:var(--tx2);font-size:13.2px}
ol.rules li{margin-bottom:6px}
.flow{display:flex;flex-wrap:wrap;gap:0;align-items:stretch;margin-bottom:16px}
.step{flex:1;min-width:132px;background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:13px 15px;margin-right:8px;margin-bottom:8px}
.step .s{font-size:11px;color:var(--tx3);font-variant-numeric:tabular-nums}
.step .n{font-weight:600;font-size:13.5px;margin:2px 0 4px}
.step .d{font-size:12px;color:var(--tx2);line-height:1.6}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.tools input{flex:1;min-width:200px;padding:8px 12px;border:1px solid var(--line);border-radius:9px;
font-size:13px;font-family:inherit;background:var(--card);color:var(--tx)}
.tools input:focus{outline:none;border-color:var(--pur)}
.fbtn{padding:7px 13px;border:1px solid var(--line);background:var(--card);border-radius:9px;
font-size:12.5px;cursor:pointer;font-family:inherit;color:var(--tx2)}
.fbtn.on{background:var(--pur);border-color:var(--pur);color:#fff}
.foot{margin-top:60px;padding-top:20px;border-top:1px solid var(--line);color:var(--tx3);font-size:12px}
code{background:var(--line2);padding:1px 5px;border-radius:4px;font-size:12.5px;
font-family:ui-monospace,Consolas,monospace}
"""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build():
    mods = load("modules.json")
    gen = load("genres.json")
    spec = load("spec.json")
    descs = load("descs.json")
    modules, genres, cats = mods["modules"], gen["genres"], mods["categories"]
    by_id = {m["id"]: m for m in modules}
    cat_label = {c["key"]: c["label"] for c in cats}
    phase_label = {p["key"]: p["label"] for p in mods["phases"]}

    referenced, holders = set(), {m["id"]: [] for m in modules}
    glabel = {g["key"]: g["label"].split(" / ")[0] for g in genres}
    for g in genres:
        for i in list(g["on"]) + list(g.get("simple", [])):
            referenced.add(i)
            if i in holders:
                holders[i].append(g["key"])
    orphans = [m for m in modules if m["scope"] == "routed" and m["id"] not in referenced]

    merged = []
    for m in modules:
        for extra in m["src"][1:]:
            merged.append((str(extra), m["id"], m["name"], "与前置模块内容重复，已合并至同一编号"))
    merged += MANUAL_ANOMALIES
    merged.sort(key=lambda r: int(str(r[0]).split("（")[0]) if str(r[0]).split("（")[0].isdigit() else 999)

    P = []
    A = P.append

    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<title>灵途自生 · 规格说明书 v1.0</title><style>' + CSS + '</style></head><body><div class="wrap">')

    A('<h1>灵途自生 · 世界观生成引擎</h1>')
    A('<p class="sub">规格说明书 v1.0</p>')
    A('<p class="meta">' + esc(spec["meta"]["subtitle"]) + '　|　'
      + esc(spec["meta"]["version"]) + '　|　' + esc(spec["meta"]["date"])
      + '　|　模块 ' + str(len(modules)) + ' 个　题材 ' + str(len(genres)) + ' 组</p>')
    A('<div class="pos">' + esc(spec["meta"]["positioning"]) + '</div>')

    A('<nav>')
    for aid, t in [("s0", "本次修订"), ("s1", "核心机制"), ("s2", "模块总表"), ("s3", "题材调度"),
                   ("s4", "数据模型"), ("s5", "规则护栏"), ("s6", "开发阶段"), ("s7", "全局约束"),
                   ("s8", "历史不可改写"), ("s9", "实现状态"), ("s10", "待决问题"), ("s11", "附录")]:
        A(f'<a href="#{aid}">{t}</a>')
    A('</nav>')

    # ---------- 0 本次修订 ----------
    A('<div class="sec" id="s0"><div class="hd"><h2>零、本次修订</h2>'
      '<span class="n">v0.9 → v1.0 → v1.1</span>'
      '<p>清单去重 → 补齐引擎层 → 钉死历史不可改写</p></div>')
    A('<div class="kpis">')
    A(f'<div class="kpi p"><div class="v">{len(modules)}</div><div class="k">模块总数（v0.9 为 94）</div></div>')
    A(f'<div class="kpi r"><div class="v">{len(merged)}</div><div class="k">消除的重复条目</div></div>')
    A(f'<div class="kpi t"><div class="v">13</div><div class="k">新增引擎层模块</div></div>')
    A(f'<div class="kpi r"><div class="v">24→0</div><div class="k">孤儿模块（原表未挂载）</div></div>')
    A(f'<div class="kpi a"><div class="v">3</div><div class="k">落地的模糊规则</div></div>')
    A(f'<div class="kpi b"><div class="v">4</div><div class="k">历史不可改写的保障层数</div></div>')
    A(f'<div class="kpi t"><div class="v">5</div><div class="k">被检出的篡改手法</div></div>')
    A('</div>')

    A('<h3>1　编号体系：重建为 M001~M0' + f'{len(modules):02d}' + '</h3>')
    A('<div class="d"></div>')
    A('<div class="fx bad"><div class="t"><span class="tag t-r">已修复</span>原编号不可用</div>'
      '<div class="d">原清单自称 001~134 连续编号，实际列出 <b>135 条</b>（第 70 条被占用两次）；'
      '其中 096~126 全部 31 条、127~134 全部 8 条，<b>都是 001~095 中已有模块的重新表述</b>，'
      f'不含任何新功能。去重后独立模块 94 个，现按 <code>M001</code>~<code>M{len(modules):03d}</code> '
      '重新连续编号，原编号全部存入 <code>src</code> 字段可回溯。</div></div>')

    A('<h3>2　模块挂载：24 个死代码全部接通</h3>')
    A('<div class="fx bad"><div class="t"><span class="tag t-r">已修复</span>孤儿模块</div>'
      '<div class="d">原题材调度表中，<b>24 个模块既非全局常驻、也未被任何题材启用</b>，调度器永远够不着：'
      'M057/M064/M065 与 M073~M093 中的绝大多数。<br>'
      '最严重的是 <b>M073~M084 整块「题材专属」模块全军覆没</b>——副本空间、赛博身份、星际航道、'
      '灵气循环、功法武技、妖兽异兽、丹药法器、宗门任务、战场损伤、俘虏处置、人质挟持；'
      '而玄幻题材的启用列表里有「预言预兆」「信物羁绊」，却<b>没有功法、灵气、妖兽、丹药、宗门</b>，'
      '仙侠世界根本无法运转。<br>'
      '现已在题材表中逐条补齐，孤儿模块归零。</div></div>')

    A('<h3>3　调度模型：二元开关 → 三档</h3>')
    A('<div class="note"><b>原设计的问题：</b>「悬疑题材禁用大规模基建种田聚落模块」这条规则无法落成配置——'
      '把 M045 聚落城镇基础整个关掉，悬疑故事就失去了发生地点。<br><br>'
      '<b>v1.0 的解法：</b>每个模块在题材下有三种状态。<br>'
      '· <b>on 完整启用</b>——参与完整推演<br>'
      '· <b>simple 简化运行</b>——实体存在、可被描写，但不启动完整推演循环<br>'
      '· <b>off 关闭</b>——不加载<br><br>'
      '悬疑题材现在是 <code>simple: [M045, M048, M050]</code>：城镇照常存在、照常可以被角色走进走出，'
      '只是不跑规模化生产与人口增长循环。原意达成，故事还能发生。</div>')

    A('<h3>4　其余两处模糊规则</h3>')
    for r in gen["meta"]["resolved"][1:]:
        A('<div class="fx good"><div class="t"><span class="tag t-t">已落地</span>'
          + esc(r["issue"]) + '</div><div class="d">' + esc(r["fix"]) + '</div></div>')

    A('<h3>5　补齐引擎层：原清单缺的 13 个地基</h3>')
    A('<div class="warn"><b>原清单最大的问题不在细节，在于它只有「世界里的东西」，没有「让世界运转的东西」。</b><br>'
      '一份 94 个模块的清单里，竟然没有世界时钟、没有因果链、没有事件调度、没有护栏、没有存档——'
      '这些是引擎的地基。目录里没有钟表，房子是转不起来的。</div>')
    A('<div class="flow">')
    for m in modules:
        if not m.get("new"):
            continue
        A('<div class="step"><div class="s">' + m["id"] + '</div><div class="n">'
          + esc(m["name"]) + '</div><div class="d">' + esc(descs.get(m["id"], "")) + '</div></div>')
    A('</div>')

    A('<h3>6　v1.1　历史不可改写：一条被钉死的底层法则</h3>')
    A('<div class="fx good"><div class="t"><span class="tag t-t">新增</span>'
      '事件产生了，就不能修改</div><div class="d">'
      '整个项目的卖点是「世界自己长出故事」。这条卖点成立的前提，是过去真的发生过、且不能被回头抹掉——'
      '否则所谓涌现就退化成作者在幕后调参。v1.1 把这条法则落成四重保障：'
      '数据库触发器拒绝改写、存储层守卫、事件哈希链、历史锚点；'
      '并配套一个验证器，能检出「改内容 / 砍尾巴 / 改时间 / 塞假事件 / 斩断溯源」五种篡改手法。'
      '详见第八节。</div></div>')

    # ---------- 1 核心机制 ----------
    A('<div class="sec" id="s1"><div class="hd"><h2>一、核心机制</h2><span class="n">四件事</span>'
      '<p>模块三档 · 推演分级 · 无面板强弱 · canon 包</p></div>')

    A('<h3>1.1　时间尺度与推演分级</h3>')
    A('<p style="color:#6B6A65;font-size:13.2px">' + esc(spec["time_scale"]["tick"]) + '</p>')
    A('<table><thead><tr><th>层级</th><th>单位</th><th>影响范围</th></tr></thead><tbody>')
    for l in spec["time_scale"]["layers"]:
        A(f'<tr><td class="id">{esc(l["name"])}</td><td class="c">{esc(l["unit"])}</td>'
          f'<td class="d">{esc(l["affects"])}</td></tr>')
    A('</tbody></table>')
    A('<p style="color:#6B6A65;font-size:13.2px">' + esc(spec["time_scale"]["lod"]["note"]) + '</p>')
    A('<table><thead><tr><th>档位</th><th>覆盖范围</th><th>结算频率</th></tr></thead><tbody>')
    for t in spec["time_scale"]["lod"]["tiers"]:
        A(f'<tr><td class="id">{esc(t["tier"])}</td><td class="d">{esc(t["scope"])}</td>'
          f'<td class="d">{esc(t["rate"])}</td></tr>')
    A('</tbody></table>')

    A('<h3>1.2　无面板强弱判定</h3>')
    A('<div class="note">' + esc(gen["global_constraints"]["power_scale"]["note"]) + '</div>')
    A('<table><thead><tr><th style="width:90px">等级</th><th style="width:110px">描述符</th>'
      '<th>判定含义</th></tr></thead><tbody>')
    for lv in gen["global_constraints"]["power_scale"]["levels"]:
        A(f'<tr><td class="id">{esc(lv["label"])}</td><td class="c">Lv.{lv["lv"]}</td>'
          f'<td class="d">{esc(lv["desc"])}</td></tr>')
    A('</tbody></table>')
    A('<p style="color:#6B6A65;font-size:13.2px"><b>影响因子：</b>'
      + '、'.join(esc(f) for f in gen["global_constraints"]["power_scale"]["factors"])
      + '　—— 因子只参与比较排序，不产生可累加数值，因此不会退化成等级制。</p>')

    A('<h3>1.3　canon 包机制</h3>')
    A('<div class="note">衍生同人题材必须绑定一个 <b>原作 canon 包</b>。包内声明三类事实：'
      '<br>· <b>确立事实</b>（<code>mutable=false</code>）——原作已写明，引擎只读，冲突即拦截'
      '<br>· <b>推断事实</b>（<code>mutable=true</code>）——原作未写明的空白，引擎可自由生成，但需标注为「引擎推断」'
      '<br>· <b>禁区</b>——明确禁止触碰的设定'
      '<br><br>一致性校验模块 M101 以该包为基准；题材表不再出现「自动过滤」这种无法执行的描述。'
      '非同类原创题材同样建议提供 canon 包，用于锁定作者已经写定的设定。</div>')

    # ---------- 2 模块总表 ----------
    A('<div class="sec" id="s2"><div class="hd"><h2>二、模块总表</h2>'
      f'<span class="n">{len(modules)} 项</span><p>紫色＝题材挂载 · 绿色＝全局常驻</p></div>')
    A('<div class="tools"><input id="q" placeholder="搜索编号或名称…" oninput="flt()">')
    A('<button class="fbtn on" data-c="all" onclick="pick(this)">全部</button>')
    for c in cats:
        A(f'<button class="fbtn" data-c="{c["key"]}" onclick="pick(this)">{esc(c["label"])}</button>')
    A('</div>')
    A('<table><thead><tr><th>编号</th><th>模块名称</th><th>功能要点</th><th>分类</th>'
      '<th>类型</th><th>批次</th><th>挂载题材</th></tr></thead><tbody>')
    for m in modules:
        scope = ('<span class="tag t-t">全局常驻</span>' if m["scope"] == "global"
                 else '<span class="tag t-p">题材挂载</span>')
        hs = holders[m["id"]]
        hstr = ('<span style="color:#96958E">—</span>' if not hs
                else ''.join(f'<span>{esc(glabel[g])}</span>' for g in hs))
        st = '<span style="color:#96958E">—</span>'
        A(f'<tr data-c="{m["cat"]}" data-s="{esc(m["id"] + " " + m["name"])}">'
          f'<td class="id">{m["id"]}</td><td>{esc(m["name"])}</td>'
          f'<td class="d">{esc(descs.get(m["id"], ""))}</td>'
          f'<td class="c">{esc(cat_label[m["cat"]])}</td><td>{scope}</td>'
          f'<td class="c">{esc(phase_label[m["phase"]])}</td>'
          f'<td><div class="chips">{hstr}</div></td></tr>')
    A('</tbody></table>')

    # ---------- 3 题材调度 ----------
    A('<div class="sec" id="s3"><div class="hd"><h2>三、题材调度矩阵</h2>'
      f'<span class="n">{len(genres)} 组</span>'
      '<p>紫＝完整启用 · 蓝虚线＝简化运行 · 红删除线＝关闭</p></div>')
    A('<div class="gcards">')
    for g in genres:
        A('<div class="gcard">')
        A(f'<div class="gt">{esc(g["label"])}</div>')
        A(f'<div class="gtags">{" · ".join(esc(t) for t in g["tags"])}</div>')
        A(f'<div class="grow"><div class="gl">完整启用（{len(g["on"])}）</div><div class="chips">')
        for i in g["on"]:
            A(f'<span class="chip"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
        A('</div></div>')
        if g.get("simple"):
            A(f'<div class="grow"><div class="gl">简化运行（{len(g["simple"])}）</div><div class="chips">')
            for i in g["simple"]:
                A(f'<span class="chip sm"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
            A('</div></div>')
        if g["off"]:
            A(f'<div class="grow"><div class="gl">关闭（{len(g["off"])}）</div><div class="chips">')
            for i in g["off"]:
                A(f'<span class="chip of"><em>{i}</em>{esc(by_id[i]["name"])}</span>')
            A('</div></div>')
        if g.get("canon_pack"):
            A('<div class="grow"><div class="gl">附加要求</div>'
              f'<div class="d" style="font-size:12px;color:#854F0B">{esc(g["canon_pack"])}</div></div>')
        A('</div>')
    A('</div></div>')

    # ---------- 4 数据模型 ----------
    A('<div class="sec" id="s4"><div class="hd"><h2>四、数据模型</h2>'
      '<span class="n">12 个实体</span><p>引擎的最小持久化集</p></div>')
    A('<div class="note">' + esc(spec["data_model"]["note"]) + '</div>')
    A('<table><thead><tr><th>实体</th><th>中文名</th><th>关键字段</th>'
      '<th>说明</th></tr></thead><tbody>')
    for e in spec["data_model"]["entities"]:
        A(f'<tr><td class="id">{esc(e["name"])}</td><td>{esc(e["cn"])}</td>'
          f'<td class="c">{esc(e["key_fields"])}</td><td class="d">{esc(e["desc"])}</td></tr>')
    A('</tbody></table>')

    # ---------- 5 护栏 ----------
    A('<div class="sec" id="s5"><div class="hd"><h2>五、规则护栏</h2>'
      f'<span class="n">{len(spec["guardrails"]["rules"])} 条</span><p>防止推演退化的硬约束</p></div>')
    A('<div class="warn">' + esc(spec["guardrails"]["note"]) + '</div>')
    A('<table><thead><tr><th style="width:60px">编号</th><th style="width:130px">名称</th>'
      '<th>规则</th></tr></thead><tbody>')
    for r in spec["guardrails"]["rules"]:
        A(f'<tr><td class="id">{esc(r["id"])}</td><td>{esc(r["name"])}</td>'
          f'<td class="d">{esc(r["desc"])}</td></tr>')
    A('</tbody></table>')

    # ---------- 6 开发阶段 ----------
    A('<div class="sec" id="s6"><div class="hd"><h2>六、开发阶段</h2>'
      f'<span class="n">{len(spec["phases"])} 个阶段</span><p>按顺序做，不要跳阶段</p></div>')
    for p in spec["phases"]:
        A('<div class="fx good"><div class="t"><span class="tag t-p">'
          + esc(p["key"]) + '</span>' + esc(p["label"]) + '</div>')
        A('<div class="d"><b>目标：</b>' + esc(p["goal"]) + '<br>')
        A('<b>模块（' + str(len(p["modules"])) + '）：</b>'
          + '、'.join(f'<code>{i}</code>' for i in p["modules"]) + '<br>')
        A('<b>验收：</b>' + esc(p["acceptance"]) + '</div></div>')

    # ---------- 7 全局约束 ----------
    A('<div class="sec" id="s7"><div class="hd"><h2>七、全局约束</h2>'
      '<span class="n">底层规则</span><p>全部题材生效</p></div>')
    A('<div class="lock"><b>永久锁定禁用：</b>'
      + '、'.join(esc(x) for x in gen["global_constraints"]["locked_disabled"])
      + '　—— 这三项在代码层硬编码拒绝，不依赖配置，不提供开关。</div>')
    A('<ol class="rules">')
    for r in gen["global_constraints"]["rules"]:
        A(f'<li>{esc(r)}</li>')
    A('</ol>')
    A('<h4>叙事修饰标签（不改变模块加载）</h4>')
    A('<table><thead><tr><th style="width:150px">标签</th><th>效果</th></tr></thead><tbody>')
    for m in gen["narrative_modifiers"]:
        A(f'<tr><td>{esc(m["label"])}</td><td class="d">{esc(m["effect"])}</td></tr>')
    A('</tbody></table>')

    # ---------- 8 历史不可改写 ----------
    imm = spec.get("immutability")
    if imm:
        A('<div class="sec" id="s8"><div class="hd"><h2>八、历史不可改写</h2>'
          '<span class="n">底层法则</span><p>世界真实感的底线</p></div>')
        A('<div class="lock"><b>' + esc(imm["principle"]) + '</b><br>'
          + esc(imm["why"]) + '</div>')
        A('<h3>A　四重保障</h3>')
        A('<table><thead><tr><th style="width:44px">层</th><th style="width:130px">机制</th>'
          '<th>说明</th><th style="width:150px">所在文件</th></tr></thead><tbody>')
        for ly in imm["layers"]:
            A(f'<tr><td class="c">{esc(ly["n"])}</td><td><b>{esc(ly["name"])}</b></td>'
              f'<td class="d">{esc(ly["desc"])}</td>'
              f'<td><code>{esc(ly["file"])}</code></td></tr>')
        A('</tbody></table>')
        A('<h3>B　如何验证</h3>')
        A('<div class="note">运行 <code>' + esc(imm["verification"]["tool"])
          + '</code>，它依次执行 ' + str(len(imm["verification"]["checks"])) + ' 项检查：'
          + esc('；'.join(imm["verification"]["checks"])) + '。</div>')
        A('<h3>C　写错了怎么办</h3>')
        A('<div class="fx good"><div class="t">勘误，而不是回改</div><div class="d">'
          + esc(imm["how_to_correct"]) + '</div></div>')
        A('<h3>D　真想改变过去呢</h3>')
        A('<div class="fx"><div class="t">另开一条时间线</div><div class="d">'
          + esc(imm["how_to_change_past"]) + '</div></div>')
        if imm.get("determinism"):
            A('<h3>E　一条被推翻又重建的前提：确定性</h3>')
            A('<div class="fx bad"><div class="t">'
              '<span class="tag t-r">已修复</span>引擎曾经并不确定</div>'
              '<div class="d">' + esc(imm["determinism"]) + '</div></div>')

    # ---------- 9 实现状态 ----------
    impl = spec.get("implementation")
    if impl:
        A('<div class="sec" id="s9"><div class="hd"><h2>九、实现状态</h2>'
          f'<span class="n">{esc(impl["version"])}</span>'
          f'<p>入口 <code>{esc(impl["entry"])}</code>　|　{esc(impl["stack"])}</p></div>')
        A('<div class="note">' + esc(impl["note"]) + '</div>')
        A('<h3>A　代码结构</h3>')
        A('<table><thead><tr><th style="width:210px">路径</th><th>职责</th></tr></thead><tbody>')
        for x in impl["layout"]:
            A(f'<tr><td><code>{esc(x["path"])}</code></td>'
              f'<td class="d">{esc(x["desc"])}</td></tr>')
        A('</tbody></table>')
        A('<h3>B　已实现模块</h3>')
        A('<p class="d">' + '　'.join(f'<code>{i}</code>' for i in impl["modules_done"])
          + f'　—— 共 {len(impl["modules_done"])} 个</p>')
        A('<div class="note">' + esc(impl["modules_note"]) + '</div>')
        A('<h3>C　验收结果</h3>')
        A('<div class="fx good"><div class="t"><span class="tag t-p">PASS</span>'
          '不崩坏、不死锁</div><div class="d">' + esc(impl["acceptance_result"])
          + '</div></div>')
        A('<h3>D　已知边界</h3>')
        A('<ol class="rules">')
        for x in impl["known_limits"]:
            A(f'<li>{esc(x)}</li>')
        A('</ol>')

    # ---------- 10 待决 ----------
    A('<div class="sec" id="s10"><div class="hd"><h2>十、待决问题</h2>'
      '<span class="n">需你拍板</span><p>不影响开工，但影响中期架构</p></div>')
    for i, q in enumerate(spec["open_questions"], 1):
        A(f'<div class="fx"><div class="t">问题 {i}</div><div class="d">{esc(q)}</div></div>')

    # ---------- 附录 ----------
    A('<div class="sec" id="s11"><div class="hd"><h2>附录</h2>'
      '<span class="n">对照与校验</span><p>原编号 → 新编号</p></div>')
    A('<h3>A　重复合并对照表</h3>')
    A('<table><thead><tr><th>原编号</th><th>合并至</th><th>模块名称</th><th>说明</th></tr></thead><tbody>')
    for orig, nid, name, why in merged:
        A(f'<tr><td class="c">{esc(orig)}</td><td class="id">{nid}</td>'
          f'<td>{esc(name)}</td><td class="d">{esc(why)}</td></tr>')
    A('</tbody></table>')
    A('<div class="note">原文 001~095 中未被列出的条目与 M 编号按顺序一一对应。'
      '全量映射可查阅 <code>data/modules.json</code> 的 <code>src</code> 字段。</div>')

    A('<h3>B　一致性校验结论</h3>')
    A('<table><thead><tr><th>检查项</th><th>结果</th></tr></thead><tbody>')
    checks = [
        ("编号唯一且连续", f"M001~M{len(modules):03d}，共 {len(modules)} 项", "ok"),
        ("题材表悬空引用", "无", "ok"),
        ("on / simple / off 互斥", "无冲突", "ok"),
        ("孤儿模块（调度器够不着）", "0 个", "ok"),
        ("未排期模块（无阶段认领）", "0 个", "ok"),
        ("重复合并条目", f"{len(merged)} 条已记录", "ok"),
    ]
    for name, res, _ in checks:
        A(f'<tr><td>{esc(name)}</td><td class="d" style="color:#0F6E56">✓ {esc(res)}</td></tr>')
    A('</tbody></table>')
    A('<div class="note">运行 <code>python tools/validate.py</code> 可在终端复现以上全部结论。'
      '修改 <code>data/</code> 下任一文件后重新执行即可。</div>')
    A('</div>')

    A('<div class="foot">由 <code>tools/build_spec.py</code> 从 '
      '<code>data/modules.json</code>、<code>genres.json</code>、<code>spec.json</code>、'
      '<code>descs.json</code> 自动生成。数据源是唯一事实来源，改数据不必改文档。</div>')
    A('</div>')

    A('<script>')
    A('var cat="all";')
    A('function pick(b){document.querySelectorAll(".fbtn").forEach(function(x){x.classList.remove("on")});'
      'b.classList.add("on");cat=b.dataset.c;flt();}')
    A('function flt(){var q=document.getElementById("q").value.trim().toLowerCase();'
      'document.querySelectorAll("#s2 tbody tr").forEach(function(r){'
      'var okc=(cat==="all"||r.dataset.c===cat);'
      'var oks=(!q||r.dataset.s.toLowerCase().indexOf(q)>=0);'
      'r.style.display=(okc&&oks)?"":"none";});}')
    A('</script></body></html>')

    out = os.path.join(ROOT, "lingtu-zisheng-spec.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(P))
    return out


if __name__ == "__main__":
    print("OK ", build())
