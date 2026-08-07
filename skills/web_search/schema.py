# skills/web_search/schema.py
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "当需要查询实时信息、新闻、最新数据，"
            "或模型自身知识之外的信息时调用。"
            "返回若干网页结果的标题、链接与摘要。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果条数，默认 5，范围 1~10"
                }
            },
            # required 只放真正必填的；Agent 分发前会据此校验
            "required": ["query"]
        }
    }
}
