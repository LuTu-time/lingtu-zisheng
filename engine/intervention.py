# -*- coding: utf-8 -*-
"""M106 作者干预：上帝面板的「凭空注入事件」入口。

与「拨粮/植树」这类有代价的资源干预不同，这里让作者直接往历史里追加一条
自定义事件（如「一位游侠自山外而来」）。它严格守两条底线：
  1. 只追加、不改写 —— 注入的是当前时刻的一条新事件，成为因果链的根（actor=author）；
     已发生的历史依旧被触发器保护，动不了。
  2. 有据可查 —— 事件走与推演事件同一条哈希链与锚点；谁在什么时候注入，一目了然。

不做的事：不直接把任意状态写进 stock/region/character。要改状态仍走有代价、
有语义校验的干预入口（play.intervene）；这里的 effects 只是描述性的元数据，
随事件存档，供纪事与「追因」阅读，不会被引擎当作状态指令执行。
"""
import json
import re

_KIND = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_PLACES = {"none", "settlement", "region", "faction"}
_ACTORS = {"author", "character", "faction", "nature"}
_VISIBILITY = {"public", "private", "secret"}
_PLACE_TABLE = {"settlement": "settlement", "region": "region", "faction": "faction"}


def _text(value, label, lo, hi):
    if not isinstance(value, str):
        raise ValueError(f"{label}须为文本")
    text = value.strip()
    if not lo <= len(text) <= hi:
        raise ValueError(f"{label}须为 {lo}~{hi} 字")
    return text


def inject_event(store, cx, data):
    """把作者自定义的一条事件追加进当前世界历史。返回 {event_id,kind,summary}。"""
    if not isinstance(data, dict):
        raise ValueError("注入内容须为对象")
    summary = _text(data.get("summary"), "事件描述", 1, 240)

    kind = data.get("kind", "author_event")
    if kind is None:
        kind = "author_event"
    if not isinstance(kind, str) or not _KIND.match(kind):
        raise ValueError("事件类型须为小写字母开头、可含数字与下划线的短标识")

    place_kind = data.get("place_kind", "settlement")
    if place_kind not in _PLACES:
        raise ValueError("地点类型仅支持 none / settlement / region / faction")
    place_id = None
    if place_kind != "none":
        place_id = data.get("place_id")
        if place_id is None:
            # 默认挂到当前聚落
            place_id = store.scalar("SELECT id FROM settlement ORDER BY id LIMIT 1")
        if type(place_id) is not int or not store.scalar(
                f"SELECT 1 FROM {_PLACE_TABLE[place_kind]} WHERE id=?", (place_id,)):
            raise ValueError("指定地点不存在")

    actor_kind = data.get("actor_kind", "author")
    if actor_kind not in _ACTORS:
        raise ValueError("发起者类型仅支持 author / character / faction / nature")
    actor_id = data.get("actor_id")
    if actor_kind in ("character", "faction"):
        if type(actor_id) is not int or not store.scalar(
                f"SELECT 1 FROM {actor_kind} WHERE id=?", (actor_id,)):
            raise ValueError("指定发起者不存在")
    else:
        actor_id = None

    visibility = data.get("visibility", "public")
    if visibility not in _VISIBILITY:
        raise ValueError("可见性仅支持 public / private / secret")

    effects = data.get("effects", {})
    if effects is None:
        effects = {}
    if not isinstance(effects, dict):
        raise ValueError("effects 须为对象")
    if len(json.dumps(effects, ensure_ascii=False)) > 4000:
        raise ValueError("effects 过大")

    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    event_id = cx.emit(tick, kind, summary, cause=None, effects=effects,
                       place_kind=place_kind, place_id=place_id,
                       actor_kind=actor_kind, actor_id=actor_id, visibility=visibility)
    if event_id is None:
        raise ValueError("事件被规则护栏拒绝；历史未改动")
    store.commit()
    return {"event_id": event_id, "kind": kind, "summary": summary,
            "place_kind": place_kind, "place_id": place_id, "visibility": visibility}
