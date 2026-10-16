# -*- coding: utf-8 -*-
"""
规则护栏（M098）。

目的不是限制创作，而是防止推演在无人干预时走向两种退化态：
  · 全灭  —— 聚落归零、势力消失，世界没法往下讲了
  · 无限连锁 —— 一次砍树引爆全世界，故事被噪声淹没

护栏清单：
  G1 人口下限锁　G2 因果链深度上限　G3 资源再生约束　G4 事件密度上限
  G5 势力存续保护　G6 死亡因果完整性　G8 历史不可改写（不可改写、不可倒填）

全部阈值可配置。被拦截的操作必须留痕（blocked 列表），因为它们本身就是
「规则在起作用」的证据，竞赛演示时是可以拿出来讲的东西。
"""
from .base import clamp


class Guardrail:
    def __init__(self, store, log=None, cfg=None):
        self.store = store
        self.log = log or (lambda msg: None)
        cfg = cfg or {}
        self.pop_floor = cfg.get("pop_floor", 5)          # G1 人口下限
        self.max_depth = cfg.get("max_depth", 4)          # G2 因果链深度上限
        self.event_cap = cfg.get("event_cap", 48)         # G4 每 tick 事件上限（超额进入排队而非丢弃；48 给季节/年度边界的密集事件留足余量，避免经济类事件被挤掉）
        self.min_factions = cfg.get("min_factions", 2)    # G5 活跃势力下限
        self.max_tick = 0                                 # G8 历史水位线（禁止倒填）
        self._per_tick = {}                               # tick -> 已发事件数
        self._res_state = {}                              # 资源tag -> 是否处于超采
        self.blocked = []                                 # 拦截留痕
        self.trigger_count = {}                           # 各护栏触发次数

    def _hit(self, rule, detail):
        self.trigger_count[rule] = self.trigger_count.get(rule, 0) + 1
        self.blocked.append((rule, detail))

    # ---- G1 人口下限锁 ----
    def clamp_population(self, settlement_id, pop, tick):
        """任一聚落人口不得低于阈值。触底时进入衰退叙事态，但不得静默清零。"""
        if pop >= self.pop_floor:
            return pop
        self._hit("G1", f"聚落#{settlement_id} 人口触底，钳制在 {self.pop_floor}")
        return self.pop_floor

    # ---- G2 因果链深度上限 ----
    def allow_depth(self, depth, kind):
        if depth > self.max_depth:
            self._hit("G2", f"因果链深度 {depth} 超限的「{kind}」降级为日志")
            return False
        return True

    # ---- G3 资源再生约束 ----
    def resource_step(self, value, regen, harvest, cap=1.0, tag=None):
        """
        可再生资源推进一步。
        采伐超过再生速率时，再生能力本身按下调曲线递减 —— 直到不可逆退化。
        这样「过度砍伐导致永久荒漠化」是自然涌现的，而不是硬编码的。

        tag 用于区分资源种类，同一资源只在「首次转入超采」时留一次痕，
        避免长跑时刷屏。
        """
        pressure = harvest / (regen + 1e-9)
        eff_regen = regen if pressure <= 1.0 else regen / (1.0 + (pressure - 1.0))
        if tag:
            was = self._res_state.get(tag, False)
            now = pressure > 1.0
            if now and not was:
                self._hit("G3", f"{tag}采伐强度达再生能力的 {pressure:.1f} 倍，"
                                f"再生速率由 {regen:.5f} 下调至 {eff_regen:.5f}")
            self._res_state[tag] = now
        nv = value + eff_regen - harvest
        if nv < 0:
            self._hit("G3", f"{tag or '资源'}被采至负值，钳制为 0")
        return clamp(nv, 0.0, cap), eff_regen

    # ---- G4 事件密度上限 ----
    def allow_event(self, tick):
        n = self._per_tick.get(tick, 0)
        if n >= self.event_cap:
            self._hit("G4", f"tick {tick} 事件数达上限 {self.event_cap}，排队抑制")
            return False
        self._per_tick[tick] = n + 1
        return True

    def prune(self, tick):
        """丢弃过期的计数，避免长跑时内存膨胀。"""
        for t in [t for t in self._per_tick if t < tick - 1]:
            del self._per_tick[t]

    # ---- G5 势力存续保护 ----
    def check_factions(self, tick):
        n = self.store.scalar("SELECT COUNT(*) FROM faction WHERE status='active'")
        if n < self.min_factions:
            self._hit("G5", f"活跃势力仅剩 {n} 个，低于下限")
            return False
        return True

    # ---- G6 死亡因果完整性 ----
    def require_death_cause(self, event_id, char_id):
        if not event_id:
            self._hit("G6", f"角色#{char_id} 死亡无可追溯事件，已拦截")
            return False
        return True

    # ---- G8 历史不可改写 ----
    def sync_horizon(self):
        """从库中恢复「已归档的最晚时刻」。续跑旧存档时必须先调用。"""
        t = self.store.scalar("SELECT MAX(tick) FROM event", default=0) or 0
        self.max_tick = max(self.max_tick, t)

    def allow_append(self, tick, kind):
        """
        历史只有一个方向。新事件的时刻不得早于已经归档的最晚时刻 ——
        否则就是「倒填日期」，等于事后往过去塞事情，本质上还是在改写历史。
        """
        if tick < self.max_tick:
            self._hit("G8", f"倒填事件被拒：{kind} 企图写在 {tick}，"
                            f"但历史已经走到 {self.max_tick}")
            return False
        self.max_tick = max(self.max_tick, tick)
        return True

    def flag_tamper(self, op, detail):
        """任何绕过正常流程、试图改写已发生事实的动作都在此留痕。"""
        self._hit("G8", f"改写历史被拒：{op} —— {detail}")

    def harvest_tamper(self, store):
        """把持久层的拦截记录收进护栏台账，让它们出现在最终报告里。"""
        for rec in getattr(store, "tamper_log", []):
            self.flag_tamper(rec["op"], f"{rec['table']}{rec['detail']}")
        if hasattr(store, "tamper_log"):
            store.tamper_log.clear()

    # ---- 报告 ----
    def report(self):
        return {
            "blocked_total": len(self.blocked),
            "by_rule": dict(self.trigger_count),
            "samples": self.blocked[:12],
        }
