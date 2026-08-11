# memory/sessions.py
# 中期记忆（MTM）纯函数层：会话清洗 + LLM 摘要。
# 路径与持久化逻辑已统一由 memory.store.MemoryStore 管理（数据落 memory_data/）；
# 本模块只保留与存储位置无关的纯函数，供 store / commands 复用。
import json
import aiohttp


def sanitize(messages):
    """
    落盘前清洗：去掉末尾悬空的 tool_calls / tool 消息。
    场景：assistant 调用了工具、但结果没回来用户就 Ctrl+C 退出。
    若把这种"调了工具却没结果"的 assistant 存下，下次读回时
    DeepSeek 会因缺对应的 tool 返回而报 400。所以从尾部向前弹掉。
    """
    out = list(messages)
    while out and out[-1].get("role") == "assistant" and out[-1].get("tool_calls"):
        out.pop()
    return out


# LLM 会话摘要提示词：把一段完整会话压缩为结构化锚点（标题/主题/要点/待续）。
SUMMARIZE_PROMPT = (
    "你是会话摘要器。请用中文把下面这段对话压缩为结构化摘要，"
    "以便后续在“继续对话”时作为上下文锚点注入。\n"
    "返回 JSON："
    '{"title":"一句话标题","topics":["主题1","主题2"],'
    '"key_points":["要点1","要点2"],'
    '"open_questions":"遗留的待解决问题或下一步（无则空字符串）"}。'
    "\n只返回 JSON，不要额外文字。"
)


async def summarize_session(messages, api_key, api_url, model):
    """
    MTM 摘要：把一段完整会话压缩为结构化摘要（标题/主题/要点/待续问题）。
    用于 /summary --llm 生成压缩存档，以及 /load 时的上下文注入。
    任何失败都降级返回空结构，不阻塞。
    """
    text = "\n".join(
        f"{m['role']}: {m.get('content', '')}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    )
    if not text.strip():
        return {"title": "", "topics": [], "key_points": [], "open_questions": ""}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SUMMARIZE_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(api_url, headers=headers,
                              data=json.dumps(payload).encode()) as resp:
                resp.raise_for_status()
                data = await resp.json()
        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return {"title": "", "topics": [], "key_points": [], "open_questions": ""}
