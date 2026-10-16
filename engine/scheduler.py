# -*- coding: utf-8 -*-
"""
题材选材驱动功能适配（M094）。

世界开局，把题材标签解析成 107 个模块各自的三档状态，落进 module_instance 表。
这是「同一套内核长出不同世界」的那个开关。

注意：P0 阶段引擎真正执行的是模块的一个子集（见 engine/__init__.py 的清单），
其余模块在此登记为 on/simple 但尚无推演实现 —— 调度表是完整的，实现是渐进的。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    with open(os.path.join(ROOT, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


def resolve(module, genre):
    """把一个模块在给定题材下解析成 (state, reason)。"""
    if module["scope"] == "global":
        return "on", "全局常驻"
    if genre is None:
        return "off", "无题材标签"
    if module["id"] in genre["on"]:
        return "on", f'题材挂载：{genre["label"]}'
    if module["id"] in genre.get("simple", []):
        return "simple", f'题材简化档：{genre["label"]}'
    if module["id"] in genre.get("off", []):
        return "off", f'题材关闭：{genre["label"]}'
    return "off", "未被当前题材挂载"


def schedule(store, theme, log=None):
    mods = _load("modules.json")
    gen = _load("genres.json")
    genre = next((g for g in gen["genres"] if g["key"] == theme), None)

    counts = {"on": 0, "simple": 0, "off": 0}
    for m in mods["modules"]:
        state, reason = resolve(m, genre)
        store.con.execute(
            "INSERT OR REPLACE INTO module_instance "
            "(module_id, scope, state, params, reason) VALUES (?,?,?,?,?)",
            (m["id"], m["scope"], state, "{}", reason))
        counts[state] += 1
    store.commit()
    return counts, (genre["label"] if genre else theme)
