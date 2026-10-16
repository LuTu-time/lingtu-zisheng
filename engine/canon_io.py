# -*- coding: utf-8 -*-
"""
设定导入与一致性校验（P1 系统调度层）：

  M100 设定导入与 canon 解析
  M101 一致性校验与冲突提示

M100 的设计取舍（按用户 2026-10-15 的裁定）：
  · 输入支持「结构化 JSON」与「TXT 原文」两种；TXT 按行解析为设定事实。
  · 解析结果先作为「候选」返回，作者确认后才正式写入 canon_fact —— 不经确认不落库。
  · 默认世界背景（村名、地利、民风）在创世时一次性播种，幂等、不可改（mutable=0），
    作为后续推演不得违背的「既定事实」（G7 的判据）。

M101：
  · 周期性（每年）与按需两种入口。
  · 目前做得到的一致性校验：检测 canon_fact 中的重复事实、以及 mutable=0 的只读事实
    是否与其余事实明显撞车；命中则发 consistency_flag 事件（被动提示），并供 API 出报告。
  · 不篡改任何事实 —— 只提示，改不改由作者定。
"""
import json
import re


# ---------- 设定三态（canon 第三态：推断事实）----------
# 一条设定事实按「硬度」分三态：
#   canon   原作明确写出      —— 只读，最高，任何来源都不得覆盖
#   author  作者确认          —— 只读，次高
#   inference 引擎推断/暂定   —— 可改写；当上位（原作/作者）给出结论时让位
# 判定「硬事实」的判据与数据库触发器一致：mutable=0 或 source='canon'。
# 推断事实用 source='engine' + mutable=1 + note 以「推断：」开头标记。
INFERENCE_PREFIX = "推断："


def fact_state(source, mutable, note=""):
    """把一条 canon_fact 归入 canon / author / inference / background / contract / other。"""
    source = source or ""
    note = note or ""
    if source == "canon":
        return "canon"
    if source == "author":
        return "author"
    if source == "engine":
        if note.startswith(INFERENCE_PREFIX) or (mutable and note == "开局地权分配"):
            return "inference"
        if note.startswith("契约类型"):
            return "contract"
        return "background"
    return "other"


def _subject(content):
    """极简主语抽取：截到第一个「是/为」前，去掉否定残留，限长 12。"""
    return re.split(r"[是为]", content)[0].strip().rstrip("不非无没").strip()[:12]


def is_hard(source, mutable):
    """硬事实 = 受触发器保护、不可改写的那类（mutable=0 或 source='canon'）。"""
    return bool(mutable == 0 or source == "canon")


def record_inference(store, cx, content, basis="", tick=None, ids=None):
    """记录一条「推断事实」：引擎/作者的暂定设定，可被上位覆盖。返回 canon_fact id。"""
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 500:
        raise ValueError("推断内容须为 1~500 字")
    if not isinstance(basis, str) or len(basis) > 200:
        raise ValueError("推断依据最多 200 字")
    if tick is None:
        tick = store.scalar("SELECT tick FROM world WHERE id=1")
    content = content.strip()
    note = INFERENCE_PREFIX + (basis.strip() or "引擎推断")
    fid = store.insert("canon_fact", {
        "content": content, "source": "engine", "mutable": 1,
        "created_tick": tick, "note": note})
    cx.emit(tick, "canon_inference", f"记录一条推断事实（可被原作/作者覆盖）：{content}",
            effects={"fact_id": fid, "basis": note},
            place_kind="settlement", place_id=(ids or {}).get("settlement_id"),
            actor_kind="engine")
    store.commit()
    return fid


def promote_inference(store, cx, fact_id, to="author"):
    """把一条推断事实提升为作者确认（或原作只读）。只能从推断提升。"""
    if to not in ("author", "canon"):
        raise ValueError("只能提升为 author 或 canon")
    row = store.one("SELECT id,content,source,mutable,note FROM canon_fact WHERE id=?", (fact_id,))
    if not row:
        raise ValueError("设定不存在")
    if fact_state(row["source"], row["mutable"], row["note"]) != "inference":
        raise ValueError("只有「推断事实」才能提升；原作/作者确认的设定无需提升")
    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    try:
        store.con.execute("UPDATE canon_fact SET source=?, mutable=0, note=? WHERE id=?",
                          (to, f"由推断提升（原：{row['note']}）", fact_id))
    except Exception:
        store.con.rollback()
        raise
    cx.emit(tick, "canon_promoted",
            f"推断事实提升为{'原作' if to == 'canon' else '作者确认'}：{row['content']}",
            effects={"fact_id": fact_id, "to": to}, actor_kind="author")
    store.commit()
    return {"fact_id": fact_id, "state": "canon" if to == "canon" else "author"}


# ---------- M100：候选解析 ----------
def parse_setting(raw, fmt="auto"):
    """
    把作者提供的设定解析为「候选事实」列表（不落库）。
    每个候选：{"content": str, "source": "canon"|"author", "mutable": 0|1}

    返回 {"candidates": [...], "errors": [...]}
    """
    candidates, errors = [], []
    fmt = fmt or "auto"

    if fmt == "auto":
        fmt = "json" if (isinstance(raw, (dict, list)) or
                         (isinstance(raw, str) and raw.lstrip().startswith(("{", "[")))) else "txt"

    if fmt == "json":
        try:
            data = raw if isinstance(raw, (dict, list)) else json.loads(raw)
        except Exception as exc:
            return {"candidates": [], "errors": [f"JSON 解析失败：{exc}"]}
        if isinstance(data, dict):
            items = data.get("facts") or [data]
        else:
            items = data
        for it in items:
            if isinstance(it, str):
                candidates.append(_line_to_candidate(it))
            elif isinstance(it, dict):
                content = it.get("content") or it.get("fact") or it.get("文本")
                if not content:
                    errors.append("缺少 content 字段，已跳过")
                    continue
                src = "canon" if str(it.get("source", "author")).lower() in ("canon", "原作") else "author"
                mut = 0 if (it.get("mutable", 1) == 0 or src == "canon") else 1
                candidates.append({"content": str(content), "source": src, "mutable": mut})
            else:
                errors.append(f"无法识别的条目类型：{type(it).__name__}")
    else:  # txt：每行一条
        lines = raw.splitlines() if isinstance(raw, str) else []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            candidates.append(_line_to_candidate(ln))

    # 去重（同一 content 只保留一条候选）
    seen, uniq = set(), []
    for c in candidates:
        if c["content"] in seen:
            continue
        seen.add(c["content"])
        uniq.append(c)
    return {"candidates": uniq, "errors": errors}


def _line_to_candidate(line):
    """TXT 单行解析：标注【不可改】的视为原作只读事实。"""
    line = line.strip().lstrip("-•·").strip()
    if "【不可改】" in line or "【原作】" in line or "【只读】" in line:
        content = re.sub(r"【(?:不可改|原作|只读)】", "", line).strip()
        return {"content": content, "source": "canon", "mutable": 0}
    return {"content": line, "source": "author", "mutable": 1}


def commit_candidates(store, cx, ids, tick, candidates):
    """
    作者确认后，把候选事实写入 canon_fact（M100）。
    返回写入的 canon_fact id 列表。原作来源（source='canon'）一律强制只读（mutable=0）。
    """
    sid = ids.get("settlement_id")
    written = []
    for c in candidates:
        source = "canon" if c.get("source") == "canon" else "author"
        mutable = 0 if source == "canon" else int(bool(c.get("mutable", 1)))
        fid = store.insert("canon_fact", {
            "content": c["content"], "source": source, "mutable": mutable,
            "created_tick": tick, "note": "作者设定导入"})
        written.append(fid)
    if written:
        names = [c["content"] for c in candidates[:3]]
        cx.emit(tick, "canon_import",
                f"作者导入 {len(written)} 条设定事实" +
                (f"（含 {sum(1 for c in candidates if c['source']=='canon')} 条原作只读）"
                 if any(c["source"] == "canon" for c in candidates) else ""),
                effects={"count": len(written), "samples": names},
                place_kind="settlement", place_id=sid, actor_kind="author")
    return written


def seed_default_canon(store, ids, tick):
    """
    创世时一次性播种世界背景设定（幂等）。
    这些是不可改的「既定事实」，作为后续推演不得违背的底（G7 判据）。
    仅当尚无 engine 创世背景设定时播种，保证续跑/多段推进不被重复写入
    （每次 run() 都会调用本函数，必须以自身写入的 engine 背景为幂等判据，
     而非 canon/author —— 否则每段推进都重复插入一遍相同背景，触发 M101 误报）。
    """
    if store.scalar("SELECT 1 FROM canon_fact WHERE source='engine' AND note='创世背景设定' LIMIT 1"):
        return 0
    sid = ids.get("settlement_id")
    region = store.one("SELECT name, terrain FROM region WHERE id=?", (ids["region_id"],))
    settle = store.one("SELECT name FROM settlement WHERE id=?", (sid,))
    world = store.one("SELECT name FROM world WHERE id=1")
    backdrop = [
        f"{world['name']}是一处世外河谷小村，依{region['terrain']}而居",
        f"村名{settle['name']}，村民世代以耕种狩猎为生",
        "此地重乡邻情义，遇有灾荒则互济口粮",
        "村中尊长老、信土地，遇事多凭乡约公议",
    ]
    for i, text in enumerate(backdrop):
        store.insert("canon_fact", {
            "content": text, "source": "engine", "mutable": 0,
            "created_tick": tick, "note": "创世背景设定"})
    return len(backdrop)


# ---------- M101：一致性校验 ----------
def consistency_report(store):
    """
    扫描 canon_fact，找出潜在的设定冲突。
    当前规则：
      1. 重复事实：两条 content 完全相同（疑似冗余/矛盾录入）。
      2. 只读事实撞车：两条 mutable=0 的事实，若主语相同却谓语相悖，提示人工复核。
    返回 {"ok": bool, "conflicts": [{"type":..., "detail":...}], "checked": int}
    """
    facts = store.all(
        "SELECT id, content, source, mutable, note FROM canon_fact ORDER BY id")
    conflicts, notes = [], []
    # 1. 重复
    seen = {}
    for f in facts:
        key = f["content"].strip()
        if key in seen:
            conflicts.append({"type": "duplicate", "detail":
                              f"设定重复：#{seen[key]} 与 #{f['id']} 内容相同"})
        else:
            seen[key] = f["id"]
    # 2. 主语相同、谓语相悖（极简启发式：都含「是/为」，但结论词互斥）
    #    只在「硬事实」之间判定 —— 推断事实（可改写、让位于上位）不参与，避免误报。
    POS, NEG = ("是", "为", "属"), ("不是", "不为", "非", "无", "没有")
    hard = [f for f in facts if is_hard(f["source"], f["mutable"])]
    by_subject = {}
    for f in hard:
        subj = _subject(f["content"])
        if len(subj) < 4:
            continue
        # 先判否定，再判肯定：否则「不是」会被「是」抢先判成正向（旧实现的正/反永不触发）。
        if any(n in f["content"] for n in NEG):
            pol = "neg"
        elif any(p in f["content"] for p in POS):
            pol = "pos"
        else:
            pol = None
        if pol is None:
            continue
        by_subject.setdefault(subj, []).append((f["id"], pol))
    for subj, lst in by_subject.items():
        if any(p == "pos" for _, p in lst) and any(p == "neg" for _, p in lst):
            ids_hit = [i for i, _ in lst]
            conflicts.append({"type": "contradiction", "detail":
                              f"主语「{subj}…」存在正/反相悖的设定（#{ids_hit[0]} 等）"})
    # 3. 推断让位提示（非违规）：某推断与一条硬事实主语相同，可考虑提升或改写。
    hard_subjects = {_subject(f["content"]) for f in hard}
    hard_subjects.discard("")
    for f in facts:
        if fact_state(f["source"], f["mutable"], f["note"]) != "inference":
            continue
        subj = _subject(f["content"])
        if len(subj) >= 4 and subj in hard_subjects:
            notes.append({"type": "inference_outranked", "detail":
                          f"推断 #{f['id']} 与既有硬设定主语「{subj}…」相同，可考虑提升或改写"})
    return {"ok": len(conflicts) == 0, "conflicts": conflicts, "notes": notes,
            "checked": len(facts)}


def check_consistency(store, cx, ids, tick):
    """
    周期性被动校验（每年）。仅当有冲突时发出提示事件（不篡改任何事实）。
    返回 consistency_report 的结果。
    """
    rep = consistency_report(store)
    if not rep["ok"]:
        samples = "; ".join(c["detail"] for c in rep["conflicts"][:3])
        cx.emit(tick, "consistency_flag",
                f"设定一致性巡检发现 {len(rep['conflicts'])} 处疑似冲突，请作者复核：{samples}",
                effects={"conflicts": len(rep["conflicts"])},
                place_kind="settlement", place_id=ids.get("settlement_id"),
                actor_kind="engine")
    return rep
