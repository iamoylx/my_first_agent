# -*- coding: utf-8 -*-
"""档案卡 v3 迁移：49 条 facts → 5 板块 + 规范 key + events 归类。
用法：python tmp/migrate_profile_v3.py          # dry-run（不写盘）
      python tmp/migrate_profile_v3.py --apply  # 写入 memory_data（原子替换）
无丢失保证：每个旧 key 都被处理（MAP/events），合并后 value 用分号拼接保留全部信息。
"""
import json, io, os, sys, tempfile, shutil
from datetime import datetime

import sys as _sys
PROFILE = _sys.argv[_sys.argv.index("--profile") + 1] if "--profile" in _sys.argv else r"memory_data\users\default\profile.json"
APPLY = "--apply" in sys.argv

# ============ 分类映射：old_key -> (category, new_key)；None -> events ============
MAP = {
    # 用户身份 user_
    "name": ("user", "user_name"), "user_name": ("user", "user_name"),
    "nickname": ("user", "user_nickname"), "age": ("user", "user_age"),
    "height": ("user", "user_height"), "weight": ("user", "user_weight"),
    "gender": ("user", "user_gender"), "city": ("user", "user_city"),
    "university": ("user", "user_university"), "major": ("user", "user_major"),
    "language": ("user", "user_language"), "learning_goal": ("user", "user_learning_goal"),
    "personality": ("user", "user_personality"), "device_usage": ("user", "user_device_usage"),
    "remote_software": ("user", "user_remote_software"),
    "usage_scenario": ("user", "user_usage_scenario"), "task": ("user", "user_current_task"),
    "project": ("user", "user_project"), "location_interest": ("user", "user_location_interest"),
    "concern": ("user", "user_concern"),
    # Agent 身份 agent_
    "agent_name": ("agent", "agent_name"), "agent_role": ("agent", "agent_role"),
    "agent_style": ("agent", "agent_style"), "role": ("agent", "agent_relation"),
    "pref_daughtership": ("agent", "agent_duty"), "bond_with_dad": ("agent", "agent_bond"),
    # 用户偏好 pref_
    "food_preference": ("pref", "pref_food"), "breakfast_preference": ("pref", "pref_breakfast"),
    "game_preference": ("pref", "pref_game"), "hobbies": ("pref", "pref_game"),
    "hobby": ("pref", "pref_private"), "pref_affection": ("pref", "pref_call"),
    "pref_answer_style": ("pref", "pref_answer_style"), "pref_style": ("pref", "pref_answer_style"),
    # 行为规定 rule_
    "call_style": ("rule", "rule_call"), "pref_avoid_ning": ("rule", "rule_call"),
    "pref_use_command_line": ("rule", "rule_command_line"), "talk_style": ("rule", "rule_talk_style"),
    # 主动触发 schedule_
    "wake_time": ("schedule", "schedule_sleep"), "sleep_time": ("schedule", "schedule_sleep"),
    "sleep_habit": ("schedule", "schedule_sleep"), "gym_time": ("schedule", "schedule_gym"),
    "gym_habit": ("schedule", "schedule_gym"), "habit": ("schedule", "schedule_milk"),
    "preference": ("schedule", "schedule_sleep_detail"),
    # 里程碑/开发记录 → events
    "active_trigger_mechanism": (None, None), "desktop_launch_date": (None, None),
    "future_plan_multimodal": (None, None), "mcp_plan": (None, None),
    "weather_skill_duty": (None, None),
}

def merge_values(vals):
    """合并多条 value：相等去重；不同用「；」拼接（保留全部信息）。"""
    seen, out = set(), []
    for v in vals:
        v = str(v or "").strip()
        if not v:
            continue
        if v not in seen:
            seen.add(v); out.append(v)
    return "；".join(out) if out else ""

def run(profile_path):
    data = json.load(io.open(profile_path, encoding="utf-8"))
    if data.get("version") == 3:
        print("已是 v3 结构，跳过迁移（幂等）")
        return data, data.get("facts", {}), data.get("events", {}), []
    facts = data.get("facts", {}) or {}
    events = data.get("events", {}) or {}

    buckets = {}
    unmapped = []
    for k, v in facts.items():
        if k in MAP:
            cat, nk = MAP[k]
            if cat is None:
                events[f"milestone_{k}"] = {
                    "value": v.get("value"), "type": "event",
                    "confidence": v.get("confidence", 0.9),
                    "occurred_at": v.get("updated_at"),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            else:
                buckets.setdefault(nk, {"cat": cat, "items": []})["items"].append(v)
        else:
            unmapped.append(k)

    new_facts = {}
    for nk, info in buckets.items():
        items = info["items"]
        vals = merge_values([i.get("value") for i in items])
        if not vals:
            continue
        confs = [float(i.get("confidence", 0.9)) for i in items]
        types = [i.get("type", "fact") for i in items]
        # type 优先级：preference > role > fact
        ftype = "preference" if "preference" in types else ("role" if "role" in types else "fact")
        upd = max((i.get("updated_at") or "" for i in items), default="")
        new_facts[nk] = {
            "value": vals, "confidence": max(confs), "type": ftype,
            "category": info["cat"], "updated_at": upd or datetime.now().isoformat(timespec="seconds"),
            "active": any(i.get("active", True) is not False for i in items),
            "legacy": [k for k, v in MAP.items() if v == (info["cat"], nk) and k != nk],
        }
    return data, new_facts, events, unmapped

data, new_facts, events, unmapped = run(PROFILE)

# ============ 输出 dry-run 报告 ============
print(f"原 facts: {len(data.get('facts', {}))} 条")
print(f"迁移后 facts: {len(new_facts)} 条（合并后） | events: {len(events)} 条（原{len(data.get('events', {}))}+里程碑{len([k for k,v in MAP.items() if v[0] is None and k in data.get('facts', {})])}）")
if unmapped:
    print(f"!! 未映射 {len(unmapped)} 条: {unmapped}")
    sys.exit(1)
from collections import Counter
cc = Counter(v["category"] for v in new_facts.values())
print("板块分布:", dict(cc))
if not APPLY:
    print("\n[dry-run] 未写盘。加 --apply 执行迁移。")
    sys.exit(0)

# ============ apply：原子替换 ============
data["facts"] = new_facts
data["events"] = events
data["version"] = 3
tmp = PROFILE + ".v3tmp"
io.open(tmp, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2))
os.replace(tmp, PROFILE)
print(f"\n[apply] 已写入 {PROFILE}（原子替换）")
print("请立即重启小满应用加载新档案结构！")
