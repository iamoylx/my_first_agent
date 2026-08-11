# skills/memory_tools/__init__.py
from .schema import TOOLS
from .skill import write_memory, save_important, recall_important

TOOL_MAP = {
    "write_memory": write_memory,
    "save_important": save_important,
    "recall_important": recall_important,
}
