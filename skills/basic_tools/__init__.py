# skills/basic_tools/__init__.py
from .schema import TOOLS
from .skill import get_current_time, calculator

TOOL_MAP = {
    "get_current_time": get_current_time,
    "calculator": calculator,
}
