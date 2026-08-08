# skills/daughter_role/schema.py
# 给 LLM 看的 5 个工具描述（OpenAI/DeepSeek 兼容 schema）。
# 这些是“女儿角色”里可工具化的动作：情绪识别、记住/回想重要人事物、关心开场、引导分享。
TOOL_SCHEMA_MOOD = {
    "type": "function",
    "function": {
        "name": "detect_mood",
        "description": "分析用户文本的情绪类型、强度与建议回应风格。当用户流露出明显情绪（开心/难过/焦虑/生气/疲惫/孤独等）时调用，辅助更贴合地倾听与安抚。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "用户刚说的话"}
            },
            "required": ["text"]
        }
    }
}
TOOL_SCHEMA_SAVE = {
    "type": "function",
    "function": {
        "name": "save_important",
        "description": "记住用户提到的重要的人、事、计划或喜好（如家人近况、想去的地方、他的小习惯）。之后可以自然地接回话题，让他感觉你一直记着。",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "要记的主体，如人名/事件名/计划名"},
                "detail": {"type": "string", "description": "具体细节描述"},
                "kind": {"type": "string", "description": "类别：人/事/计划/喜好/其他，默认 其他"}
            },
            "required": ["subject", "detail"]
        }
    }
}
TOOL_SCHEMA_RECALL = {
    "type": "function",
    "function": {
        "name": "recall_important",
        "description": "按关键词找回之前记住的重要人/事，用于延续此前聊过的话题。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，如人名、事件名"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 5"}
            },
            "required": ["query"]
        }
    }
}
TOOL_SCHEMA_CHECKIN = {
    "type": "function",
    "function": {
        "name": "daily_checkin",
        "description": "生成贴合时段（morning/afternoon/evening/anytime）的关心开场白建议，用于主动陪伴、自然开启闲聊。",
        "parameters": {
            "type": "object",
            "properties": {
                "moment": {"type": "string", "description": "时段：morning/afternoon/evening/anytime，默认 anytime"}
            },
            "required": []
        }
    }
}
TOOL_SCHEMA_FOLLOWUP = {
    "type": "function",
    "function": {
        "name": "suggest_followup",
        "description": "针对用户提到的某件事，给出温柔的追问示例，鼓励他多分享日常点滴。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "用户提到的事件/话题，用于生成相关追问"}
            },
            "required": []
        }
    }
}
TOOLS = [TOOL_SCHEMA_MOOD, TOOL_SCHEMA_SAVE, TOOL_SCHEMA_RECALL,
         TOOL_SCHEMA_CHECKIN, TOOL_SCHEMA_FOLLOWUP]
