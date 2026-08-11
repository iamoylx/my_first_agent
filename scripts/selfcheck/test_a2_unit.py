# -*- coding: utf-8 -*-
"""A2 单元测试：TaskStore / ReminderSource / ClockSource 动态刷新 / scheduler 全链路。"""
import asyncio, json, pathlib, sys, tempfile
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore
from skills.reminder_tools.store import TaskStore, parse_when
from active.sources import ClockSource, ReminderSource, IdleSource
from active.scheduler import ActiveScheduler
from active.config import DEFAULT_CONFIG

# ---------- 1) parse_when ----------
assert parse_when("2026-08-12 15:00").strftime("%Y-%m-%d %H:%M") == "2026-08-12 15:00"
assert parse_when("2026/08/12 15:30").strftime("%Y-%m-%d %H:%M") == "2026-08-12 15:30"
assert parse_when("明天 15:00", datetime(2026, 8, 11, 22, 0)).strftime("%Y-%m-%d %H:%M") == "2026-08-12 15:00"
assert parse_when("今天 09:00", datetime(2026, 8, 11, 22, 0)).strftime("%Y-%m-%d %H:%M") == "2026-08-11 09:00"
assert parse_when("乱写") is None
print("PASS 1: parse_when")

# ---------- 2) TaskStore 增删查 + 到点触发（一次性/每日/每周）----------
with tempfile.TemporaryDirectory() as td:
    ts = TaskStore(base_dir=td, user_id="t")
    now = datetime(2026, 8, 11, 22, 0)
    tid, err = ts.add("明天下午三点开会", "2026-08-12 15:00", "none", now=now)
    assert tid and not err, (tid, err)
    tid2, _ = ts.add("每晚提醒喝水", "今天 23:00", "daily", now=now)
    tid3, _ = ts.add("每周五健身", "2026-08-14 17:00", "weekly", now=now)
    assert len(ts.list()) == 3
    # 过期时间拒绝
    tid4, err4 = ts.add("过去的事", "2026-08-10 10:00", "none", now=now)
    assert tid4 is None and "已过" in err4
    # 到点触发：08-12 15:00 → 一次性(tid)触发完成 + daily(tid2 昨晚23点已过)触发推进
    due = ts.due(datetime(2026, 8, 12, 15, 0))
    ids = {t["id"] for t in due}
    assert tid in ids and tid2 in ids, ids
    assert tid3 not in ids, "weekly(周五)此时不应触发"
    left = {t["id"]: t for t in ts.list()}
    assert tid not in left, "一次性任务应完成"
    assert left[tid2]["when"] == "2026-08-12 23:00", "daily 应推进到次日同一时刻"
    # 周五到点 → weekly 触发推进到下周
    due2 = ts.due(datetime(2026, 8, 14, 17, 0))
    assert tid3 in {t["id"] for t in due2}
    left2 = {t["id"]: t for t in ts.list()}
    assert left2[tid3]["when"] == "2026-08-21 17:00", "weekly 应推进到下周"
    # 删除
    assert ts.delete(tid2) is True
    assert ts.delete("不存在的id") is False
    print("PASS 2: TaskStore（一次性/每日/每周/删除）")

# ---------- 3) ReminderSource ----------
with tempfile.TemporaryDirectory() as td:
    ts = TaskStore(base_dir=td, user_id="t")
    ts.add("喝水", "2026-08-12 09:00", "none", now=datetime(2026, 8, 11, 22, 0))
    src = ReminderSource(ts)
    trig = src.check(datetime(2026, 8, 12, 9, 0))
    assert trig and trig.kind == "reminder" and "喝水" in trig.text and "爸爸" in trig.text, trig
    assert src.check(datetime(2026, 8, 12, 9, 1)) is None, "已触发任务不应重复"
    print("PASS 3: ReminderSource")

# ---------- 4) ClockSource 动态刷新（聊天改作息即时生效）----------
with tempfile.TemporaryDirectory() as td:
    mem = MemoryStore(base_dir=td, user_id="t")
    src = ClockSource(mem, DEFAULT_CONFIG)
    assert src.check(datetime(2026, 8, 11, 23, 30)) is None, "无作息时不应有睡眠提醒"
    # 模拟对话中写入作息（即时生效，无需重启）
    mem.add_profile_item("sleep_habit", "熬夜，凌晨才睡", "fact")
    trig = src.check(datetime(2026, 8, 11, 23, 30))
    assert trig and trig.id == "sleep_remind", trig
    print("PASS 4: ClockSource 动态刷新档案")

# ---------- 5) scheduler 全链路：任务到点 → 载体 ----------
async def main():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(base_dir=td, user_id="t")
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["quiet"] = {"fullscreen": False}
        cfg["sources"]["idle"]["enabled"] = False
        task_dir = pathlib.Path(td) / "tasks"
        sched = ActiveScheduler(mem, config=cfg, log_dir=pathlib.Path(td) / "logs", task_dir=task_dir)
        received = []
        class FakeCarrier:
            async def send(self, msg): received.append(msg)
            def close(self): pass
        sched.register_carrier(FakeCarrier())
        # 注入一条 1 分钟后到点的任务
        now = datetime.now() + timedelta(minutes=1)
        sched._task_store.add("测试提醒", now.strftime("%Y-%m-%d %H:%M"), "none")
        await sched._tick(now + timedelta(minutes=2))
        assert received and received[0]["kind"] == "reminder", received
        assert "测试提醒" in received[0]["text"]
        # 任务已 done（不再重复）
        assert not sched._task_store.list()
        print("PASS 5: scheduler 全链路（任务到点→载体→完成）")

asyncio.run(main())
print("ALL A2 UNIT TESTS PASSED")
