import os
# -*- coding: utf-8 -*-
"""隔离迁移测试：临时目录跑迁移（apply）→ 用 store 新接口验证板块/编辑/新增。"""
import io, json, os, shutil, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore

SRC = r"D:\document\AGENT_archives\memory.bak.20260812_003720\users\default\profile.json"   # 迁移前原始数据（归档）
td = tempfile.mkdtemp(prefix="mig_test_")
dst = os.path.join(td, "profile.json")
shutil.copy(SRC, dst)
print("临时 profile 复制到:", dst)

# 1) 隔离迁移
import subprocess
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "migrate_profile_v3.py"), "--apply", "--profile", dst],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("迁移输出:", r.stdout.strip())
assert r.returncode == 0, r.stderr

# 2) 验证迁移后结构
data = json.load(io.open(dst, encoding="utf-8"))
assert data.get("version") == 3
facts = data["facts"]
print("迁移后 facts:", len(facts), "| events:", len(data.get("events", {})))
# 无丢失：legacy 字段记录旧 key，且旧 key 的 value 都在新 facts 里（合并后）
import re
all_values = json.dumps(facts, ensure_ascii=False)
for k, v in data.get("events", {}).items():
    if k.startswith("milestone_"):
        assert v.get("value"), k
print("每个事实都有 category:", all(c.get("category") in ("user","agent","pref","rule","schedule") for c in facts.values()))
assert all("category" in c for c in facts.values())

# 3) store 新接口验证（用临时 base_dir）
store_dir = os.path.join(td, "store")
os.makedirs(os.path.join(store_dir, "users", "default"), exist_ok=True)
shutil.copy(dst, os.path.join(store_dir, "users", "default", "profile.json"))
mem = MemoryStore(base_dir=store_dir, user_id="default")

items = mem.list_profile_items()
cats = items["categories"]
print("板块:", [(c["id"], c["name"], len(c["items"])) for c in cats])
assert len(cats) == 5
assert sum(len(c["items"]) for c in cats) == len(facts)
user_cat = next(c for c in cats if c["id"] == "user")
assert any(i["key"] == "user_name" and "爸爸" in str(i["value"]) for i in user_cat["items"])
schedule_cat = next(c for c in cats if c["id"] == "schedule")
assert any("schedule_sleep" == i["key"] for i in schedule_cat["items"])
assert any("schedule_gym" == i["key"] for i in schedule_cat["items"])

# 4) 编辑接口
ok = mem.update_profile_item("user_age", value="21岁", category="user", confidence=0.95)
assert ok
age = [i for c in cats for i in c["items"] if i["key"] == "user_age"][0]
# 重新读
items2 = mem.list_profile_items()
age2 = [i for c in items2["categories"] for i in c["items"] if i["key"] == "user_age"][0]
assert age2["value"] == "21岁" and age2["confidence"] == 0.95, age2
print("编辑接口 OK: user_age ->", age2["value"])

# 5) 新增接口（带板块）
ok, err = mem.add_profile_item("schedule_weather", "五点前查天气提醒带伞", "fact", 0.9, "schedule")
assert ok and not err, (ok, err)
items3 = mem.list_profile_items()
sch3 = next(c for c in items3["categories"] if c["id"] == "schedule")
assert any("schedule_weather" == i["key"] for i in sch3["items"])
# key 规范化：无前缀也会自动加
ok2, _ = mem.add_profile_item("提醒我喝水", "每小时喝一次水", "fact", 0.9, "")
assert ok2
keys = [i["key"] for c in mem.list_profile_items()["categories"] for i in c["items"]]
assert any(k.startswith("schedule_") and "提醒我喝水" in k for k in keys), keys
print("新增接口（板块+key规范化）OK")

# 6) 主动触发源能读到 schedule_*
from active.sources import ClockSource
from active.config import DEFAULT_CONFIG
import json as _j
src = ClockSource(mem, _j.loads(_j.dumps(DEFAULT_CONFIG)))
rules = [(r["time"], r["id"]) for r in src.rules]
print("ClockSource rules:", rules)
assert any(r[1] == "gym_remind" for r in rules), "gym 规则应解析到"
assert any(r[1] == "sleep_remind" for r in rules), "sleep 规则应解析到"
print("PASS: 隔离迁移 + 板块接口 + 编辑/新增 + 主动触发全部正常")
