# -*- coding: utf-8 -*-
"""隔离迁移测试：构造 v2 样本 → 跑迁移 → 验证板块/编辑/新增/主动触发。"""
import io, json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore

# ---------- 1) 构造 v2 样本 ----------
SAMPLE_V2 = {
    "facts": {
        "name": {"value": "爸爸", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "age": {"value": "20岁", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "city": {"value": "江西", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "wake_time": {"value": "熬夜，凌晨才睡", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "gym_time": {"value": "下午五点去健身房", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "habit": {"value": "睡前喝牛奶", "type": "fact", "confidence": 0.9, "updated_at": "2026-08-01T10:00:00", "active": True},
        "pref_answer_style": {"value": "回答时带爱心", "type": "preference", "confidence": 0.8, "updated_at": "2026-08-01T10:00:00", "active": True},
        "talk_style": {"value": "说话带情境动作", "type": "preference", "confidence": 0.8, "updated_at": "2026-08-01T10:00:00", "active": True},
    },
    "events": {},
}
td = tempfile.mkdtemp(prefix="mig_")
dst = os.path.join(td, "profile.json")
io.open(dst, "w", encoding="utf-8").write(json.dumps(SAMPLE_V2, ensure_ascii=False, indent=2))
print("v2 样本:", dst)

# ---------- 2) 跑迁移 ----------
script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "migrate_profile_v3.py")
r = subprocess.run([sys.executable, script, "--apply", "--profile", dst],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
assert r.returncode == 0, r.stdout + r.stderr
print("迁移输出:", r.stdout.strip().splitlines()[0:2])

data = json.load(io.open(dst, encoding="utf-8"))
assert data.get("version") == 3
facts = data["facts"]
print("迁移后 facts:", len(facts))
# 分类校验
assert facts["user_name"]["category"] == "user" and facts["user_name"]["value"] == "爸爸"
assert facts["schedule_sleep"]["category"] == "schedule"
assert facts["schedule_gym"]["category"] == "schedule"
assert facts["schedule_milk"]["category"] == "schedule"
assert facts["pref_answer_style"]["category"] == "pref"
assert facts["rule_talk_style"]["category"] == "rule"
assert facts["user_age"]["category"] == "user"
print("PASS 1: 迁移分类/规范 key")

# ---------- 3) store 板块/编辑/新增 ----------
store_dir = os.path.join(td, "store")
os.makedirs(os.path.join(store_dir, "users", "default"), exist_ok=True)
shutil.copy(dst, os.path.join(store_dir, "users", "default", "profile.json"))
mem = MemoryStore(base_dir=store_dir, user_id="default")
cats = mem.list_profile_items()["categories"]
assert len(cats) == 5
assert sum(len(c["items"]) for c in cats) == len(facts)
user_cat = next(c for c in cats if c["id"] == "user")
assert any(i["key"] == "user_name" for i in user_cat["items"])
print("PASS 2: 5 板块分组")

ok = mem.update_profile_item("user_age", value="21岁", category="user", confidence=0.95)
assert ok
age = [i for c in mem.list_profile_items()["categories"] for i in c["items"] if i["key"] == "user_age"][0]
assert age["value"] == "21岁" and age["confidence"] == 0.95
print("PASS 3: 编辑接口")

ok, err = mem.add_profile_item("schedule_weather", "每天五点查天气带伞", "fact", 0.9, "schedule")
assert ok and not err
keys = [i["key"] for c in mem.list_profile_items()["categories"] for i in c["items"]]
assert "schedule_weather" in keys
print("PASS 4: 新增接口（板块）")

# ---------- 4) ClockSource 读 schedule ----------
from active.sources import ClockSource
from active.config import DEFAULT_CONFIG
src = ClockSource(mem, json.loads(json.dumps(DEFAULT_CONFIG)))
rules = [(r["time"], r["id"]) for r in src.rules]
print("ClockSource rules:", rules)
assert any(r[1] == "gym_remind" for r in rules)
assert any(r[1] == "sleep_remind" for r in rules)
assert any(r[1] == "milk_remind" for r in rules)
assert any(r[1] == "weather_remind" for r in rules)
print("PASS 5: 主动触发规则")
print("ALL MIGRATION TESTS PASSED")
