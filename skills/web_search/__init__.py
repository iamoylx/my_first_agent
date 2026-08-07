# skills/web_search/__init__.py
from .schema import TOOL_SCHEMA
from .skill import search_web

TOOLS = [TOOL_SCHEMA]                 # 给 LLM 看的描述
TOOL_MAP = {"web_search": search_web} # 实际可调用的函数
