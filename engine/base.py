# -*- coding: utf-8 -*-
"""
基础工具层：确定性随机 + 世界历法（M095）。

确定性随机的意义：同一个 seed 必须复现同一个世界，且随机值不能依赖调用顺序。
因此所有随机数都由 (seed, 维度..., 序号) 哈希派生，而不是用有状态的 Random 对象。
这样即便中途改变了推演分支顺序，已有结论依然可复现。
"""
import hashlib
import math

HOURS_PER_DAY = 24
DAYS_PER_XUN = 10          # 一旬十日
XUN_PER_SEASON = 3         # 一季三旬
SEASONS_PER_YEAR = 4

DAYS_PER_SEASON = DAYS_PER_XUN * XUN_PER_SEASON      # 30
DAYS_PER_YEAR = DAYS_PER_SEASON * SEASONS_PER_YEAR   # 120
TICKS_PER_DAY = HOURS_PER_DAY

SEASON_NAMES = ["春", "夏", "秋", "冬"]

# 农作产出系数：春播、夏长、秋收、冬藏
SEASON_FARM = [0.70, 1.00, 1.75, 0.18]
# 狩猎系数：夏季草木茂盛猎物分散，冬季反而易猎
SEASON_HUNT = [0.95, 0.80, 1.10, 1.25]


# ---------- 确定性随机 ----------

def unit(seed, *parts):
    """返回 [0,1) 的确定性随机数。"""
    key = "|".join(str(p) for p in (seed,) + parts)
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def between(seed, lo, hi, *parts):
    return lo + (hi - lo) * unit(seed, *parts)


def chance(seed, p, *parts):
    return unit(seed, *parts) < p


def pick(seed, seq, *parts):
    if not seq:
        return None
    return seq[int(unit(seed, *parts) * len(seq)) % len(seq)]


def jitter(seed, mu, sigma, *parts):
    """正态抖动（Box-Muller），用于生成带自然波动的初始值。"""
    u1 = max(unit(seed, *(parts + ("a",))), 1e-9)
    u2 = unit(seed, *(parts + ("b",)))
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# ---------- 历法（M095） ----------

class Calendar:
    """世界时钟。1 tick = 世界内 1 小时。不做跨世代跃迁（永久禁用项）。"""

    @staticmethod
    def day(tick):
        return tick // HOURS_PER_DAY

    @staticmethod
    def hour(tick):
        return tick % HOURS_PER_DAY

    @staticmethod
    def season_index(tick):
        return (tick // (HOURS_PER_DAY * DAYS_PER_SEASON)) % SEASONS_PER_YEAR

    @staticmethod
    def season(tick):
        return SEASON_NAMES[Calendar.season_index(tick)]

    @staticmethod
    def day_of_season(tick):
        return (tick // HOURS_PER_DAY) % DAYS_PER_SEASON + 1

    @staticmethod
    def year(tick):
        return tick // (HOURS_PER_DAY * DAYS_PER_YEAR) + 1

    @staticmethod
    def farm_factor(tick):
        return SEASON_FARM[Calendar.season_index(tick)]

    @staticmethod
    def hunt_factor(tick):
        return SEASON_HUNT[Calendar.season_index(tick)]

    @staticmethod
    def is_new_day(tick):
        return tick % HOURS_PER_DAY == 0

    @staticmethod
    def is_new_season(tick):
        return tick % (HOURS_PER_DAY * DAYS_PER_SEASON) == 0

    @staticmethod
    def is_new_year(tick):
        return tick % (HOURS_PER_DAY * DAYS_PER_YEAR) == 0

    @staticmethod
    def stamp(tick):
        """人类可读时刻，如「第1年 春 第3日」"""
        return (f"第{Calendar.year(tick)}年 {Calendar.season(tick)}季 "
                f"第{Calendar.day_of_season(tick)}日")
