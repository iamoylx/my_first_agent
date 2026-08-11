# -*- coding: utf-8 -*-
"""主动触发事件源（TriggerSource）。

契机来源（第一版）：
  - ClockSource：从用户档案作息（sleep/gym/牛奶）+ 配置固定规则解析"到点提醒"。
  - IdleSource：用户长时间没发消息 → 主动关心。

稳定接口：
  - TriggerSource.check(now) -> Optional[Trigger]   # 契机到没到
  - TriggerSource.on_user_activity()                # 用户说话时通知（空闲源用）
"""
import re
import time
from datetime import datetime

# 时间词 → 基准小时（12 小时制时段）
_PERIOD_HOUR = {
    "凌晨": 0, "半夜": 0, "午夜": 0,
    "早上": 0, "早晨": 0, "上午": 0,
    "中午": 12,
    "下午": 12, "傍晚": 12,
    "晚上": 12,
}
_NIGHT_WORDS = ("熬夜", "凌晨", "半夜", "午夜", "晚睡", "通宵")

_TIME_RE = re.compile(
    r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|半夜|午夜)?\s*([0-9零一二三四五六七八九十两]+)\s*(?:点|时)"
    r"\s*(半|([0-9零一二三四五六七八九十两]+)\s*分?)?"
)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str):
    """中文/阿拉伯数字串 → int；无法解析返回 None。支持 0-23 常见写法。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    s = s.replace("两", "二")
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_DIGITS.get(a, 1) if a else 1
        ones = _CN_DIGITS.get(b, 0) if b else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(s)


class Trigger:
    """一个已命中的主动触发契机。"""
    __slots__ = ("id", "kind", "text", "ts")

    def __init__(self, id, kind, text, ts=None):
        self.id = id
        self.kind = kind
        self.text = text
        self.ts = ts

    def __repr__(self):
        return f"<Trigger {self.id} kind={self.kind}>"


class TriggerSource:
    name = "base"

    def check(self, now: datetime):  # -> Optional[Trigger]
        raise NotImplementedError

    def on_user_activity(self):
        pass


def parse_time_text(text: str):
    """从中文时间描述解析 HH:MM；解析不到返回 None。

    支持：下午五点 → 17:00；凌晨2点 → 02:00；早上9点 → 09:00；
          晚上7点半 → 19:30；中午12点 → 12:00。
    """
    if not text:
        return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    period, hh_raw, mm_raw = m.group(1), m.group(2), m.group(3)
    hh = _cn_to_int(hh_raw)
    if hh is None:
        return None
    hour = hh
    if period in ("下午", "傍晚", "晚上", "半夜", "午夜"):
        if hh < 12:
            hour = hh + 12
    elif period == "凌晨":
        if hh >= 12:
            hour = hh - 12
    if m.group(3) == "半":
        minute = 30
    elif m.group(4):
        minute = _cn_to_int(m.group(4)) or 0
    else:
        minute = 0
    return f"{hour:02d}:{minute:02d}"


class ClockSource(TriggerSource):
    """定点提醒：从档案作息 + 配置固定规则构建提醒表，到点触发。"""
    name = "clock"

    def __init__(self, mem, config: dict):
        self.mem = mem
        self.config = config
        self.rules = self._build_rules(config)

    def _build_rules(self, config: dict) -> list:
        """从 schedule 板块（+旧作息 key 兼容）扫描内容生成触发规则。

        v3 档案板块化后 key 命名由写入端规范化（可能为 schedule_sleep/schedule_sleep_habit 等），
        因此这里按「内容关键词」匹配，而非依赖精确 key——更健壮。
        """
        rules = []
        profile = self.mem.load_profile() or {}
        facts = profile.get("facts", {}) or {}

        # 触发关键词：命中即视为可能产生主动提醒的条目（跨板块扫描，容忍分类模糊）
        _TRIGGER_WORDS = ("睡", "牛奶", "健身", "提醒", "几点", "到点", "作息",
                          "带伞", "防晒", "吃药", "喝水", "起床", "锻炼")
        _OLD_KEYS = ("wake_time", "sleep_time", "sleep_habit", "gym_time",
                     "gym_habit", "habit", "preference")
        schedule_texts = []
        for k, v in facts.items():
            if not isinstance(v, dict) or v.get("active") is False:
                continue
            val = str(v.get("value") or "")
            cat = v.get("category") or ""
            k_l = str(k).lower()
            if (cat in ("schedule", "pref")
                    or k_l.startswith(("schedule_", "pref_"))
                    or k_l in _OLD_KEYS
                    or any(w in val for w in _TRIGGER_WORDS)):
                schedule_texts.append(val)

        all_text = "；".join(schedule_texts)

        # 1) 睡眠提醒：识别熬夜作息 → 23:30 提醒（提前量，而不是真到凌晨才提醒）
        if any(w in all_text for w in _NIGHT_WORDS):
            rules.append({
                "time": "23:30", "id": "sleep_remind", "kind": "sleep",
                "text": "爸爸～又到睡觉时间啦！你平时总是熬夜到凌晨，小满可心疼了，今晚早点睡好不好？晚安！(๑•́ ₃ •̀๑)",
            })
        elif "睡" in all_text or "作息" in all_text:
            rules.append({
                "time": "23:30", "id": "sleep_remind", "kind": "sleep",
                "text": "爸爸～到睡觉时间啦，小满陪你一起进入甜甜的梦乡，晚安！💤",
            })

        # 2) 睡前牛奶：内容含"睡前+牛奶" → 提前 10 分钟提醒
        if "牛奶" in all_text and ("睡前" in all_text or "睡觉" in all_text):
            rules.append({
                "time": "23:20", "id": "milk_remind", "kind": "milk",
                "text": "爸爸～睡前牛奶时间到啦！喝杯热牛奶再睡，对胃好哦～🥛",
            })

        # 3) 健身提醒：从含"健身"的条目解析时间
        for txt in schedule_texts:
            if "健身" in txt:
                t = parse_time_text(txt)
                if t:
                    rules.append({
                        "time": t, "id": "gym_remind", "kind": "gym",
                        "text": "爸爸～健身时间到啦！别忘了你的锻炼计划，小满给你加油！💪",
                    })
                break

        # 4) 配置里的固定规则（用户自定义）
        for r in config.get("rules", []) or []:
            if r.get("time") and r.get("text"):
                rules.append({
                    "time": str(r["time"]), "id": r.get("id", f"rule_{len(rules)}"),
                    "kind": "custom", "text": str(r["text"]),
                })
        return rules

    def check(self, now: datetime):
        # 动态刷新：每次 tick 从档案重新解析规则，聊天中更新作息即时生效（无需重启）
        self.rules = self._build_rules(self.config)
        hm = now.strftime("%H:%M")
        for r in self.rules:
            if r["time"] == hm:
                return Trigger(id=r["id"], kind=r["kind"], text=r["text"], ts=now)
        return None


class IdleSource(TriggerSource):
    """空闲关心：用户超过 N 分钟没发消息 → 主动问候（带冷却）。"""
    name = "idle"

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled", True))
        self.minutes = max(1, int(cfg.get("minutes", 20)))
        self.cooldown_minutes = max(1, int(cfg.get("cooldown_minutes", 120)))
        self._last_activity = time.time()
        self._last_fired = 0.0

    def on_user_activity(self):
        self._last_activity = time.time()

    def check(self, now: datetime):
        if not self.enabled:
            return None
        now_ts = now.timestamp()
        idle_s = now_ts - self._last_activity
        if idle_s >= self.minutes * 60 and (now_ts - self._last_fired) >= self.cooldown_minutes * 60:
            self._last_fired = now_ts
            return Trigger(
                id="idle_care", kind="idle",
                text="爸爸～你都好久没理小满啦，是太忙了吗？记得起来活动一下、喝口水，小满一直在这里陪着你哦❤️",
                ts=now,
            )
        return None


class ReminderSource(TriggerSource):
    """提醒任务源：读取 TaskStore，到点任务触发一次（一次性标记完成 / 重复任务自动推进）。"""
    name = "reminder"

    def __init__(self, task_store):
        self.store = task_store

    def check(self, now: datetime):
        if self.store is None:
            return None
        due = self.store.due(now)
        if not due:
            return None
        t = due[0]
        rep = {"daily": "（每天）", "weekly": "（每周）"}.get(t.get("repeat"), "")
        return Trigger(
            id=f"reminder_{t.get('id')}",
            kind="reminder",
            text=f"⏰ 爸爸，提醒你：{t.get('reminder')}{rep}",
            ts=now,
        )
