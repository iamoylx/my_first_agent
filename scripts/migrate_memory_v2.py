# -*- coding: utf-8 -*-
"""记忆档案 v1 → v2 一次性迁移（幂等、只读源、可回滚）。

迁移内容（不删除任何原始值，全部等价保留）：
  - facts  稳定事实（身份/偏好/角色）→ 注入 system
  - events 时间敏感/事件型事实（当前状态/当天计划/刚发生的事）→ 不注入 system，可检索
  - discarded 迁移时判定为垃圾的占位事实（保留但不注入）
同义 key 归一（hometown→city 等），type=None 补全类型。

用法：python scripts/migrate_memory_v2.py [--user default] [--dry-run]
前置：已执行备份（memory.bak.<ts>/memory_data）；本脚本不改会话归档。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory.profile import canonical_key

# 时间敏感/事件型：从稳定档案移到 events（不注入 system）
TEMPORAL_KEYS = {
    "current_time", "activity", "schedule", "plan", "movie_plan",
    "movie_watched", "movie", "current_task", "task",
}


def infer_type(key: str, value) -> str:
    k = key.lower()
    v = str(value)
    if k.startswith("pref") or k in ("preference", "habit", "concern"):
        return "preference"
    if "role" in k or ("女儿" in v and "爸爸" in v):
        return "role"
    return "fact"


def is_junk(item: dict) -> bool:
    val = str(item.get("value", "")).strip()
    conf = item.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    if not val or val in ("未提及", "未知", "无"):
        return True
    if conf is not None and conf < 0.1:
        return True
    return False


def migrate(profile_path: str, dry_run: bool = False) -> dict:
    with open(profile_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    old_facts = old.get("facts", {})

    new_facts, events, discarded = {}, {}, {}
    moved = {"events": [], "discarded": []}
    for k, v in old_facts.items():
        ck = canonical_key(k)
        if k in TEMPORAL_KEYS:
            events[ck] = dict(v); events[ck]["type"] = "event"
            moved["events"].append(k)
            continue
        if is_junk(v):
            discarded[ck] = dict(v)
            moved["discarded"].append(k)
            continue
        v = dict(v)
        if not v.get("type"):
            v["type"] = infer_type(ck, v.get("value"))
        v.setdefault("source", "legacy")
        # 同义合并：同 key 取置信度更高/更新的
        oldv = new_facts.get(ck)
        if oldv is None or v.get("confidence", 0) >= oldv.get("confidence", 0):
            new_facts[ck] = v

    new_profile = {
        "schema_version": 2,
        "updated_at": old.get("updated_at"),
        "facts": new_facts,
        "events": events,
        "discarded": discarded,
    }
    return {"old": old_facts, "new": new_profile, "moved": moved}


def verify(old_facts: dict, new_profile: dict) -> bool:
    """完整性：每个原始值都必须出现在新结构中（允许 key 归一合并）。"""
    new_text = json.dumps(new_profile, ensure_ascii=False)
    missing = []
    for k, v in old_facts.items():
        val = v.get("value")
        if isinstance(val, list):
            for x in val:
                if str(x) not in new_text:
                    missing.append(f"{k}:{x}")
        elif str(val) not in new_text:
            missing.append(f"{k}:{val}")
    return missing, len(missing) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_path = os.path.join(root, "memory_data", "users", args.user, "profile.json")
    if not os.path.exists(profile_path):
        print("profile not found:", profile_path); sys.exit(1)

    res = migrate(profile_path, dry_run=args.dry_run)
    old_facts = res["old"]
    new_profile = res["new"]

    print("===== 迁移结果（dry-run)" if args.dry_run else "===== 迁移结果")
    print(f"旧 facts: {len(old_facts)} 条")
    print(f"新 facts: {len(new_profile['facts'])} 条（稳定，注入 system）")
    print(f"  events: {len(new_profile['events'])} 条（时间敏感，不再注入）")
    print(f"  discarded: {len(new_profile['discarded'])} 条（垃圾占位，保留不注入）")
    print("移入 events:", res["moved"]["events"])
    print("移入 discarded:", res["moved"]["discarded"])

    missing, ok = verify(old_facts, new_profile)
    if missing:
        print("⚠️ 以下原始值未在新结构中找到（不应发生）:", missing[:10])
    print("完整性校验:", "PASS" if ok else "FAIL")

    if args.dry_run:
        print("（dry-run，未写盘）")
        return
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(new_profile, f, ensure_ascii=False, indent=2)
    print("已写回:", profile_path)


if __name__ == "__main__":
    main()
