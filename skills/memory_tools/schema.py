# skills/memory_tools/schema.py
# 记忆读写工具 schema。
TOOL_SCHEMA_WRITE_MEMORY = {
    "type": "function",
    "function": {
        "name": "write_memory",
        "description": "把一条稳定事实或用户偏好写入长期记忆（LTM 档案卡）。用户明确要求「记住 / 记下来 / 写进记忆 / 存档」时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "事实键，语义化英文小写下划线，如 pref_drink / recent_work / name"},
                "value": {"type": "string", "description": "事实内容，如 冰美式 / 修好了 DSML bug"},
                "fact_type": {"type": "string", "enum": ["fact", "preference"], "description": "fact=客观事实；preference=偏好/意图", "default": "fact"},
                "confidence": {"type": "number", "description": "置信度 0~1，默认 0.9", "minimum": 0, "maximum": 1, "default": 0.9}
            },
            "required": ["key", "value"]
        }
    }
}

TOOL_SCHEMA_SAVE_IMPORTANT = {
    "type": "function",
    "function": {
        "name": "save_important",
        "description": "把一条重要事项/提醒保存到长期记忆（可追加多条）。用户说「记下来 / 记住 / 保存重要 / 帮我记着」时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要保存的重要事项内容"}
            },
            "required": ["text"]
        }
    }
}

TOOL_SCHEMA_RECALL_IMPORTANT = {
    "type": "function",
    "function": {
        "name": "recall_important",
        "description": "读取长期记忆里保存的所有重要事项。用户问「我之前记过什么 / 提醒我」时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}

TOOLS = [TOOL_SCHEMA_WRITE_MEMORY, TOOL_SCHEMA_SAVE_IMPORTANT, TOOL_SCHEMA_RECALL_IMPORTANT]
