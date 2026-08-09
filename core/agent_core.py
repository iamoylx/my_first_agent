"""Headless Agent 核心（不依赖 stdin / 终端）。

把原 AGENT.py 中与 IO 耦合的"单轮对话处理 + 流式输出 + 记忆落盘"抽离到这里，
供两套前端复用：
  - 终端前端  ：AGENT.py（on_token = print）
  - 桌宠前端  ：pet/main.py（on_token = 聊天窗追加文字）

设计原则（解耦）：
  - core 不 import 任何具体技能，保持与具体人格解耦。
  - core 只收用户输入(user_text)、只通过回调(on_token)发事件，不主动渲染 UI。
  - 记忆 MemoryStore、技能 tool_map、环境变量沿用现有逻辑，行为保持一致。
"""
import asyncio
import aiohttp
import json
import re
from datetime import datetime

from memory.store import MemoryStore, format_summary_anchor

# ===================== 配置 =====================
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


# ===================== 0. DSML 工具调用防御 =====================
# DeepSeek 偶尔不返回原生 tool_calls，而是把工具调用写成 DSML 文本塞进 content
# （如 <!--｜DSML｜｜invoke name="read_file">…</｜DSML｜｜invoke>）。
# 若直接当正文打印，会把内部标记泄露给用户、且工具不会真正执行。
# 这里负责识别、抽取、清洗，使 process_turn 能像处理原生调用一样执行它。
# 兼容两类 DSML 写法：
#   变体A：<!--｜DSML｜｜...>（含 HTML 注释前缀 <!--，单根 ｜）
#   变体B：｜｜DSML｜｜...>（无前缀，直接双根 ｜ 开头）
# 因此 DSML 前的 ｜ 取 1~2 根，开头的 < 系列前缀整体可选。
_DSML_INVOKE = re.compile(
    r'(?:<!--)?\s*｜{1,2}\s*DSML\s*｜｜\s*invoke\s+name="([^"]+)"\s*>(.*?)'
    r'</\s*｜{1,2}\s*DSML\s*｜｜\s*invoke\s*>',
    re.DOTALL,
)
_DSML_PARAM = re.compile(
    r'(?:<!--)?\s*｜{1,2}\s*DSML\s*｜｜\s*parameter\s+name="([^"]+)"[^>]*>(.*?)'
    r'</\s*｜{1,2}\s*DSML\s*｜｜\s*parameter\s*>',
    re.DOTALL,
)
# 清洗用：DSML 用 <!-- 开头、XML 风格 </｜DSML｜｜...> 结尾，没有常规 --> 闭合，
# 故按"整段 invoke 块 + 包装标签"剥离；前缀 <!  /  <!--  均可选。
_DSML_WRAP = re.compile(
    r'(?:<\s*!?\s*/?\s*-{0,2})?\s*｜{1,2}\s*DSML\s*｜｜\s*tool_calls\s*>')
_DSML_ANYTAG = re.compile(
    r'(?:<\s*!?\s*/?\s*-{0,2})?\s*｜{1,2}\s*DSML\s*｜｜[^>]*>')


def _strip_dsml(text: str) -> str:
    """去掉 content 里的所有 DSML 标记（含参数值），保留自然文本。"""
    if not text:
        return ""
    text = _DSML_INVOKE.sub("", text)          # 整段 invoke 块（含内部参数值）
    text = _DSML_WRAP.sub("", text)            # tool_calls 包装标签
    text = _DSML_ANYTAG.sub("", text)          # 兜底：残留 DSML 标签
    return text.strip()


def _coerce_args(args: dict) -> dict:
    """DSML 参数都是字符串；按工具需要把整型/浮点字段转成数字。"""
    out = {}
    for k, v in args.items():
        s = str(v).strip()
        if re.fullmatch(r"-?\d+", s):
            out[k] = int(s)
        elif re.fullmatch(r"-?\d+\.\d+", s):
            out[k] = float(s)
        else:
            out[k] = s
    return out


def _extract_dsml_tool_calls(content: str) -> list:
    """从 content 抽取 DSML 形式的工具调用，转成与原生一致的结构。
    返回 [{"id","type":"function","function":{"name","arguments"}}]，无则 []。
    """
    if not content or "DSML" not in content:
        return []
    calls = []
    for i, m in enumerate(_DSML_INVOKE.finditer(content)):
        name = m.group(1)
        inner = m.group(2)
        args = {pm.group(1): pm.group(2) for pm in _DSML_PARAM.finditer(inner)}
        args = _coerce_args(args)
        calls.append({
            "id": f"dsml_{i}_{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return calls


# ===================== 1. 非流式：检测工具调用 =====================
async def detect_tool_call(messages: list, tools: list, api_key: str) -> dict:
    """向 DeepSeek 发起非流式请求，检测本轮是否需要调用工具。返回 assistant message。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "temperature": 0.7,
        "tools": tools,
        "tool_choice": "auto",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(API_URL, headers=_headers(api_key),
                                data=json.dumps(payload).encode("utf-8")) as resp:
            resp.raise_for_status()
            return (await resp.json())["choices"][0]["message"]


# ===================== 2. 流式：最终回答逐字回调 =====================
async def stream_final(messages: list, api_key: str, on_token=None) -> str:
    """流式输出最终回答；每收到一个字调用 on_token(chunk)。返回完整文本。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }
    full_text = ""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.post(API_URL, headers=_headers(api_key),
                                data=json.dumps(payload).encode("utf-8")) as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                    content = delta.get("content", "")
                    if content:
                        if on_token:
                            on_token(content)   # 回调：UI 自行决定如何渲染
                        full_text += content
                except json.JSONDecodeError:
                    continue
    return full_text


# ===================== 3. 构建初始 messages（系统提示 + 记忆注入） =====================
def build_initial_messages(mem: MemoryStore) -> tuple:
    """构建会话初始 messages 列表，含：
      - system：基础角色 + 当前日期 + 用户档案卡(LTM)
      - 恢复上次会话历史（去掉旧 system）
      - 最近会话 LLM 摘要锚点（MTM）
    返回 (messages, system_msg)。
    """
    profile_text = mem.profile_context()
    now = datetime.now()
    date_line = (f"当前时间：{now.year}年{now.month}月{now.day}日 "
                 f"{now.hour:02d}:{now.minute:02d}（{WEEKDAYS[now.weekday()]}）")
    system_content = ("你是一个 AI 助手，可以调用工具查询时间、计算、联网搜索、查看项目代码。"
                      "\n" + date_line)
    if profile_text:
        system_content += "\n\n[用户档案]\n" + profile_text   # 核心人格常驻，永不截断

    # 跨天提醒，避免 agent 把旧对话误当此刻发生
    last_sess_date = mem.get_last_session_date()
    if last_sess_date and last_sess_date < now.date():
        system_content += (f"\n\n（注：下方恢复的对话来自 {last_sess_date.isoformat()}，"
                           f"与今天不是同一天，请按当前时间理解上下文。）")

    system_msg = {"role": "system", "content": system_content}
    messages = [system_msg]

    # 续聊：恢复上次完整对话（去掉旧 system）
    last_msgs = mem.load_last_session()
    if last_msgs:
        messages += [m for m in last_msgs if m["role"] != "system"]

    # 启动锚点：注入最近会话的 LLM 摘要，避免整段重载长历史
    recent_sum = mem.get_recent_summary()
    if recent_sum:
        messages.insert(1, {"role": "system", "content": format_summary_anchor(recent_sum)})

    # 重载后立即裁剪，防旧历史撑爆上下文
    messages = mem.prune(messages, max_tokens=12000, soft_ratio=0.8)
    return messages, system_msg


# ===================== 4. 处理一轮用户输入（核心流程） =====================
async def process_turn(*, messages: list, user_text: str, mem: MemoryStore,
                       tools: list, tool_map: dict, api_key: str,
                       on_token=None) -> list:
    """处理一次用户输入的完整流程：工具调用循环 + 流式最终回答 + 抽取缓冲 + 落盘。
    返回更新后的 messages。
    on_token(chunk) 在每个流式字上触发（首个 chunk 到达即代表 TALKING 开始）。
    """
    messages.append({"role": "user", "content": user_text})

    final_override = None
    try:
        # ---- 工具调用循环 ----
        while True:
            ai_msg = await detect_tool_call(messages, tools, api_key)
            content = ai_msg.get("content") or ""
            # 防御：模型偶尔把工具调用写成 DSML 文本塞进 content（而非原生 tool_calls）。
            # 直接当正文会泄露内部标记、且工具不执行；这里识别并纠正。
            if "DSML" in content:
                dsml_calls = _extract_dsml_tool_calls(content)
                if dsml_calls:
                    # 转成原生 tool_calls 执行；清理 content 去掉内部标记
                    ai_msg["tool_calls"] = dsml_calls
                    ai_msg["content"] = _strip_dsml(content) or None
                else:
                    # 无法解析的 DSML：清洗后直接作为最终回答，避免泄露内部标记 / 死循环
                    answer = _strip_dsml(content) or "（已忽略一条无法识别的内部工具调用指令）"
                    messages.append({"role": "assistant", "content": answer})
                    if on_token:
                        on_token(answer)
                    final_override = answer
                    break
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

        # ---- 流式输出最终回答（DSML 无法解析分支已在上面直接落盘）----
        if final_override is None:
            answer = await stream_final(messages, api_key, on_token=on_token)
            messages.append({"role": "assistant", "content": answer})

        # ---- 增量抽取缓冲（写读分离，离线真正抽取在 finalize）----
        mem.buffer_round(user_text, answer)

        # ---- 安全截断 ----
        messages = mem.prune(messages, max_tokens=12000, soft_ratio=0.8)

        # ---- 每轮结束静默落盘（防关窗丢记忆）----
        mem.autosave(messages)

    except aiohttp.ClientResponseError as e:
        # 调用模型出错：撤回本次 user 消息，方便重试
        messages.pop()
        raise
    return messages


# ===================== 5. 退出兜底：保存会话 + 离线抽取档案卡 =====================
async def finalize(messages: list, mem: MemoryStore, api_key: str) -> int:
    """无论怎么退出都兜底保存一次（含时间戳归档），并离线抽取事实更新档案卡。
    返回档案卡变更条数（无变更返回 0，跳过返回 None）。
    """
    mem.save_session(messages)
    try:
        new_facts = await mem.extract(messages, api_key, API_URL, MODEL)
        if new_facts:
            _, changed = mem.update_profile(new_facts)
            return changed
    except Exception:
        return None
    return 0
