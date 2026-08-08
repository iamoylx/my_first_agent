# skills/code_tools/schema.py
# 给 LLM 看的工具描述（DeepSeek/OpenAI 兼容 function-calling schema）。
# 作用：让模型知道何时调用、参数含义。实现见 skill.py。

TOOL_SCHEMA_READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取 agent 所在项目内某个代码/文本文件的内容，用于查看源码或配置。默认相对项目根目录解析路径；返回带行号范围标记的文本。大文件可分次读取。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，相对项目根目录（如 skills/code_tools/skill.py）或绝对路径"},
                "max_lines": {"type": "integer", "description": "最多返回行数，默认 200，防止超大文件撑爆上下文"},
                "start_line": {"type": "integer", "description": "起始行号（从 1 开始），用于分段读取大文件，默认 1"}
            },
            "required": ["path"]
        }
    }
}

TOOL_SCHEMA_LIST_DIR = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "列出目录下的文件与子目录，用于浏览/确认项目结构。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，相对项目根目录或绝对路径，默认 .（项目根）"}
            },
            "required": []
        }
    }
}

TOOL_SCHEMA_SEARCH_FILES = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": "按文件名或通配符（如 *.py、AGENT.py）在项目内查找文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "name_pattern": {"type": "string", "description": "文件名或通配符，支持 * 与 ?"},
                "path": {"type": "string", "description": "搜索根目录，相对项目根目录或绝对路径，默认 ."}
            },
            "required": ["name_pattern"]
        }
    }
}

TOOL_SCHEMA_SEARCH_CONTENT = {
    "type": "function",
    "function": {
        "name": "search_content",
        "description": "在文件内容中搜索关键词/正则，定位代码或配置（类似 grep）。用于“某函数在哪定义”“哪里用到某变量”。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "要搜索的关键词或正则表达式"},
                "path": {"type": "string", "description": "搜索根目录，相对项目根目录或绝对路径，默认 ."},
                "max_matches": {"type": "integer", "description": "最多返回匹配条数，默认 50"}
            },
            "required": ["keyword"]
        }
    }
}

TOOL_SCHEMA_RUN_CMD = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "在 agent 运行目录执行一条命令行指令并返回输出，用于简单操作电脑（如查看环境、运行脚本、git 状态、编译）。内置危险指令拦截（递归删除/格式化/关机/提权等），非沙箱，请谨慎使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令，如 python --version、git status"}
            },
            "required": ["command"]
        }
    }
}

TOOLS = [
    TOOL_SCHEMA_READ_FILE,
    TOOL_SCHEMA_LIST_DIR,
    TOOL_SCHEMA_SEARCH_FILES,
    TOOL_SCHEMA_SEARCH_CONTENT,
    TOOL_SCHEMA_RUN_CMD,
]
