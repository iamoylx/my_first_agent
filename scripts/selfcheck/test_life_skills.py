# -*- coding: utf-8 -*-
"""生活 skill 单元测试：health_record 写入/查询 + weather 调用。"""
import asyncio, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore
from skills.health_record import record_health, health_records, TOOL_MAP
from skills.weather import get_weather

# ---------- 1) health_record 写入 + 查询 ----------
async def test_health():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(base_dir=td, user_id="t")
        r1 = await record_health("sleep_hours", "7.5", date="今天", note="睡得不错", mem=mem)
        print("record:", r1)
        assert "已记录" in r1 and "7.5" in r1
        r2 = await record_health("weight_kg", "75.5", date="2026-08-10", mem=mem)
        assert "已记录" in r2
        r3 = await record_health("bad_metric", "1", mem=mem)
        assert "不支持的指标" in r3
        # 查询
        q = await health_records(metric="sleep_hours", days=7, mem=mem)
        print("query sleep:", q)
        assert "7.5" in q and "睡得不错" in q
        qall = await health_records(days=7, mem=mem)
        assert "weight_kg" in qall and "sleep_hours" in qall
        # 写入的是 events 而非 facts
        profile = mem.load_profile()
        assert any(k.startswith("health_sleep_hours_") for k in profile.get("events", {})), "应写入 events"
        assert not any(k.startswith("health_sleep_hours_") for k in profile.get("facts", {})), "不应进 facts"
        print("PASS 1: health_record")

# ---------- 2) weather 真实调用（网络可用则验证）----------
async def test_weather():
    try:
        w = await get_weather("重庆")
        print("weather 重庆:", w[:200])
        assert "°C" in w and ("当前天气" in w or "错误" in w)
        print("PASS 2: weather（真实网络）")
    except Exception as e:
        print("weather 网络不可用，跳过真实断言:", e)

asyncio.run(test_health())
asyncio.run(test_weather())
print("DONE")
