# -*- coding: utf-8 -*-
"""提醒任务 skill：对话中创建/查看/删除到点提醒。

工具通过 task_store 参数注入（由 core._call_tool 识别并传入）；
未注入时返回友好提示，不崩溃（终端版等未接线场景）。
"""
import json

from .store import TaskStore  # noqa: F401  (重导出，便于集成方 import)


# ===================== 工具实现（task_store 由 _call_tool 注入）=====================
async def create_reminder(reminder: str, when: str, repeat: str = "none", task_store=None) -> str:
    """创建一条到点提醒。when 传 YYYY-MM-DD HH:MM（模型按系统提示里的当前时间推算）。"""
    if task_store is None:
        return "错误：提醒服务未初始化"
    tid, err = task_store.add(reminder, when, repeat)
    if err:
        return f"错误：{err}"
    return f"✅ 已创建提醒 #{tid}：{reminder}（{when}）"


async def list_reminders(task_store=None) -> str:
    """列出所有未完成的提醒任务。"""
    if task_store is None:
        return "错误：提醒服务未初始化"
    items = task_store.list()
    if not items:
        return "目前没有未完成的提醒任务～"
    lines = ["当前未完成提醒："]
    for t in items:
        rep = {"none": "", "daily": "（每天）", "weekly": "（每周）"}.get(t.get("repeat"), "")
        lines.append(f"  #{t['id']} {t['when']} {t['reminder']}{rep}")
    return "\n".join(lines)


async def delete_reminder(id: str, task_store=None) -> str:
    """删除一条提醒任务（按 id）。"""
    if task_store is None:
        return "错误：提醒服务未初始化"
    if task_store.delete(id):
        return f"🗑️ 已删除提醒 #{id}"
    return f"未找到提醒 #{id}"


# ===================== 注册给 LLM =====================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "创建一条到点提醒任务。当用户说「提醒我/到点叫我/记得提醒/明天下午三点提醒我开会」时使用。"
                "when 传绝对时间，格式 YYYY-MM-DD HH:MM，根据系统提示里的当前时间推算（如当前是 2026-08-11 22:00，"
                "明天 15:00 就传 2026-08-12 15:00）；repeat 传 none（一次）/daily（每天）/weekly（每周）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder": {"type": "string", "description": "提醒内容，如：下午三点开会"},
                    "when": {"type": "string", "description": "提醒时间 YYYY-MM-DD HH:MM"},
                    "repeat": {"type": "string", "enum": ["none", "daily", "weekly"],
                               "description": "重复方式：none 一次 / daily 每天 / weekly 每周"},
                },
                "required": ["reminder", "when"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "列出所有未完成的提醒任务（用户问「有哪些提醒/我的任务」时使用）。",
            "parameters": {"type": "object", "properties": {}},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": "删除一条提醒任务（用户说「取消提醒/删掉任务」时使用）。",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "提醒任务 id，如 r1234567890"}},
                "required": ["id"],
            },
        }
    },
]

TOOL_MAP = {
    "create_reminder": create_reminder,
    "list_reminders": list_reminders,
    "delete_reminder": delete_reminder,
}
