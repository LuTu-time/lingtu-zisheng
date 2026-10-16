# -*- coding: utf-8 -*-
"""
《灵途自生》规格一致性审计器
读取 data/modules.json + data/genres.json + data/spec.json，输出结构性问题清单。
用法: python tools/validate.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(ROOT, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


MANUAL_ANOMALIES = [
    ("70（第二次出现）", "M070", "原清单编号 70 被连续占用两次，纯编号事故"),
    ("85", "M084", "与第 84 条「人质挟持与交换博弈」同义，已合并"),
]


def audit():
    mods = load("modules.json")
    gen = load("genres.json")
    spec = load("spec.json")
    modules = mods["modules"]
    genres = gen["genres"]

    ids = [m["id"] for m in modules]
    known = set(ids)
    issues = []

    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        issues.append(("P0", "编号不唯一", f"重复的模块编号：{', '.join(dup)}"))
    seq = sorted(int(i[1:]) for i in ids)
    if seq != list(range(1, len(ids) + 1)):
        missing = sorted(set(range(1, len(ids) + 1)) - set(seq))
        issues.append(("P0", "编号不连续", f"缺失编号：{missing}"))

    for g in genres:
        for field in ("on", "simple", "off"):
            bad = [i for i in g.get(field, []) if i not in known]
            if bad:
                issues.append(("P0", f"{g['label']} 悬空引用",
                               f"{field} 中的 {bad} 在模块表中不存在"))

    for g in genres:
        on, sm, off = set(g["on"]), set(g.get("simple", [])), set(g["off"])
        for a, b, na, nb in ((on, sm, "on", "simple"), (on, off, "on", "off"), (sm, off, "simple", "off")):
            both = sorted(a & b)
            if both:
                issues.append(("P0", f"{g['label']} 状态冲突",
                               f"{na} 与 {nb} 同时包含：{both}"))

    referenced = set()
    for g in genres:
        referenced |= set(g["on"]) | set(g.get("simple", []))
    orphans = [m for m in modules if m["scope"] == "routed" and m["id"] not in referenced]
    if orphans:
        issues.append((
            "P0", "孤儿模块",
            f"{len(orphans)} 个模块既非全局常驻，也未被任何题材挂载 → 调度器永远不会加载："
            + "、".join(f"{m['id']} {m['name']}" for m in orphans)))

    staged = set()
    for p in spec["phases"]:
        for i in p["modules"]:
            if i not in known:
                issues.append(("P0", f"{p['label']} 引用不存在的模块", i))
            staged.add(i)
    unstaged = [m for m in modules if m["id"] not in staged]
    if unstaged:
        issues.append((
            "P1", "未排期模块",
            f"{len(unstaged)} 个模块没有出现在任何一个开发阶段中："
            + "、".join(f"{m['id']} {m['name']}" for m in unstaged)))

    solo = []
    for m in modules:
        if m["scope"] == "global":
            continue
        hit = sum(1 for g in genres if m["id"] in set(g["on"]) | set(g.get("simple", [])))
        if hit == 1:
            solo.append(f"{m['id']} {m['name']}")
    if solo:
        issues.append((
            "P3", "题材强绑定模块（正常现象，仅提示复用率）",
            f"{len(solo)} 个模块只被单一题材挂载"))

    merged = sum(max(0, len(m["src"]) - 1) for m in modules) + len(MANUAL_ANOMALIES)
    genres_simple = sum(len(g.get("simple", [])) for g in genres)

    return {
        "modules": len(modules),
        "genres": len(genres),
        "global": sum(1 for m in modules if m["scope"] == "global"),
        "routed": sum(1 for m in modules if m["scope"] == "routed"),
        "new": sum(1 for m in modules if m.get("new")),
        "orphans": len(orphans),
        "merged": merged,
        "genres_simple": genres_simple,
        "issues": issues,
    }


if __name__ == "__main__":
    r = audit()
    print("=" * 64)
    print("《灵途自生》规格一致性审计 v1.0")
    print("=" * 64)
    print(f"独立模块        : {r['modules']}  （其中新增引擎层 {r['new']} 个）")
    print(f"  全局常驻      : {r['global']}")
    print(f"  题材挂载      : {r['routed']}")
    print(f"题材分组        : {r['genres']}")
    print(f"简化运行标记    : {r['genres_simple']} 处")
    print(f"重复合并条目    : {r['merged']}")
    print(f"孤儿模块        : {r['orphans']} 个")
    print("-" * 64)
    if not r["issues"]:
        print("未发现结构性问题。")
    for level, title, detail in r["issues"]:
        print(f"[{level}] {title}\n      {detail}\n")
    sys.exit(0)
