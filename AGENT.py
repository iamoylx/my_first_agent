import asyncio
import aiohttp
import json
import os
from datetime import datetime
# ============ 统一记忆层：STM/MTM/LTM 一体，支持 user/session 维度隔离 ============
from memory.store import MemoryStore, format_summary_anchor
# user_id 可经环境变量 AGENT_USER_ID 切换，实现多用户记忆隔离；默认 default。
mem = MemoryStore(user_id=os.getenv("AGENT_USER_ID", "default"))

# ============ 技能注册表 ============
from skills import collect_tools
from skills.web_search import TOOLS as ws_tools, TOOL_MAP as ws_map
from skills.basic_tools import TOOLS as bt_tools, TOOL_MAP as bt_map
from skills.code_tools import TOOLS as ct_tools, TOOL_MAP as ct_map
from skills.memory_tools import TOOLS as mt_tools, TOOL_MAP as mt_map

# ============ 终端命令分发 ============
from commands import is_command, run_command

# ============ 抽离的 headless 核心（终端 / 桌宠共用）============
from core import agent_core as core

# ===================== 配置 =====================
API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# ============ 新增：启动时校验 Key，缺了早点报错 ============
if not API_KEY:
    raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY，请在运行前配置。")

# ============ 改动：tools/tool_map 改为自动聚合，不再手写 ============
tools, tool_map = collect_tools((ws_tools, ws_map), (bt_tools, bt_map),
                                (ct_tools, ct_map), (mt_tools, mt_map))

# ===================== 1 & 2. 模型调用已抽离至 core/agent_core.py =====================
# 原 llm_detect_tool_call / llm_stream_final_answer 已迁移到 core 包，
# 终端与桌宠前端通过 core.detect_tool_call / core.stream_final / core.process_turn 复用，
# 行为保持一致（流式改回调 on_token，终端用 print 还原）。




# ============ 改写 main：用 try/except 包住每一轮 ============
async def main():
    # ① 构建初始会话：系统提示 + 档案(LTM) + 女儿人格 + 恢复历史 + 摘要锚点
    #    逻辑统一在 core.build_initial_messages，终端行为与之前完全一致。
    messages, system_msg = core.build_initial_messages(mem)

    # 启动提示（保留原输出，帮助用户确认记忆已载回）
    if len(messages) > 1:
        print(f"[系统] 已恢复上次会话（{len(messages) - 1} 条历史消息）。")
        if mem.profile_context():
            print("[系统] 已载入用户档案卡（LTM）。")
    if mem.get_recent_summary():
        print("[系统] 已注入最近会话的 LLM 摘要作为上下文锚点。")

    print("===== DeepSeek Function Calling 终端Demo | 输入exit退出 =====")

    try:
        while True:
            try:
                user_input = input("\n你：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[系统] 已退出。")
                break
            if user_input.lower() == "exit":
                print("[系统] 已退出。")
                break
            if not user_input:
                continue

            # ===== 新增：斜杠命令分发（只读检索/跨会话操作，不进 LLM 循环）=====
            # 命中命令则执行并跳过本轮模型调用；/load 会改写 messages 实现续聊。
            cmd = is_command(user_input)
            if cmd:
                name, arg = cmd
                messages, _ = await run_command(name, arg, mem, messages, system_msg)
                continue

            # ===== 调用 headless core 处理本轮（工具循环 + 流式 + 落盘）=====
            # on_token=print 还原"逐字打印"；core 内部已含 buffer_round/prune/autosave。
            print("AI：", end="", flush=True)
            try:
                messages = await core.process_turn(
                    messages=messages,
                    user_text=user_input,
                    mem=mem,
                    tools=tools,
                    tool_map=tool_map,
                    api_key=API_KEY,
                    on_token=lambda t: print(t, end="", flush=True),
                )
                print()   # 流式结束后换行
            except aiohttp.ClientResponseError as e:
                print(f"\n[系统] 调用模型出错：HTTP {e.status} {e.message}")
                print("[系统] 本轮已跳过，你可以重新输入。")
                continue
            except Exception as e:
                print(f"\n[系统] 发生意外错误：{e}")
                messages.pop()   # 撤回本次 user 消息，方便重试
                continue
    finally:
        # ④ 退出兜底：保存会话 + 离线抽取档案卡（统一在 core.finalize）
        changed = await core.finalize(messages, mem, API_KEY)
        print("[系统] 会话已保存到本地。")
        if changed is None:
            print("[系统] 档案卡抽取跳过。")
        elif changed:
            print(f"[系统] 档案卡已更新（新增/覆盖 {changed} 条事实）。")


if __name__ == "__main__":
    asyncio.run(main())
