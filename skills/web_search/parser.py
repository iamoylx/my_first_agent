# skills/web_search/parser.py
import re
import html
import json
from .config import MAX_SNIPPET_LEN, MAX_TOTAL_LEN

# 匹配任何 HTML 标签，用于剥离
_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(raw: str) -> str:
    """去掉 HTML 标签、反转义实体、剔除控制字符。"""
    text = _TAG_RE.sub("", raw or "")        # 1) 去标签
    text = html.unescape(text)               # 2) &amp; &lt; -> & <
    # 3) 去掉不可打印的控制字符（保留换行和 tab）
    text = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    return text.strip()

def normalize_results(items: list, query: str) -> str:
    """
    把搜索 API 的原始条目，整理成受控的 JSON 字符串返回给 LLM。
    只返回 title/url/snippet，绝不下发原始 HTML 或任意抓取内容。
    """
    safe, total = [], 0
    for it in items:
        title = clean_text(it.get("title", ""))
        url = str(it.get("url", ""))
        snippet = clean_text(it.get("content", it.get("snippet", "")))[:MAX_SNIPPET_LEN]
        safe.append({"title": title, "url": url, "snippet": snippet})
        total += len(title) + len(url) + len(snippet)
        if total >= MAX_TOTAL_LEN:           # 超总上限就截断
            break
    return json.dumps(
        {"query": query, "results": safe, "truncated": total >= MAX_TOTAL_LEN},
        ensure_ascii=False
    )
