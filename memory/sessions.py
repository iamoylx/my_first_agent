# memory/sessions.py
# 中期记忆 + 会话持久化（跨重启续聊）。
# 核心原则：写读分离——落盘只在退出时做，读回只在启动时做，二者缺一不可。
# 只写不读 = 重启仍空记忆（这正是本项目之前的痛点）。
import json
import os
from datetime import datetime

# 当前会话文件：每次退出覆盖写，启动时整段读回，实现"接着聊"。
CURRENT_FILE = os.path.join(os.path.dirname(__file__), "sessions", "current.json")
# 带时间戳的归档：保留历史副本，供后续 MTM 摘要 / 找回更早会话使用。
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")


def _ensure_dir():
    """懒创建 sessions 目录，避免首次运行因目录不存在而写盘失败。"""
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def sanitize(messages):
    """
    落盘前清洗：去掉末尾悬空的 tool_calls / tool 消息。
    场景：assistant 调用了工具、但结果没回来用户就 Ctrl+C 退出。
    若把这种"调了工具却没结果"的 assistant 存下，下次读回时
    DeepSeek 会因缺对应的 tool 返回而报 400。所以从尾部向前弹掉。
    """
    out = list(messages)
    while out and out[-1].get("role") == "assistant" and out[-1].get("tool_calls"):
        out.pop()
    return out


def autosave(messages):
    """
    每轮对话结束后静默调用：只更新 current.json，不写归档。
    目的——即使你直接点 ✕ 关掉终端窗口、进程被系统杀死，
    最近一轮的对话也已经落到磁盘，下次启动能接上（不会丢记忆）。
    """
    _ensure_dir()
    clean = sanitize(messages)
    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def save_session(messages):
    """
    会话完整结束时（main 的 finally 兜底）调用。
    更新 current.json（供下次续聊）+ 额外写一份带时间戳归档（供后续 MTM 摘要/检索）。
    写读分离：写只在退出时做，读只在启动时由 load_last_session 做。
    """
    autosave(messages)   # 先更新 current
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(SESSIONS_DIR, f"{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(sanitize(messages), f, ensure_ascii=False, indent=2)


def load_last_session():
    """
    启动读回：返回上次完整对话（已去除旧 system，由 main 重新拼接）。
    没有历史文件时返回空列表（首次运行），调用方据此判断是否为新会话。
    """
    if not os.path.exists(CURRENT_FILE):
        return []
    with open(CURRENT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def list_archived():
    """列出所有带时间戳的归档会话文件名（供 MTM 摘要阶段使用，暂不自动调用）。"""
    if not os.path.isdir(SESSIONS_DIR):
        return []
    return sorted(
        f for f in os.listdir(SESSIONS_DIR)
        if f != "current.json" and f.endswith(".json")
    )
