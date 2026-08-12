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


def _to_afternoon(t):
    """把凌晨时间（<7点）视为下午同一时刻（活动/天气提醒不可能在凌晨）。"""
    if not t:
        return t
    try:
        h = int(t.split(":")[0])
    except (ValueError, IndexError):
        return t
    if h < 7:
        return f"{h + 12:02d}:{t.split(':')[1]}"
    return t


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


# ===================== 通用"时间+内容"解析（主动触发 v2）=====================
def find_times(text: str) -> list:
    """找出文本里所有时间（中文 + HH:MM），返回 [(HH:MM, 位置)]，按位置排序。"""
    out = []
    for m in _TIME_RE.finditer(text or ""):
        try:
            period, hh_raw, mm_raw = m.group(1), m.group(2), m.group(3)
            hh = _cn_to_int(hh_raw)
            if hh is None:
                continue
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
            out.append((f"{hour:02d}:{minute:02d}", m.start()))
        except Exception:
            continue
    for m in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})", text or ""):
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out.append((f"{hh:02d}:{mm:02d}", m.start()))
    out.sort(key=lambda x: x[1])
    return out


def pick_time(text: str):
    """选提醒时间：文本含「提醒/记得/到点」时优先取它前面最近的时间；否则取第一个时间。"""
    times = find_times(text)
    if not times:
        return None
    for kw in ("提醒", "记得", "到点", "叫我"):
        idx = text.find(kw)
        if idx != -1:
            before = [t for t in times if t[1] < idx]
            if before:
                return before[-1][0]
    return times[0][0]


_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_EPOCH = __import__("datetime").date(2026, 1, 1)


def parse_repeat(text: str):
    """解析重复模式 → ("daily"|"weekday"|"workday"|"weekend"|"every"|"date", 参数)。"""
    t = text or ""
    m = re.search(r"(?:每|隔)\s*(\d+)\s*(?:天|日)", t)
    if m:
        return ("every", max(1, int(m.group(1))))
    if "工作日" in t:
        return ("workday", None)
    if "周末" in t:
        return ("weekend", None)
    m = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        return ("weekday", _WEEKDAY_CN[m.group(1)])
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        return ("date", (int(m.group(1)), int(m.group(2))))
    return ("daily", None)


def match_repeat(repeat, now) -> bool:
    """判断 now 是否落在该重复模式上。"""
    kind, param = repeat
    if kind == "daily":
        return True
    if kind == "weekday":
        return now.weekday() == param
    if kind == "workday":
        return now.weekday() < 5
    if kind == "weekend":
        return now.weekday() >= 5
    if kind == "every":
        return (now.date() - _EPOCH).days % param == 0
    if kind == "date":
        return (now.month, now.day) == param
    return True


class ClockSource(TriggerSource):
    """定点提醒：从档案作息 + 配置固定规则构建提醒表，到点触发。"""
    name = "clock"

    def __init__(self, mem, config: dict):
        self.mem = mem
        self.config = config
        self.rules = self._build_rules(config)

    def _build_rules(self, config: dict) -> list:
        """从 schedule 板块（+旧作息 key 兼容）扫描内容生成触发规则。

        v3 档案板块化后 key 命名由写入端规范化（可能为 schedule_sleep 等），
        因此这里按「内容关键词」匹配，而非依赖精确 key——更健壮。

        支持（v2 通用解析）：
          - 任意「时间 + 内容」条目都会生成提醒（不再只认健身/天气/牛奶/睡）
          - 时间格式：下午五点 / 17:00 / 16:30 / 一点半 / 晚上7点半 等
          - 重复模式：每天(默认) / 每周X / 周X / 工作日 / 周末 / 每N天 / X月X日
          - 含「提醒/记得」时优先取提醒词前面的时间（如"一点半时提醒睡觉"→01:30）
        """
        rules = []
        profile = self.mem.load_profile() or {}
        facts = profile.get("facts", {}) or {}

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

        seen = set()

        def _add(kind, time, repeat, text, rid):
            key = (kind, time, tuple(repeat) if isinstance(repeat, tuple) else repeat)
            if key in seen:
                return
            seen.add(key)
            rules.append({
                "time": time, "id": rid, "kind": kind, "text": text,
                "repeat": repeat,
            })

        for txt in schedule_texts:
            t = pick_time(txt)
            repeat = parse_repeat(txt)
            if not t:
                continue
            if "健身" in txt:
                _add("gym", _to_afternoon(t), repeat,
                     "爸爸～健身时间到啦！别忘了你的锻炼计划，小满给你加油！💪", "gym_remind")
            elif any(w in txt for w in ("天气", "带伞", "防晒")):
                _add("weather", _to_afternoon(t), repeat,
                     "爸爸～到点啦！我先帮你查查天气，出门记得看要不要带伞/防晒哦～🌤️", "weather_remind")
            elif "牛奶" in txt and ("睡" in txt or "作息" in txt):
                # 睡眠提醒保留原始时间（凌晨 01:30 合法）
                _add("sleep", t, repeat,
                     "爸爸～该睡觉啦，睡前记得喝杯热牛奶，暖胃又好睡～🥛💤", "sleep_remind")
            elif "睡" in txt or "作息" in txt:
                _add("sleep", t, repeat,
                     "爸爸～到睡觉时间啦，小满陪你一起进入甜甜的梦乡，晚安！💤", "sleep_remind")
            elif "牛奶" in txt:
                _add("milk", _to_afternoon(t), repeat,
                     "爸爸～牛奶时间到啦！喝杯热牛奶再忙哦～🥛", "milk_remind")
            else:
                # 通用「时间 + 内容」：直接按条目内容提醒
                content = txt.strip(" ；;。，,")
                _add("custom", _to_afternoon(t), repeat, content, f"custom_{len(rules)}")

        # 兼容兜底：有"睡/作息"但没显式时间的 → 默认 23:30；睡前牛奶 → 23:20
        all_text = "；".join(schedule_texts)
        if not any(r["kind"] == "sleep" for r in rules):
            if any(w in all_text for w in _NIGHT_WORDS):
                _add("sleep", "23:30", ("daily", None),
                     "爸爸～又到睡觉时间啦！你平时总是熬夜到凌晨，小满可心疼了，今晚早点睡好不好？晚安！(๑•́ ₃ •̀๑)", "sleep_remind")
            elif "睡" in all_text or "作息" in all_text:
                _add("sleep", "23:30", ("daily", None),
                     "爸爸～到睡觉时间啦，小满陪你一起进入甜甜的梦乡，晚安！💤", "sleep_remind")
        # 睡前牛奶：仅在睡眠提醒文本里已含"牛奶"时视为已覆盖，否则补 23:20 兜底
        sleep_covers_milk = any(r["kind"] == "sleep" and "牛奶" in r["text"] for r in rules)
        if not any(r["kind"] == "milk" for r in rules) and not sleep_covers_milk:
            if "牛奶" in all_text and ("睡前" in all_text or "睡觉" in all_text):
                _add("milk", "23:20", ("daily", None),
                     "爸爸～睡前牛奶时间到啦！喝杯热牛奶再睡，对胃好哦～🥛", "milk_remind")

        # 配置里的固定规则（用户自定义）
        for r in config.get("rules", []) or []:
            if r.get("time") and r.get("text"):
                _add("custom", str(r["time"]), ("daily", None),
                     str(r["text"]), r.get("id", f"rule_{len(rules)}"))
        return rules

    def check(self, now: datetime):
        # 动态刷新：每次 tick 从档案重新解析规则，聊天中更新作息即时生效（无需重启）
        self.rules = self._build_rules(self.config)
        hm = now.strftime("%H:%M")
        for r in self.rules:
            if r["time"] == hm and match_repeat(r.get("repeat", ("daily", None)), now):
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
