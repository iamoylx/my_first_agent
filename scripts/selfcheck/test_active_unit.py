# -*- coding: utf-8 -*-
"""主动触发单元测试：解析/时钟源/空闲源/调度器链路（临时记忆隔离）。"""
import asyncio, json, pathlib, sys, tempfile
from datetime import datetime

sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore
from active.sources import parse_time_text, ClockSource, IdleSource, Trigger
from active.scheduler import ActiveScheduler
from active.carriers import LogCarrier
from active.config import DEFAULT_CONFIG

# ---------- 1) 时间解析 ----------
cases = {
    "下午五点": "17:00", "凌晨2点": "02:00", "早上9点": "09:00",
    "晚上7点半": "19:30", "中午12点": "12:00", "下午三点十五": "15:15",
    "晚上7点": "19:00",
}
for txt, expect in cases.items():
    got = parse_time_text(txt)
    assert got == expect, f"{txt}: got {got}, expect {expect}"
assert parse_time_text("无时间信息") is None
print("PASS 1: parse_time_text", len(cases), "cases")

# ---------- 2) ClockSource 从档案解析规则 ----------
with tempfile.TemporaryDirectory() as td:
    mem = MemoryStore(base_dir=td, user_id="t")
    mem.add_profile_item("sleep_habit", "熬夜，凌晨才睡", "fact")
    mem.add_profile_item("gym_time", "下午五点去健身房", "fact")
    mem.add_profile_item("preference", "喜欢休息后再睡觉、睡前喝牛奶", "preference")
    src = ClockSource(mem, DEFAULT_CONFIG)
    times = [(r["time"], r["id"]) for r in src.rules]
    print("rules:", times)
    assert ("23:30", "sleep_remind") in times, times
    assert ("17:00", "gym_remind") in times, times
    assert ("23:20", "milk_remind") in times, times
    # 到点触发
    trig = src.check(datetime(2026, 8, 11, 23, 30))
    assert trig and trig.id == "sleep_remind" and "爸爸" in trig.text, trig
    assert src.check(datetime(2026, 8, 11, 10, 0)) is None
    print("PASS 2: ClockSource")

# ---------- 3) IdleSource 活动/空闲/冷却 ----------
idle = IdleSource({"enabled": True, "minutes": 1, "cooldown_minutes": 60})
idle.on_user_activity()
assert idle.check(datetime.now()) is None, "刚活动不应触发"
idle._last_activity -= 90   # 模拟 90 秒前活动
t1 = idle.check(datetime.now())
assert t1 is not None and t1.id == "idle_care", "空闲应触发"
assert idle.check(datetime.now()) is None, "冷却内不应重复触发"
print("PASS 3: IdleSource")

# ---------- 4) 调度器全链路（tick → 冷却 → 载体 → 日志）----------
async def main():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(base_dir=td, user_id="t")
        mem.add_profile_item("sleep_habit", "熬夜，凌晨才睡", "fact")
        log_dir = pathlib.Path(td) / "logs"
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["quiet"] = {"fullscreen": False}   # 测试环境禁用全屏免打扰
        cfg["sources"]["idle"]["enabled"] = False   # idle 已在第 3 步单独验证
        cfg["tick_seconds"] = 1
        cfg["cooldown_seconds"] = 3600
        sched = ActiveScheduler(mem, config=cfg, log_dir=log_dir)
        received = []
        class FakeCarrier:
            async def send(self, msg): received.append(msg)
            def close(self): pass
        sched.register_carrier(FakeCarrier())
        # 到点 tick：23:30 命中 sleep
        await sched._tick(datetime(2026, 8, 11, 23, 30))
        assert len(received) == 1 and received[0]["id"] == "sleep_remind", received
        assert received[0]["type"] == "active"
        # 冷却：同一分钟内再次 tick 不重复
        await sched._tick(datetime(2026, 8, 11, 23, 30, 30))
        assert len(received) == 1, "冷却应阻止重复触发"
        # 日志落盘
        logs = list(log_dir.glob("active-*.jsonl"))
        assert logs, "active log missing"
        line = json.loads(logs[0].read_text(encoding="utf-8").strip().splitlines()[-1])
        assert line["id"] == "sleep_remind" and line["text"]
        print("PASS 4: scheduler 链路（触发/冷却/载体/日志）")

asyncio.run(main())
print("ALL ACTIVE UNIT TESTS PASSED")
