# -*- coding: utf-8 -*-
"""M100 扩展：从 TXT 原文（网文/设定稿）里抽取**候选**实体。

定位：只做「候选」，不落库。抽取结果交给作者逐条确认，确认后才写成 author 事实
（沿用 canon_io.commit_candidates）。这与项目一贯的「候选由作者确认后写入」一致。

实现是零依赖的规则法，不追求 NLP 级别的准确，只把高频、有对话归属或地名后缀的
词挑出来给作者过目。准确率边界如实写在 README：它会给名字漏抽、也会混入少量
非人名（如「众人」「那人」这类已列入停用词，但仍可能有漏网），作者需自行剔除。
"""
import hashlib
import json
import re

# 文本与候选上限，避免恶意/超大输入拖垮服务。
MAX_TEXT = 300000
MAX_CANDIDATES = 60

# 常见非人名词（会出现在「X说」位置但不是人名）。
STOP = {
    "他们", "她们", "我们", "你们", "大家", "众人", "村人", "老人", "那人", "此人",
    "有人", "自己", "对方", "二人", "两人", "一人", "旁人", "这时", "此时", "于是",
    "说罢", "说着", "闻言", "听完", "见状", "话音", "话音未落", "半晌", "片刻",
    "声音", "语气", "村里", "众人皆", "所有人", "一群人", "那汉", "汉子", "女子",
    "男子", "少年", "青年", "老者", "妇人", "孩子", "小孩", "孩子",
}
# 「X说」这类对话归属的动词/后缀。
_SAY = re.compile(r"(说道|喊道|叫道|笑道|叹道|问道|答道|说道|说|道|问|答|喊|叫|笑|叹|应)")
_NAMED = re.compile(r"(?:名叫|叫做|唤作|名为|姓)([\u4e00-\u9fa5]{2,3})")
_CJK_TAIL = re.compile(r"([\u4e00-\u9fa5]{2,3})$")
# 地名：前缀 1~3 字 + 地名后缀。
_PLACE = re.compile(
    r"([\u4e00-\u9fa5]{1,3})(村|镇|城|山|岭|河|谷|关|庄|寨|府|县|洞|林|渡|桥|峰|川|洲|岛)")
# 关系：称谓式「A 是 B 的〈称谓〉」，以及同现式「A 与/和/同 B 〈关系动词〉」。
_ROLE_WORDS = ("师父|师傅|师娘|父亲|母亲|兄长|弟弟|姐姐|妹妹|哥哥|妻子|丈夫|儿子|女儿|"
               "朋友|仇人|徒弟|掌柜|叔父|伯父|表兄|堂兄|同乡|旧识|东家|债主")
_REL_KIN = re.compile(rf"([\u4e00-\u9fa5]{{2,3}})是([\u4e00-\u9fa5]{{2,3}})的({_ROLE_WORDS})")
_REL_CO = re.compile(r"([\u4e00-\u9fa5]{2,3})(?:与|和|同)([\u4e00-\u9fa5]{2,3})"
                     r"(是|结为|成亲|反目|相交|相识|同行|为伴|有仇|交好|动手)")
_NEG_ROLES = {"仇人", "反目", "有仇", "动手"}
_LOVE_ROLES = {"成亲", "妻子", "丈夫"}


def _sentiment(role):
    """把关系称谓/动词映射成 (kind, intensity)。"""
    if role in _NEG_ROLES:
        return "怨", -0.6
    if role in _LOVE_ROLES:
        return "爱", 0.8
    if role in ("朋友", "相交", "交好"):
        return "恩", 0.5
    if role in ("相识", "同行", "为伴", "同乡", "旧识"):
        return "attitude", 0.2
    return "恩", 0.6  # 亲缘 / 师承 / 主雇
PLACE_STRIP = set("的了是在和与便也都还就却竟要会能把被之其而乃且亦则又从向过往于遂即更极最"
                  "渡淌涉沿穿越登临那这这座个")
# 名字末尾常被规则误吸入的虚词/助词，需剥掉。
PARTICLES = set("的了是在和与便也都还就却竟要会能把被之其而乃且亦则又很极最")


def _clean(name):
    while name and name[-1] in PARTICLES:
        name = name[:-1]
    return name


def _sample(text, needle):
    i = text.find(needle)
    if i < 0:
        return ""
    return text[max(0, i - 8):i + len(needle) + 8].replace("\n", " ").strip()


def extract(text, min_person=3, min_place=2):
    """从原文抽取候选人物、地点。返回 {persons, places, stats}。纯函数，不落库。"""
    if not isinstance(text, str):
        raise ValueError("原文须为文本")
    truncated = len(text) > MAX_TEXT
    text = text[:MAX_TEXT]

    persons: dict[str, int] = {}
    for m in _SAY.finditer(text):
        window = text[max(0, m.start() - 4):m.start()]
        tail = _CJK_TAIL.search(window)
        if not tail:
            continue
        name = _clean(tail.group(1))
        if name in STOP or len(name) < 2:
            continue
        persons[name] = persons.get(name, 0) + 1
    for m in _NAMED.finditer(text):
        name = _clean(m.group(1))
        if name not in STOP and len(name) >= 2:
            persons[name] = persons.get(name, 0) + 2  # 显式命名，权重更高

    places: dict[str, int] = {}
    for m in _PLACE.finditer(text):
        prefix = "".join(ch for ch in m.group(1) if ch not in PLACE_STRIP)
        if not prefix:
            continue
        name = prefix + m.group(2)
        places[name] = places.get(name, 0) + 1

    def _top(counter, minimum, limit):
        items = [(k, v) for k, v in counter.items() if v >= minimum]
        items.sort(key=lambda kv: (-kv[1], kv[0]))
        return [{"name": k, "count": v, "sample": _sample(text, k)} for k, v in items[:limit]]

    return {
        "persons": _top(persons, min_person, MAX_CANDIDATES),
        "places": _top(places, min_place, MAX_CANDIDATES),
        "relations": _relations(text, set(persons)),
        "stats": {"chars": len(text), "truncated": truncated},
    }


def _relations(text, known):
    """抽取候选人物关系（称谓式 + 同现式）。至少要有一端是已抽到的人名。"""
    found = {}

    def add(a, b, role):
        a, b = _clean(a), _clean(b)
        if len(a) < 2 or len(b) < 2 or a == b or a in STOP or b in STOP:
            return
        if a not in known and b not in known:
            return
        found[(a, b, role)] = found.get((a, b, role), 0) + 1

    for m in _REL_KIN.finditer(text):
        add(m.group(1), m.group(2), m.group(3))
    for m in _REL_CO.finditer(text):
        verb = m.group(3)
        add(m.group(1), m.group(2), "相识" if verb in ("是", "结为") else verb)
    items = []
    for (a, b, role), count in found.items():
        kind, intensity = _sentiment(role)
        items.append({"a": a, "b": b, "role": role, "kind": kind,
                      "intensity": intensity, "count": count})
    items.sort(key=lambda r: (-r["count"], r["a"], r["b"]))
    return items[:MAX_CANDIDATES]


def declare_relations(store, cx, ids, links):
    """把候选关系写成角色间的 relation 关系（有向，双向各写一条）+ 不可改写事件。

    需双方都是世界中的在世角色（可先用 cast_characters 建角）；名字对不上的会跳过。
    """
    if not isinstance(links, list) or not 1 <= len(links) <= 60:
        raise ValueError("请选择 1~60 条关系")
    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    resolved, skipped = [], []
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("关系须为对象")
        a, b = (link.get("a") or "").strip(), (link.get("b") or "").strip()
        role = (link.get("role") or "相识").strip()[:16] or "相识"
        kind = (link.get("kind") or "attitude").strip()[:8] or "attitude"
        try:
            intensity = max(-1.0, min(1.0, float(link.get("intensity", 0))))
        except (TypeError, ValueError):
            intensity = 0.0
        if not a or not b or a == b:
            skipped.append(f"{a or '?'}—{b or '?'}（关系两端须为不同的人）")
            continue
        ca = store.one("SELECT id FROM character WHERE name=? AND status='alive'", (a,))
        cb = store.one("SELECT id FROM character WHERE name=? AND status='alive'", (b,))
        if not ca or not cb:
            skipped.append(f"{a}—{b}（须双方都在世；可先「建成世界角色」）")
            continue
        resolved.append((ca["id"], cb["id"], a, b, kind, intensity, role))
    if not resolved:
        raise ValueError("没有可建立的关系：请先让双方都成为世界中的在世角色")
    try:
        for aid, bid, an, bn, kind, intensity, role in resolved:
            for src, dst in ((aid, bid), (bid, aid)):
                store.con.execute(
                    "INSERT OR REPLACE INTO relation(from_kind,from_id,to_kind,to_id,kind,"
                    "intensity,publicity,note,updated_tick) VALUES('character',?,'character',?,?,?,1.0,?,?)",
                    (src, dst, kind, intensity, f"作者声明：{role}", tick))
            cx.emit(tick, "relation_declared", f"作者声明关系：{an}与{bn}（{role}）",
                    place_kind="settlement", place_id=ids.get("settlement_id"),
                    actor_kind="author", effects={"from": an, "to": bn, "kind": kind, "role": role})
        store.commit()
    except Exception:
        store.con.rollback()
        raise
    return {"created": [{"a": a, "b": b, "kind": k} for _, _, a, b, k, _, _ in resolved],
            "count": len(resolved), "skipped": skipped}


def cast_characters(store, cx, ids, persons):
    """把导入的人物建成世界中的**具名角色**（只追加事件）。

    与「入世角色」同例：具名个体与抽象的 settlement.population 分开统计，
    因此不改变人口总数，也不动经济分配；他们作为角色参与记忆/关系/自主行为。
    """
    if not isinstance(persons, list) or not 1 <= len(persons) <= 30:
        raise ValueError("请选择 1~30 位人物")
    sid = ids.get("settlement_id")
    if sid is None:
        raise ValueError("世界里还没有可安置角色的聚落")
    # 先整体校验，避免中途失败留下半批角色。
    seen, clean = set(), []
    for p in persons:
        if not isinstance(p, dict):
            raise ValueError("人物须为对象")
        name = (p.get("name") or "").strip()
        if not 2 <= len(name) <= 24:
            raise ValueError("人物姓名须为 2~24 字")
        if name in seen:
            raise ValueError(f"同一批里有重名：{name}")
        if store.scalar("SELECT 1 FROM character WHERE name=? AND status='alive'", (name,)):
            raise ValueError(f"已有同名在世角色：{name}")
        seen.add(name)
        occupation = (p.get("occupation") or "村人").strip()[:32] or "村人"
        age = p.get("age")
        goal = (p.get("goal") or "在新的世界立足").strip()[:120] or "在新的世界立足"
        clean.append((name, occupation, age, goal))

    tick = store.scalar("SELECT tick FROM world WHERE id=1")
    seed = store.scalar("SELECT seed FROM world WHERE id=1") or 0
    place = store.scalar("SELECT name FROM settlement WHERE id=?", (sid,))
    created = []
    try:
        for name, occupation, age, goal in clean:
            if type(age) is not int or not 14 <= age <= 90:
                age = 18 + int(hashlib.sha256(f"{name}:{seed}".encode("utf-8")).hexdigest(), 16) % 28
            char_id = store.insert("character", {
                "settlement_id": sid, "faction_id": None, "name": name,
                "occupation": occupation, "age": age, "health": 1.0, "status": "alive",
                "profile": json.dumps({"origin": "导入", "identity": occupation}, ensure_ascii=False),
                "traits": "{}", "needs": "{}", "goal_stack": json.dumps([goal], ensure_ascii=False),
                "born_tick": tick - age * 365 * 24, "updated_tick": tick})
            cx.emit(tick, "character_cast", f"作者将{name}（{occupation}）引入{place}",
                    place_kind="settlement", place_id=sid, actor_kind="author",
                    effects={"character_id": char_id, "name": name, "source": "import"})
            created.append({"character_id": char_id, "name": name})
        store.commit()
    except Exception:
        store.con.rollback()
        raise
    return {"created": created, "count": len(created)}


def to_candidates(result):
    """把抽取结果转成可确认的候选事实（供作者逐条确认）。"""
    out = []
    for p in result.get("persons", []):
        out.append({"content": f"人物：{p['name']}",
                    "note": f"TXT 抽取，出现 {p['count']} 次。示例：{p['sample'][:40]}"})
    for p in result.get("places", []):
        out.append({"content": f"地点：{p['name']}",
                    "note": f"TXT 抽取，出现 {p['count']} 次。示例：{p['sample'][:40]}"})
    for r in result.get("relations", []):
        out.append({"content": f"关系：{r['a']}—{r['b']}（{r['role']}）",
                    "note": f"TXT 抽取，出现 {r['count']} 次"})
    return out
