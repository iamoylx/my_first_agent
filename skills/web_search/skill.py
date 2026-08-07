# skills/web_search/skill.py
import asyncio
import aiohttp
import json
from .config import (SEARCH_API_URL, SEARCH_API_KEY, REQUEST_TIMEOUT,
                     MAX_RESULTS, MAX_RETRIES)
from .parser import normalize_results

async def search_web(query: str, top_k: int = 5) -> str:
    # —— 1) 入口先校验，脏数据绝不发出去 ——
    if not query or not query.strip():
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    top_k = min(max(int(top_k), 1), 10)      # 夹在 1~10 之间
    if not SEARCH_API_KEY:
        return json.dumps({"error": "未配置搜索 API Key"}, ensure_ascii=False)

    # 发给服务商的请求体（URL 来自 config，不由模型决定）
    payload = {
        "api_key": SEARCH_API_KEY,
        "query": query,
        "max_results": min(top_k, MAX_RESULTS),
    }
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    # —— 2) 有限次重试，退避 1s、2s ——
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(SEARCH_API_URL, json=payload) as resp:
                    resp.raise_for_status()          # 非 2xx 立即抛异常
                    data = await resp.json()         # 安全解析 JSON
            items = data.get("results", [])
            return normalize_results(items, query)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            if attempt == MAX_RETRIES:
                # —— 3) 降级：返回错误信息，让 LLM 自己跟用户解释 ——
                return json.dumps({"error": f"搜索失败：{e}"}, ensure_ascii=False)
            await asyncio.sleep(attempt)             # 退避后重试
