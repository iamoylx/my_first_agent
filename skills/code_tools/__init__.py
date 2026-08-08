# skills/code_tools/__init__.py
from .schema import TOOLS
from .skill import (
    read_file,
    list_dir,
    search_files,
    search_content,
    run_command,
)

# 实际可调用的函数表：name -> async 函数
TOOL_MAP = {
    "read_file": read_file,
    "list_dir": list_dir,
    "search_files": search_files,
    "search_content": search_content,
    "run_command": run_command,
}
