# -*- coding: utf-8 -*-
from .skill import get_weather

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的当前天气和未来 3 天预报。用户问天气/今天冷不冷/要不要带伞时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名（中文，如 重庆/南昌/江西）"},
                    "lat": {"type": "number", "description": "可选：纬度（与 lon 成对，跳过城市定位）"},
                    "lon": {"type": "number", "description": "可选：经度（与 lat 成对）"},
                },
            },
        }
    },
]

TOOL_MAP = {"get_weather": get_weather}
