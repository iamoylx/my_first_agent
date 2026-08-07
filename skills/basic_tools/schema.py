# skills/basic_tools/schema.py
TOOL_SCHEMA_TIME = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "用户询问当前时间时调用",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}
TOOL_SCHEMA_CALC = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "数学计算，支持加减乘除",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个数字"},
                "b": {"type": "number", "description": "第二个数字"},
                "op": {"type": "string", "description": "add/sub/mul/div"}
            },
            "required": ["a", "b", "op"]
        }
    }
}
TOOLS = [TOOL_SCHEMA_TIME, TOOL_SCHEMA_CALC]
