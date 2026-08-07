# skills/web_search/config.py
import os

# 搜索服务商地址：写死，绝不让 LLM 去拼任意 URL（防 SSRF / 投毒）
SEARCH_API_URL = "https://api.tavily.com/search"

# Key 只从环境变量读，代码里不出现明文，也绝不打印
SEARCH_API_KEY = os.getenv("TAVILY_API_KEY", "")

REQUEST_TIMEOUT = 15    # 单次请求最多等 15 秒
MAX_RESULTS = 5         # 默认返回条数上限
MAX_SNIPPET_LEN = 300   # 单条摘要最多 300 字，防止撑爆上下文
MAX_TOTAL_LEN = 4000    # 整个返回结果的最大字符数
MAX_RETRIES = 2         # 失败最多重试 2 次（带退避）
