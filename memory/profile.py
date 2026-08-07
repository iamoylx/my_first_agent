# memory/profile.py
# 长期记忆（LTM）主体 = 结构化用户档案卡（profile.json）。
#
# 设计要点（对应 README 架构决策）：
#   1) 状态复写 latest-wins：按事实 key 去重，新值覆盖旧值（仅当新值置信度 >= 已有）。
#   2) 禁止低置信度覆盖已存事实：新事实置信度低于已存则不覆盖，保留旧值。
#   3) 写读分离：抽取在【会话结束】的离线任务里做（AGENT.py 的 finally），
#      绝不在用户发言的轮次里同步做，避免增加每轮延迟。
#   4) 常驻注入：启动时载入，渲染成紧凑文本注入 system 提示词（≤上下文预算，约 15%）。
import json
import os
import aiohttp                 # 离线抽取时复用，保证本模块自包含
from datetime import datetime

PROFILE_FILE = os.path.join(os.path.dirname(__file__), "profile.json")

# 抽取提示词：只抽高信号、稳定的用户事实，明确禁止编造/猜测。
EXTRACT_PROMPT = (
    "你是记忆抽取器。请从下面的对话中提取关于【用户】的"
    "稳定、客观事实（如：姓名、所在城市、职业、语言偏好、"
    "正在做的项目/学习目标、长期偏好）。\n"
    "规则：\n"
    "1) 只提取对话中明确说出的事实，禁止猜测或推断；\n"
    "2) 对每条事实给出 confidence（0~1），不确定就别提；\n"
    "3) 返回 JSON，格式："
    '{"facts":[{"key":"name","value":"小明","confidence":0.95}]}；'
    "若没有可提取的事实，返回 {\"facts\":[]}。\n"
    "只返回 JSON，不要额外文字。"
)


def load_profile():
    """读取档案卡；文件不存在或解析失败都安全返回空结构。"""
    if not os.path.exists(PROFILE_FILE):
        return {"version": 1, "facts": {}}
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "facts" not in data:
            data["facts"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "facts": {}}


def save_profile(profile):
    """原子式写盘：先写临时文件再 os.replace 替换，避免半截文件损坏。"""
    profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = PROFILE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILE_FILE)   # 原子替换（Windows 也支持）


def merge_facts(profile, extracted):
    """
    核心：latest-wins 状态复写。
    extracted: [{"key","value","confidence"}, ...]
    规则：
      - 新 key 直接写入；
      - 已有 key：仅当 new_conf >= old_conf 才覆盖（即禁止低置信度覆盖已存事实）；
      - 每次更新记录 updated_at。
    返回 (profile, changed) —— changed 是本次实际变更的条数。
    """
    facts = profile.setdefault("facts", {})
    now = datetime.now().isoformat(timespec="seconds")
    changed = 0
    for item in extracted:
        key = item.get("key")
        value = item.get("value")
        conf = float(item.get("confidence", 1.0))
        if not key or value is None:
            continue
        old = facts.get(key)
        if old is None:
            # 全新事实，直接写入
            facts[key] = {"value": value, "confidence": conf, "updated_at": now}
            changed += 1
        elif conf >= float(old.get("confidence", 0)):
            # latest-wins：新值置信度不低于旧值 → 覆盖
            facts[key] = {"value": value, "confidence": conf, "updated_at": now}
            changed += 1
        # 否则：新值置信度更低，保留旧值，跳过（防低置信度覆盖）
    return profile, changed


def to_context_text(profile, max_chars=600):
    """把档案渲染成紧凑文本，注入 system 提示词。超长截断以保上下文预算。"""
    facts = profile.get("facts", {})
    if not facts:
        return ""
    lines = [f"- {k}: {v['value']}" for k, v in facts.items()]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n- ...(更多略)"
    return text


async def extract_facts(messages, api_key, api_url, model):
    """
    离线抽取：把对话交给模型，提取结构化事实。
    原理：写读分离——只在会话结束调用，不占用户轮次延迟。
    返回 [{"key","value","confidence"}, ...]；任何失败都降级返回 []，不阻塞退出。
    """
    # 只取 user/assistant 的自然语言，剔除 system 与 tool 噪音
    text = "\n".join(
        f"{m['role']}: {m.get('content', '')}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    )
    if not text.strip():
        return []

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "temperature": 0.1,                       # 抽取要稳定，不要发散
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(api_url, headers=headers,
                              data=json.dumps(payload).encode()) as resp:
                resp.raise_for_status()
                data = await resp.json()
        # 取出模型返回的 JSON 文本，去掉可能的 ```json 围栏
        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        return parsed.get("facts", [])
    except Exception:
        # 降级：抽取失败（网络/解析）不阻塞退出，下次会话还可再抽
        return []
