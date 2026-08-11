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
import aiohttp                 # 离线抽取时复用，保证本模块自包含
from datetime import datetime

# 同义 key → 规范 key：抽取/迁移时把重复事实归一到一个 key（latest-wins 语义保留）。
# 仅收录"语义明确等价"的映射，避免误合并。
CANONICAL_KEYS = {
    "hometown": "city",                        # 江西
    "school": "university",                    # 重庆邮电大学
    "dietary_preference": "food_preference",   # 无辣不欢
    "spice_preference": "food_preference",     # 无辣不欢
    "pref_milk": "habit",                      # 睡前喝牛奶
    "preferred_answer_style": "pref_answer_style",  # 带爱心
    "sleep_schedule": "wake_time",             # 凌晨2点睡9点起
}


def canonical_key(key: str) -> str:
    """把同义 key 归一为规范 key（未收录则原样返回）。"""
    return CANONICAL_KEYS.get(key, key)


# 抽取提示词：抽两类高信号、稳定的用户记忆——事实 + 偏好/意图，明确禁止编造/猜测。
EXTRACT_PROMPT = (
    "你是记忆抽取器。请从下面的对话中提取关于【用户】的以下内容：\n"
    "A) 稳定客观事实：姓名、所在城市、职业、语言、正在做的项目/学习目标等；\n"
    "B) 偏好与意图：对回答风格的要求（简洁/详细/带代码示例）、对技术的倾向、"
    "明确表达的不满或要求、长期习惯等。\n"
    "规则：\n"
    "1) 只提取对话中明确说出的内容，禁止猜测或推断；\n"
    "2) 对每条给出 confidence（0~1），不确定就别提；\n"
    "3) 每条用 type 标注：事实填 \"fact\"，偏好/意图填 \"preference\"；\n"
    "4) key 用语义化英文小写下划线，同一事实永远用同一个 key（例如：城市=city、学校=university、"
    "饮食偏好=food_preference、作息=wake_time、回答风格=pref_answer_style、习惯=habit、名字=name），"
    "遇到同义内容复用已有 key，禁止为同一事实造新 key；\n"
    "5) 不要抽取时间性/一次性内容为稳定事实（当前时间、当天/明天的计划、刚去过哪、一次性的活动），"
    "除非用户明确说\"记住/以后都\"；\n"
    "6) 返回 JSON，格式："
    '{"facts":[{"key":"name","value":"小明","confidence":0.95,"type":"fact"},'
    '{"key":"pref_answer_style","value":"要简洁、少废话","confidence":0.8,"type":"preference"}]}；'
    "若没有可提取的内容，返回 {\"facts\":[]}。\n"
    "只返回 JSON，不要额外文字。"
)


def merge_facts(profile, extracted):
    """
    核心：latest-wins 状态复写。
    extracted: [{"key","value","confidence","type"}, ...]，type ∈ {fact, preference}
    规则：
      - 新 key 直接写入；
      - 已有 key：仅当 new_conf >= old_conf 才覆盖（即禁止低置信度覆盖已存事实）；
      - 每条记录 type（事实/偏好）与 updated_at，供展示与注入区分。
    返回 (profile, changed) —— changed 是本次实际变更的条数。
    """
    facts = profile.setdefault("facts", {})
    now = datetime.now().isoformat(timespec="seconds")
    changed = 0
    for item in extracted:
        key = canonical_key(item.get("key"))   # 同义 key → 规范 key
        value = item.get("value")
        conf = float(item.get("confidence", 1.0))
        ftype = item.get("type", "fact")
        if not key or value is None:
            continue
        old = facts.get(key)
        if old is None:
            # 全新事实，直接写入（默认生效）
            facts[key] = {"value": value, "confidence": conf, "type": ftype,
                          "updated_at": now, "active": True}
            changed += 1
        elif conf >= float(old.get("confidence", 0)):
            # latest-wins：新值置信度不低于旧值 → 覆盖（保留原生效开关状态）
            facts[key] = {"value": value, "confidence": conf, "type": ftype,
                          "updated_at": now, "active": old.get("active", True)}
            changed += 1
        # 否则：新值置信度更低，保留旧值，跳过（防低置信度覆盖）
    return profile, changed


# 核心事实：定义 agent 人格/身份/语言风格，永远保留、绝不因上下文预算被截断。
# 既识别显式 type，也用 key 兜底（早期档案的 agent_role/agent_style 无 type 字段）。
_CORE_TYPES = {"preference", "role"}
_CORE_KEY_HINTS = ("role", "style", "persona", "name", "nickname", "pref",
                   "answer_style", "affection", "address")


def _is_core(key: str, v: dict) -> bool:
    if v.get("type") in _CORE_TYPES:
        return True
    k = (key or "").lower()
    return any(h in k for h in _CORE_KEY_HINTS)


def _fact_line(k: str, v: dict) -> str:
    t = v.get("type")
    tag = "偏好" if t == "preference" else ("角色" if t == "role" else "事实")
    val = v["value"]
    if isinstance(val, list):          # 多条记录（如 important_notes）渲染成可读文本
        val = "；".join(str(x) for x in val)
    return f"- [{tag}] {k}: {val}"


def _fact_ts(item) -> "datetime":
    try:
        return datetime.fromisoformat(item[1].get("updated_at", ""))
    except Exception:
        return datetime.min


def to_context_text(profile, max_chars=2000):
    """
    把档案渲染成紧凑文本，注入 system 提示词。

    关键修正（修复“停留在昨天 / 丢失风格”两个根因）：
      1) 分两层：核心事实（身份/角色/风格/偏好）永远置顶且【不被预算截断】；
         普通事实按 updated_at 倒序（最新在前），保证“今天/近期”优先，
         旧事实才可能被预算切掉——截断方向从“丢最新的”改为“丢最旧的”。
      2) 纯渲染逻辑，不读写任何存储文件，现有记录零丢失。
    """
    facts = profile.get("facts", {})
    if not facts:
        return ""
    core, regular = [], []
    for k, v in facts.items():
        if v.get("type") == "event":     # 事件型/时间敏感事实不注入 system
            continue
        if v.get("active") is False:     # 用户手动停用的事实不注入
            continue
        (core if _is_core(k, v) else regular).append((k, v))

    # 核心：完整保留，置顶
    core_lines = [_fact_line(k, v) for k, v in core]

    # 普通：最新在前
    regular.sort(key=_fact_ts, reverse=True)
    reg_lines = [_fact_line(k, v) for k, v in regular]

    text = "\n".join(core_lines)
    # 预算只约束“普通事实”层；核心人格块哪怕超长也绝不截断
    remaining = max_chars - len(text) - 2
    for line in reg_lines:
        if remaining <= 0:
            break
        if len(line) > remaining:
            break  # 这条放不下就停，避免半句
        text += "\n" + line
        remaining -= len(line) + 1
    return text


async def _extract_call(text, api_key, api_url, model):
    """
    真正发网络请求抽取事实。text 为已拼好的 user/assistant 对话文本。
    任何失败都降级返回 []，不阻塞退出。
    """
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
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
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


async def extract_facts_from_text(text, api_key, api_url, model):
    """
    从已拼好的纯文本抽取事实。供【增量抽取】复用——
    MemoryStore 把“本次新增轮次”拼好文本后直接调用，避免重复拼装逻辑。
    """
    return await _extract_call(text, api_key, api_url, model)


async def extract_facts(messages, api_key, api_url, model):
    """
    离线抽取：把对话交给模型，提取结构化事实。
    原理：写读分离——只在会话结束调用，不占用户轮次延迟。
    返回 [{"key","value","confidence","type"}, ...]；失败降级 []。
    """
    # 只取 user/assistant 的自然语言，剔除 system 与 tool 噪音
    text = "\n".join(
        f"{m['role']}: {m.get('content', '')}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    )
    return await _extract_call(text, api_key, api_url, model)
