import asyncio
import aiohttp
import json
import os
# ============ 统一记忆层：STM/MTM/LTM 一体，支持 user/session 维度隔离 ============
from memory.store import MemoryStore, format_summary_anchor
# user_id 可经环境变量 AGENT_USER_ID 切换，实现多用户记忆隔离；默认 default。
mem = MemoryStore(user_id=os.getenv("AGENT_USER_ID", "default"))

# ============ 新增：引入技能注册表与两个技能 ============
from skills import collect_tools
from skills.web_search import TOOLS as ws_tools, TOOL_MAP as ws_map
from skills.basic_tools import TOOLS as bt_tools, TOOL_MAP as bt_map

# ============ 新增：引入终端命令分发（检索/跨会话操作，不进 LLM 循环）============
from commands import is_command, run_command

# ===================== 配置 =====================
API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# ============ 新增：启动时校验 Key，缺了早点报错 ============
if not API_KEY:
    raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY，请在运行前配置。")

# ============ 改动：tools/tool_map 改为自动聚合，不再手写 ============
tools, tool_map = collect_tools((ws_tools, ws_map), (bt_tools, bt_map))

# ===================== 1. 非流式：检测是否调用工具 =====================
async def llm_detect_tool_call(messages: list):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "temperature": 0.7,
        "tools": tools,          # 现在来自注册表聚合结果
        "tool_choice": "auto"
    }
    # ============ 改动：抽出统一请求函数，加超时与状态码检查 ============
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(API_URL, headers=headers,
                                data=json.dumps(payload).encode("utf-8")) as resp:
            resp.raise_for_status()              # 非 2xx 抛异常，不再静默
            resp_json = await resp.json()
    return resp_json["choices"][0]["message"]

# ===================== 2. 流式：最终回答逐字打印 =====================
async def llm_stream_final_answer(messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0.7
    }
    full_text = ""
    # ============ 改动：流式也加超时；先 raise_for_status 再读流 ============
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.post(API_URL, headers=headers,
                                data=json.dumps(payload).encode("utf-8")) as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"]
                        content = delta.get("content", "")
                        if content:
                            print(content, end="", flush=True)
                            full_text += content
                    except json.JSONDecodeError:
                        continue
    print()
    return full_text




# ============ 改写 main：用 try/except 包住每一轮 ============
async def main():
    # ===== ① 启动即读回：跨重启连续的关键（只写不读 = 重启仍空记忆）=====
    # 注：首次运行会从旧扁平路径（memory/profile.json、memory/sessions/）
    #     只读回退已有数据，旧文件不会被改写或删除。
    last_msgs = mem.load_last_session()      # 读取上次完整对话（含旧 system）
    # ===== 新增：载入 LTM 用户档案卡，渲染进 system 提示词（latest-wins 已落盘）=====
    profile = mem.load_profile()
    profile_text = mem.profile_context()
    system_content = "你是助手，可以调用工具查询时间、计算、联网搜索。"
    if profile_text:
        system_content += "\n\n[用户档案]\n" + profile_text   # 常驻注入，≤上下文预算
    system_msg = {"role": "system", "content": system_content}
    messages = [system_msg]

    # ② 模式 A 续聊：把上次对话（去掉旧 system）接在后面，实现"接着聊"
    if last_msgs:
        body = [m for m in last_msgs if m["role"] != "system"]
        messages += body
        print(f"[系统] 已恢复上次会话（{len(body)} 条历史消息）。")
        if profile_text:
            print("[系统] 已载入用户档案卡（LTM）。")

    # ②-2 启动锚点：若上一段会话曾生成过 LLM 摘要，注入为上下文锚点，
    #     让 agent 一开机就“知道之前聊到哪”，无需整段重载长历史。
    recent_sum = mem.get_recent_summary()
    if recent_sum:
        messages.insert(1, {"role": "system", "content": format_summary_anchor(recent_sum)})
        print("[系统] 已注入最近会话的 LLM 摘要作为上下文锚点。")

    # ③ 重载后立即裁剪，防止旧历史直接撑爆上下文窗口
    messages = mem.prune(messages, max_tokens=12000, soft_ratio=0.8)

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

            messages.append({"role": "user", "content": user_input})

            try:
                # ---- 工具调用循环 ----
                while True:
                    ai_msg = await llm_detect_tool_call(messages)
                    if ai_msg.get("tool_calls"):
                        messages.append(ai_msg)
                        for tool_call in ai_msg["tool_calls"]:
                            call_id = tool_call["id"]
                            func_info = tool_call["function"]
                            func_name = func_info["name"]
                            try:
                                args = json.loads(func_info["arguments"] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            func = tool_map.get(func_name)
                            if func is None:
                                tool_result = f"错误：未知工具 {func_name}"
                            else:
                                try:
                                    tool_result = await func(**args)
                                except TypeError as e:
                                    tool_result = f"错误：参数不合法 - {e}"
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": str(tool_result),
                            })
                        continue
                    else:
                        break

                # ---- 流式输出最终回答 ----
                print("AI：", end="", flush=True)
                answer = await llm_stream_final_answer(messages)
                messages.append({"role": "assistant", "content": answer})

                # ===== 新增：累积本轮到抽取缓冲（增量抽取，省 token）=====
                mem.buffer_round(user_input, answer)

                # ---- 安全截断 ----
                messages = mem.prune(messages, max_tokens=12000, soft_ratio=0.8)

                # ===== 新增：每轮结束自动落盘（静默）=====
                # 关键：即使你直接点 ✕ 关掉终端窗口、进程被系统杀死，
                # 最近一轮的对话也已经写入磁盘，重启后能接上，不会丢记忆。
                mem.autosave(messages)

            # 关键：捕获 API 错误，打印提示后继续循环，而不是让整个程序崩溃
            except aiohttp.ClientResponseError as e:
                print(f"\n[系统] 调用模型出错：HTTP {e.status} {e.message}")
                print("[系统] 本轮已跳过，你可以重新输入。")
                messages.pop()          # 撤回本次 user 消息，方便重试
                continue
            except Exception as e:
                print(f"\n[系统] 发生意外错误：{e}")
                messages.pop()
                continue
    finally:
        # ④ 无论怎么退出（exit / Ctrl+C / 异常）都兜底保存一次（含时间戳归档）。
        #    写读分离：退出时写，启动时由 mem.load_last_session 读回。
        mem.save_session(messages)
        print("[系统] 会话已保存到本地。")

        # ===== 新增：会话结束离线抽取 → 更新档案卡（latest-wins 状态复写）=====
        # 写读分离：这里才会调一次 LLM 抽取事实，不占用户发言轮次的延迟。
        try:
            new_facts = await mem.extract(messages, API_KEY, API_URL, MODEL)
            if new_facts:
                _, changed = mem.update_profile(new_facts)
                if changed:
                    print(f"[系统] 档案卡已更新（新增/覆盖 {changed} 条事实）。")
        except Exception as e:
            print(f"[系统] 档案卡抽取跳过：{e}")


if __name__ == "__main__":
    asyncio.run(main())
