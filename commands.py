# commands.py
# 终端斜杠命令：只读检索与跨会话操作，不进入 LLM 工具循环。
import os
#
# 设计原则：
#   - 命令与「工具(tools)」是两套东西：tools 交给模型自主调用；commands 由用户在
#     终端显式输入，用于检视/操纵记忆，绝不在每轮对话里偷偷消耗 token。
#   - /recall 只展示检索结果，不把历史内容注入上下文（避免重复与上下文膨胀）；
#     需要「接着聊」请用 /load 显式载入某次会话。
#   - 识别方式：以 / 开头即命令；首词恰好是命令名（无斜杠）也兼容，方便快速输入。
from collections import Counter
from memory.sessions import summarize_session


# 支持的无参数 / 有参数命令名（裸词形式仅匹配这些精确词）
_COMMAND_NAMES = {"help", "sessions", "profile", "recall", "load", "summary", "forget"}


def is_command(text: str):
    """识别输入是否为命令。返回 (name, arg)；非命令返回 None。"""
    if not text:
        return None
    # 斜杠形式：/cmd arg
    if text.startswith("/"):
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        return name, arg
    # 裸词形式：首词正好是命令名（兼容无斜杠输入，降低门槛）
    parts = text.split(maxsplit=1)
    if parts[0].lower() in _COMMAND_NAMES:
        arg = parts[1].strip() if len(parts) > 1 else ""
        return parts[0].lower(), arg
    return None


async def run_command(name, arg, mem, messages, system_msg):
    """
    执行命令。返回 (messages, handled)。
    handled=True 时主循环跳过本轮 LLM 调用；/load 会改写 messages 以实现续聊。
    """
    name = (name or "").lower()
    if name in ("help", "h", "?"):
        _print_help()
        return messages, True
    if name == "sessions":
        _print_sessions(mem)
        return messages, True
    if name == "profile":
        _print_profile(mem)
        return messages, True
    if name == "recall":
        if not arg:
            print("[系统] 用法：/recall <关键词>")
        else:
            _print_recall(mem, arg)
        return messages, True
    if name == "load":
        if not arg:
            print("[系统] 用法：/load <会话ID>，先 /sessions 查看可用ID")
            return messages, True
        loaded = mem.load_session(arg)
        if not loaded:
            print(f"[系统] 未找到会话 {arg!r}，用 /sessions 列出可用ID")
            return messages, True
        # 丢掉旧会话里的 system，拼上当前 system，再裁剪防撑爆窗口
        body = [m for m in loaded if m["role"] != "system"]
        messages = mem.prune([system_msg] + body, max_tokens=12000, soft_ratio=0.8)
        # 若该归档会话已有 LLM 摘要，注入为上下文锚点（无需整段重载）
        summ = mem.load_summary(arg)
        if summ:
            messages.insert(1, {"role": "system", "content": _format_summary_anchor(summ)})
            print("[系统] 已注入该会话的 LLM 摘要作为上下文锚点。")
        # 清空抽取缓冲：上一段会话的新增内容不应并入新会话抽取
        mem.reset_extract_buffer()
        print(f"[系统] 已载入会话 {arg}（{len(body)} 条消息），可继续对话。")
        return messages, True
    if name == "summary":
        if not arg:
            print("[系统] 用法：/summary <会话ID> [--llm]")
            return messages, True
        parts = arg.split()
        sid = parts[0]
        use_llm = "--llm" in parts[1:]
        msgs = mem.load_session(sid)
        if not msgs:
            print(f"[系统] 未找到会话 {sid!r}")
            return messages, True
        if use_llm:
            await _print_summary_llm(mem, sid, msgs)
        else:
            _print_summary_local(sid, msgs)
        return messages, True
    if name == "forget":
        if not arg:
            print("[系统] 用法：/forget <会话ID>，删除该归档会话")
            return messages, True
        ok = mem.delete_session(arg)
        print(f"[系统] 会话 {arg} {'已删除' if ok else '不存在或不可删'}")
        return messages, True
    # 未知命令
    print(f"[系统] 未知命令：{name}，输入 /help 查看可用命令。")
    return messages, True


# ===================== 各命令的具体打印 =====================
def _print_help():
    print("[系统] 可用命令：")
    print("  /help             显示本帮助")
    print("  /sessions         列出历史会话（ID / 消息数 / 修改时间）")
    print("  /profile          查看长期档案卡(LTM)")
    print("  /recall <关键词>   跨层检索档案卡+历史会话（只读，不注入上下文）")
    print("  /load <会话ID>     载入某历史会话并继续对话")
    print("  /summary <会话ID> [--llm]  本地统计摘要；加 --llm 用模型生成压缩存档")
    print("  /forget <会话ID>   删除某条归档会话")
    print("  exit              退出")


def _print_sessions(mem):
    items = mem.list_sessions()
    if not items:
        print("[系统] 暂无历史会话。")
        return
    print("[系统] 历史会话列表：")
    for it in items:
        tag = "（当前）" if it["session_id"] == "current" else ""
        print(f"  · {it['session_id']}{tag}  消息数={it['messages']}  修改于 {it['mtime']}")


def _print_profile(mem):
    p = mem.load_profile()
    facts = p.get("facts", {})
    if not facts:
        print("[系统] 档案卡为空（尚未抽取到长期事实）。")
        return
    print(f"[系统] 档案卡（更新于 {p.get('updated_at', '未知')}）：")
    for k, v in facts.items():
        val = v.get("value", "")
        conf = v.get("confidence", "")
        upd = v.get("updated_at", "")
        ftype = "偏好" if v.get("type") == "preference" else "事实"
        print(f"  · [{ftype}] {k}: {val}  (置信 {conf}, 更新 {upd})")


def _print_recall(mem, keyword):
    res = mem.retrieve(keyword)
    ph = res.get("profile_hits") or {}
    sh = res.get("session_hits") or []
    print(f"[系统] 检索“{keyword}”：档案卡命中 {len(ph)} 条，历史会话命中 {len(sh)} 个")
    if ph:
        print("── 档案卡(LTM) ──")
        for k, v in ph.items():
            ftype = "偏好" if v.get("type") == "preference" else "事实"
            print(f"  · [{ftype}] {k}: {v.get('value')}  (置信 {v.get('confidence')})")
    if sh:
        print("── 历史会话(MTM) ──")
        for h in sh:
            print(f"  ▸ 会话 {h['session_id']}")
            for line in h["matches"]:
                print(f"      {line}")
    if not ph and not sh:
        print("  （无匹配）")


def _print_summary_local(session_id, msgs):
    """本地零 LLM 开销统计摘要（消息计数/首问/调用工具）。"""
    role_count = Counter(m.get("role") for m in msgs)
    user_q = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
    tools = []
    for m in msgs:
        for tc in (m.get("tool_calls") or []):
            tools.append(tc["function"]["name"])
    print(f"[系统] 会话摘要 {session_id}（本地统计）：")
    print(f"  消息总数：{len(msgs)}"
          f"（user {role_count.get('user', 0)} / "
          f"assistant {role_count.get('assistant', 0)} / "
          f"tool {role_count.get('tool', 0)}）")
    print(f"  对话轮数：{role_count.get('user', 0)}")
    if user_q:
        print(f"  首条提问：{user_q[:80]}")
    if tools:
        # dict.fromkeys 去重保序
        print(f"  调用工具：{', '.join(dict.fromkeys(tools))}")


def _format_summary_anchor(summ):
    """把 LLM 摘要渲染成可注入 system 的上下文锚点文本。"""
    lines = ["以下是你将要继续的会话的压缩摘要（非逐字记录）："]
    if summ.get("title"):
        lines.append(f"标题：{summ['title']}")
    if summ.get("topics"):
        lines.append("主题：" + "、".join(summ["topics"]))
    if summ.get("key_points"):
        lines.append("要点：")
        lines += [f"  - {kp}" for kp in summ["key_points"]]
    if summ.get("open_questions"):
        lines.append(f"待续问题：{summ['open_questions']}")
    return "\n".join(lines)


async def _print_summary_llm(mem, session_id, msgs):
    """用 LLM 压缩会话并保存为 <id>.summary.json，然后打印。"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    api_url = "https://api.deepseek.com/v1/chat/completions"
    model = "deepseek-chat"
    if not api_key:
        print("[系统] 未配置 DEEPSEEK_API_KEY，无法生成 LLM 摘要（可用无参 /summary 看本地统计）。")
        return
    print(f"[系统] 正在用 LLM 压缩会话 {session_id} …")
    summ = await summarize_session(msgs, api_key, api_url, model)
    mem.save_summary(session_id, summ)
    print(f"[系统] 已保存 LLM 摘要到 {session_id}.summary.json：")
    print(f"  标题：{summ.get('title', '')}")
    if summ.get("topics"):
        print(f"  主题：{', '.join(summ['topics'])}")
    for kp in summ.get("key_points", []):
        print(f"   · {kp}")
    if summ.get("open_questions"):
        print(f"  待续问题：{summ['open_questions']}")
