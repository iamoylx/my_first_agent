import asyncio
import aiohttp
import json
import os
from memory.token_window import prune  

# ============ 新增：引入技能注册表与两个技能 ============
from skills import collect_tools
from skills.web_search import TOOLS as ws_tools, TOOL_MAP as ws_map
from skills.basic_tools import TOOLS as bt_tools, TOOL_MAP as bt_map
from memory.sessions import load_last_session, save_session   # 新增：跨重启会话持久化

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
    last_msgs = load_last_session()          # 读取上次完整对话（含旧 system）
    system_msg = {"role": "system",
                  "content": "你是助手，可以调用工具查询时间、计算、联网搜索。"}
    messages = [system_msg]

    # ② 模式 A 续聊：把上次对话（去掉旧 system）接在后面，实现"接着聊"
    if last_msgs:
        body = [m for m in last_msgs if m["role"] != "system"]
        messages += body
        print(f"[系统] 已恢复上次会话（{len(body)} 条历史消息）。")

    # ③ 重载后立即裁剪，防止旧历史直接撑爆上下文窗口
    messages = prune(messages, max_tokens=12000, soft_ratio=0.8)

    print("===== DeepSeek Function Calling 终端Demo | 输入exit退出 =====")

    while True:
        try:
            user_input = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n程序退出")
            break
        if user_input.lower() == "exit":
            print("程序退出")
            break
        if not user_input:
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

            # ---- 安全截断 ----
            messages = prune(messages, max_tokens=12000, soft_ratio=0.8)

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

    # ④ 退出循环后落盘（exit / Ctrl+C break 都会走到这里）。
    #    写读分离：只在退出时写一次，启动时由 load_last_session 读回。
    save_session(messages)
    print("[系统] 会话已保存到本地。")


if __name__ == "__main__":
    asyncio.run(main())
