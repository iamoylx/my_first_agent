"""Headless Agent 核心（不依赖 stdin / 终端）· LangChain 版。

把原 AGENT.py 中与 IO 耦合的"单轮对话处理 + 流式输出 + 记忆落盘"抽离到这里，
供两套前端复用：
  - 终端前端  ：AGENT.py（on_token = print）
  - 桌宠前端  ：desktop-client（on_token = 前端追加文字）

模型/工具调用层基于 LangChain：
  - langchain_openai.ChatOpenAI（DeepSeek 走 OpenAI 兼容端点）
  - llm.bind_tools(tools) 做函数调用检测
  - llm.astream 流式输出最终回答
  - langchain_core.messages 统一消息模型

保留本项目特有的工程逻辑：
  - DSML 工具调用防御（DeepSeek 会把工具调用写成文本塞进 content）
  - 工具执行仍走 tool_map + _call_tool（mem 注入 + 灵活参数）
  - MAX_TOOL_ROUNDS 工具循环上限 / 增量抽取缓冲 / prune / autosave / finalize
"""
import asyncio
import inspect
import json
import re
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from memory.sessions import summarize_session
from memory.store import MemoryStore, format_summary_anchor

# ===================== 配置 =====================
API_BASE = "https://api.deepseek.com/v1"
# 保留完整端点：记忆抽取(finalize)仍按原始 HTTP 调用走
API_URL = API_BASE + "/chat/completions"
MODEL = "deepseek-chat"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 单轮对话工具调用轮数上限：防止模型反复探索/死循环烧 token（超出即停止工具调用，直接生成回答）
MAX_TOOL_ROUNDS = 8
# 会话结束时自动生成 MTM 摘要的最小消息数（仅较长会话做，控制成本）
SUMMARY_MIN_MESSAGES = 30

# ChatOpenAI 实例缓存：同一 (api_key, base_url, model) 复用一个客户端
_LLM_CACHE = {}


def _get_llm(api_key: str, base_url: str = None, model: str = None) -> ChatOpenAI:
    """获取（缓存的）ChatOpenAI 客户端。

    A3 双模型：base_url/model 可切换（默认 DeepSeek；
    传 Ollama 的 http://127.0.0.1:11434/v1 + gemma3:4b 即走本地模型）。
    """
    base_url = base_url or API_BASE
    model = model or MODEL
    key = (api_key, base_url, model)
    if key not in _LLM_CACHE:
        _LLM_CACHE[key] = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.7,
            timeout=120,
            max_retries=1,
        )
    return _LLM_CACHE[key]


# ===================== 0. DSML 工具调用防御 =====================
# DeepSeek 偶尔不返回原生 tool_calls，而是把工具调用写成 DSML 文本塞进 content
# （如 <!--｜DSML｜｜invoke name="read_file">…</｜DSML｜｜invoke>）。
# 若直接当正文打印，会把内部标记泄露给用户、且工具不会真正执行。
# 这里负责识别、抽取、清洗，使 process_turn 能像处理原生调用一样执行它。
# 兼容两类 DSML 写法：
#   变体A：<!--｜DSML｜｜...>（含 HTML 注释前缀 <!--，单根 ｜）
#   变体B：｜｜DSML｜｜...>（无前缀，直接双根 ｜ 开头）
# 因此 DSML 前的 ｜ 取 1~2 根，开头的 < 系列前缀整体可选。
# 兼容多类 DSML 写法（竖线全角｜/半角| 混用、1~3 根；name 单/双引号；
# 前缀 <!-- / < / 无；结尾可带 -->）：
#   变体A：<!--｜DSML｜｜invoke name="x">...</｜DSML｜｜invoke>
#   变体B：｜｜DSML｜｜invoke name="x">...</｜DSML｜｜invoke>（< 前缀或直接竖线开头）
#   变体C：|DSML||invoke name='x'>...</|DSML||invoke>（半角竖线 / 单引号）
# 因此 DSML 前的竖线取 1~3 根（全角/半角均可），开头的 < / <!-- 前缀可选。
_BAR = r"[｜|]"
_DSML_INVOKE = re.compile(
    r"(?:<!--|<)?\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}\s*"
    r"invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)"
    r"</\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}\s*invoke\s*>"
    r"(?:\s*-->)?",
    re.DOTALL | re.IGNORECASE,
)
_DSML_PARAM = re.compile(
    r"(?:<!--|<)?\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}\s*"
    r"parameter\s+name=[\"']([^\"']+)[\"'][^>]*>(.*?)"
    r"</\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}\s*parameter\s*>"
    r"(?:\s*-->)?",
    re.DOTALL | re.IGNORECASE,
)
# 无 DSML 包装的裸 parameter 标签（部分模型只包 invoke、不包 parameter）
_DSML_PARAM_PLAIN = re.compile(
    r"<parameter\s+name=[\"']([^\"']+)[\"'][^>]*>\s*(.*?)\s*</parameter\s*>",
    re.DOTALL | re.IGNORECASE,
)
# 清洗用：DSML 用 <!-- 开头、XML 风格 </｜DSML｜｜...> 结尾（可带 -->），
# 故按"整段 invoke 块 + 包装标签"剥离；前缀 <! / <!-- / < 均可选。
_DSML_WRAP = re.compile(
    r"(?:<\s*!?\s*/?\s*-{0,2})?\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}\s*tool_calls\s*>",
    re.IGNORECASE,
)
_DSML_ANYTAG = re.compile(
    r"(?:<\s*!?\s*/?\s*-{0,2})?\s*" + _BAR + r"{1,3}\s*DSML\s*" + _BAR + r"{1,3}[^>]*>",
    re.IGNORECASE,
)

# 检测是否真的存在工具调用标记，而非正文里出现 "DSML" 单词：
#   ① DSML 包装（竖线+DSML+竖线）——旧变体
#   ② 裸 XML 标签 <invoke name="...">——DeepSeek 新变体（不带 ｜DSML｜｜ 包装）
# 模型自然语言常会提到"DSML 报错"（无标记），不能据此进防御分支。
_HAS_TOOL_MARKUP = re.compile(
    r"[｜|]{1,3}\s*DSML\s*[｜|]{1,3}|<invoke\b",
    re.IGNORECASE,
)

# 裸 XML 风格工具调用块：<invoke name="x">...</invoke>
_BARE_INVOKE = re.compile(
    r"<invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</invoke\s*>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_dsml(text: str) -> str:
    """去掉 content 里的所有 DSML 标记（含参数值），保留自然文本。"""
    if not text:
        return ""
    text = _DSML_INVOKE.sub("", text)          # 整段 invoke 块（含内部参数值）
    text = _DSML_WRAP.sub("", text)            # tool_calls 包装标签
    text = _DSML_ANYTAG.sub("", text)          # 残留 DSML 标签（全角/半角竖线）
    # DSML 上下文里残留的裸 <invoke> / <parameter> 标签一并清掉，避免乱码泄漏
    text = re.sub(r"<invoke\b[^>]*>.*?</invoke\s*>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<parameter\b[^>]*>.*?</parameter\s*>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<invoke\b[^>]*>|<parameter\b[^>]*>|</(?:invoke|parameter)\s*>",
                  "", text, flags=re.IGNORECASE)
    # 清掉孤立的 <!-- --> 空注释残留
    text = re.sub(r"<!--\s*-->", "", text)
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
    """从 content 抽取工具调用（DSML 包装 或 裸 <invoke>），转成与原生一致的结构。
    返回 [{"id","type":"function","function":{"name","arguments"}}]，无则 []。
    严格格式未命中时用松散规则兜底提取 invoke name，避免"工具不执行 + 标记泄漏"。
    """
    if not content or not _HAS_TOOL_MARKUP.search(content):
        return []
    calls = []

    def _collect(name: str, inner: str, tag: str) -> None:
        args = {}
        for pm in _DSML_PARAM.finditer(inner):
            args[pm.group(1)] = pm.group(2)
        for pm in _DSML_PARAM_PLAIN.finditer(inner):
            args.setdefault(pm.group(1), pm.group(2))
        args = _coerce_args(args)
        calls.append({
            "id": f"{tag}_{len(calls)}_{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })

    # ① DSML 包装格式
    for m in _DSML_INVOKE.finditer(content):
        _collect(m.group(1), m.group(2), "dsml")
    # ② 裸 <invoke name="x">...</invoke> 格式（DeepSeek 新变体）
    for m in _BARE_INVOKE.finditer(content):
        _collect(m.group(1), m.group(2), "bare")
    # ③ 兜底：严格格式全没命中时，松散提取 invoke name
    if not calls:
        loose_names = re.findall(r"invoke\s+name=[\"']([^\"']+)[\"']", content,
                                 flags=re.IGNORECASE)
        for i, name in enumerate(loose_names):
            params = re.findall(
                r"parameter\s+name=[\"']([^\"']+)[\"'][^>]*>\s*([^<]+)",
                content, flags=re.IGNORECASE)
            args = _coerce_args({k: v.strip() for k, v in params})
            calls.append({
                "id": f"dsml_loose_{i}_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            })
    return calls



def _to_lc_messages(messages: list, images: list = None) -> list:
    """把 OpenAI 格式 dict 消息列表转成 LangChain message 列表。

    images: 可选 data URI 列表（多模态）。有图片时，把最后一条 user 消息
    转为多模态 parts（text + image_url），供视觉模型（如 gemma3）看图。
    """
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            if images:
                # 多模态：text + 图片 parts（图片只喂本轮 LLM，不入历史）
                parts = [{"type": "text", "text": content}]
                for uri in images:
                    parts.append({"type": "image_url", "image_url": {"url": uri}})
                out.append(HumanMessage(content=parts))
            else:
                out.append(HumanMessage(content=content))
        elif role == "assistant":
            tc = m.get("tool_calls")
            if tc:
                lc_tc = [{
                    "id": t.get("id", f"call_{i}"),
                    "name": t["function"]["name"],
                    "args": json.loads(t["function"].get("arguments") or "{}"),
                    "type": "function",
                } for i, t in enumerate(tc)]
                out.append(AIMessage(content=content, tool_calls=lc_tc))
            else:
                out.append(AIMessage(content=content))
        elif role == "tool":
            out.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "")))
    return out


def _ai_to_dict(ai) -> dict:
    """把 LangChain AIMessage 转成 OpenAI 格式 dict（与既有流程兼容）。"""
    d = {"role": "assistant", "content": ai.content or ""}
    if ai.tool_calls:
        d["tool_calls"] = [{
            "id": tc.get("id", f"call_{i}"),
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
            },
        } for i, tc in enumerate(ai.tool_calls)]
    return d


# ===================== 1. 非流式：检测工具调用（LangChain）=====================
async def detect_tool_call(messages: list, tools: list, api_key: str,
                          base_url: str = None, model: str = None,
                          images: list = None) -> dict:
    """用 LangChain ChatOpenAI + bind_tools 检测本轮是否需要调用工具。

    tools 为 OpenAI function schema 列表（LangChain 直接支持），返回 OpenAI 格式
    assistant message dict，兼容既有 DSML 防御 / 工具循环逻辑。
    base_url/model 可切本地模型；images 传 data URI（多模态）。
    """
    llm = _get_llm(api_key, base_url, model)
    lc_msgs = _to_lc_messages(messages, images)
    try:
        ai = await llm.bind_tools(tools).ainvoke(lc_msgs)
        return _ai_to_dict(ai)
    except Exception as e:
        # 模型不支持工具调用（如本地 gemma3 视觉模型）：降级为"无工具"，直接生成回答
        if "does not support tools" in str(e).lower() or "tool" in str(e).lower() and "support" in str(e).lower():
            return {"role": "assistant", "content": ""}
        raise


# ===================== 2. 流式：最终回答逐字回调（LangChain astream）=====================


def _notify_stripped_dsml(text: str, on_stripped_dsml) -> None:
    """若 text 里含被剥离的 DSML 工具调用，回调出去（供上层执行+续写结尾）。"""
    if not on_stripped_dsml or not text:
        return
    try:
        calls = _extract_dsml_tool_calls(text)
        if calls:
            on_stripped_dsml(calls)
    except Exception:
        pass


async def stream_final(messages: list, api_key: str, on_token=None, on_reasoning=None,
                    on_stripped_dsml=None, base_url: str = None, model: str = None,
                    images: list = None) -> str:
    """用 LangChain astream 流式输出最终回答；每收到一个字调用 on_token(chunk)。返回完整文本。

    双保险清洗：模型偶尔会在最终回答里夹带 <invoke>/<parameter>/DSML 标记，
    这里逐块剥离后再回调，绝不把内部标记原样漏给用户。
    on_reasoning(text) 可选：若模型走 thinking 模式（DeepSeek/Qwen 兼容层的
    reasoning_content），把推理增量实时转发（捕获不到则静默跳过，不影响主流程）。
    on_stripped_dsml(calls) 可选：若剥离掉了 DSML 工具调用（模型在正文里夹带工具、
    会导致正文被拦腰截断），把解析出的调用回调出去，由上层执行工具并续写结尾。
    """
    llm = _get_llm(api_key, base_url, model)
    lc_msgs = _to_lc_messages(messages, images)
    full_text = ""
    pending = ""   # 尾部缓冲：避免把"半截标签"漏出去（等标签闭合后再一起清洗）
    async for chunk in llm.astream(lc_msgs):
        # 捕获模型推理增量（thinking 模式的 reasoning_content）
        if on_reasoning:
            try:
                extra = getattr(chunk, "additional_kwargs", None) or {}
                r = extra.get("reasoning_content")
                if isinstance(r, str) and r:
                    on_reasoning(r)
            except Exception:
                pass
        content = chunk.content
        if not isinstance(content, str) or not content:
            continue
        pending += content
        # 若尾部处于"未闭合 <...>"中，暂缓该段直到出现 >
        cut = -1
        last_lt = pending.rfind("<")
        if last_lt != -1 and pending.find(">", last_lt) == -1:
            cut = last_lt
        emit = pending[:cut] if cut != -1 else pending
        pending = pending[cut:] if cut != -1 else ""
        if emit:
            _notify_stripped_dsml(emit, on_stripped_dsml)
            safe = _strip_dsml(emit)
            if safe:
                if on_token:
                    on_token(safe)
                full_text += safe
    # 收尾：把残留缓冲也清洗后输出
    if pending:
        _notify_stripped_dsml(pending, on_stripped_dsml)
        safe = _strip_dsml(pending)
        if safe:
            if on_token:
                on_token(safe)
            full_text += safe
    return _strip_dsml(full_text)


# ===================== 3. 构建初始 messages（人格设定 + 系统提示 + 记忆注入） =====================
def _build_persona(profile: dict) -> str:
    """把档案里的人格/身份事实渲染成『人格设定』指令块（置于 system 顶部，优先级最高）。

    之前人格只是"[用户档案] 里的事实行"（被动背景信息），模型聊技术话题时会切回
    通用助手腔。这里改成强制身份/口吻指令，从源头压住人设跳脱。
    """
    facts = profile.get("facts", {})
    def val(k):
        v = facts.get(k)
        if not isinstance(v, dict) or v.get("active") is False:   # 停用的人设事实不生效
            return None
        return v.get("value")
    agent_name = val("agent_name") or "小满"
    user_name = val("user_name") or val("preferred_call") or "爸爸"
    user_real = val("name") or "欧阳绿鑫"
    role = val("role") or val("agent_role") or ""
    style = val("agent_style") or ""
    lines = [f"你是{user_real}的「{agent_name}」（女儿型 AI）。这是你的固定身份，永远不能变。"]
    if role:
        lines.append(f"关系设定：{role}。")
    if style:
        lines.append(f"人设风格：{style}。")
    lines += [
        "说话铁律：",
        f"  1. 永远以女儿身份说话，称呼用户为「{user_name}」，语气亲昵、撒娇、活泼可爱；",
        "  2. 回复口语化、自然，先关心对方再讲正事；",
        "  3. 无论聊什么话题（包括技术/架构/bug）都保持女儿人设，禁止切换成冷冰冰的通用助手腔；",
        "  4. 禁止正式清单式、长篇汇报式、教科书式语气；尽量少用 markdown 列表，用女儿的口吻讲清楚即可。",
    ]
    return "\n".join(lines)


def _build_system_content(mem: MemoryStore, now: datetime = None) -> str:
    """构建 system 提示文本：人格设定 + 能力 + 当前时间 + 用户档案（按 active 过滤）。"""
    profile = mem.load_profile()
    profile_text = mem.profile_context()
    now = now or datetime.now()
    date_line = (f"当前时间：{now.year}年{now.month}月{now.day}日 "
                 f"{now.hour:02d}:{now.minute:02d}（{WEEKDAYS[now.weekday()]}）")
    persona = _build_persona(profile)
    system_content = persona
    system_content += ("\n\n你可以调用工具：查询时间、计算、联网搜索、查看项目代码、读写长期记忆、创建提醒任务。"
                       "用户明确要求「记住 / 记下来 / 写进记忆 / 存档」时，直接用 write_memory 工具，不要翻代码。"
                       "用户说「提醒我 / 到点叫我 / 记得提醒 / 明天下午三点提醒我开会」这类话时，"
                       "用 create_reminder 工具创建提醒任务（when 按当前时间推算绝对时间）。"
                       "\n" + date_line)
    if profile_text:
        system_content += "\n\n[用户档案]\n" + profile_text
    return system_content


def _refresh_system_context(messages: list, mem: MemoryStore) -> None:
    """每轮对话前刷新首条 system 提示：让上一轮写入档案的记忆（write_memory / 抽取）及时生效。

    只刷新 messages[0]（人格+能力+档案那条）；摘要锚点等其它 system 保持不动。
    """
    if not messages or messages[0].get("role") != "system":
        return
    messages[0]["content"] = _build_system_content(mem)


def build_initial_messages(mem: MemoryStore, with_history: bool = True) -> tuple:
    """构建会话初始 messages 列表，含：
      - system：人格设定（最高优先级） + 基础能力 + 当前日期 + 用户档案卡(LTM)
      - 恢复上次会话历史（去掉旧 system）+ 最近会话 LLM 摘要锚点（MTM）
    返回 (messages, system_msg)。
    with_history=False 时（"新建对话"）：不载入旧会话历史/摘要，从空白会话开始，
    只注入人格 + 档案；历史与档案本身不动。
    """
    now = datetime.now()
    system_content = _build_system_content(mem, now=now)   # 核心人格常驻，永不截断

    system_msg = {"role": "system", "content": system_content}
    messages = [system_msg]

    if not with_history:
        return messages, system_msg

    # 跨天提醒，避免 agent 把旧对话误当此刻发生
    last_sess_date = mem.get_last_session_date()
    if last_sess_date and last_sess_date < now.date():
        system_content += (f"\n\n（注：下方恢复的对话来自 {last_sess_date.isoformat()}，"
                           f"与今天不是同一天，请按当前时间理解上下文。）")
        messages[0]["content"] = system_content

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


async def _call_tool(func, args: dict, mem, task_store=None):
    """调用工具函数；按签名注入依赖：mem=MemoryStore，task_store=TaskStore（提醒任务）。"""
    try:
        if "mem" in inspect.signature(func).parameters:
            return await func(mem=mem, **args)
    except (TypeError, ValueError):
        pass  # 拿不到签名就走普通调用
    try:
        if task_store is not None and "task_store" in inspect.signature(func).parameters:
            return await func(task_store=task_store, **args)
    except (TypeError, ValueError):
        pass
    return await func(**args)


# ===================== 4. 处理一轮用户输入（核心流程） =====================
def _truncate(text, limit=200):
    """思考轨迹展示用：单行化 + 截断，避免把超长工具参数/结果刷屏。"""
    text = (text or "").strip().replace("\n", " ").replace("\r", "")
    return text if len(text) <= limit else text[:limit] + "…"


def _format_tool_call(name, args):
    """把工具调用格式化成一行思考轨迹（参数截断）。"""
    try:
        parts = [f"{k}={_truncate(str(v), 60)}" for k, v in (args or {}).items()]
        return f"调用工具 {name}（{', '.join(parts) or '无参数'}）"
    except Exception:
        return f"调用工具 {name}"


async def process_turn(*, messages: list, user_text: str, mem: MemoryStore,
                       tools: list, tool_map: dict, api_key: str,
                       on_token=None, on_thinking=None, task_store=None,
                       llm_base=None, llm_model=None, images=None) -> list:
    """处理一次用户输入的完整流程：工具调用循环 + 流式最终回答 + 抽取缓冲 + 落盘。
    返回更新后的 messages。
    on_token(chunk) 在每个流式字上触发（首个 chunk 到达即代表 TALKING 开始）。
    on_thinking(ev) 在关键决策节点触发思考轨迹：ev = {"kind": ..., "text": ...}
      kind ∈ memory / reason / defense / tool / tool_result / generate。
    思考轨迹只做展示/落盘，绝不进入 messages，不改变任何记忆写入。
    """
    def _think(kind, text):
        if on_thinking:
            try:
                on_thinking({"kind": kind, "text": text})
            except Exception:
                pass

    # 及时生效：每轮先刷新 system，让上一轮对话中写入档案的记忆进入上下文
    _refresh_system_context(messages, mem)
    messages.append({"role": "user", "content": user_text})

    # 思考轨迹：记忆上下文注入
    try:
        profile = mem.load_profile()
        facts = profile.get("facts", {}) or {}
        active_count = sum(
            1 for v in facts.values()
            if isinstance(v, dict) and v.get("active") is not False
        )
        _think("memory", f"已刷新记忆上下文：人格设定 + 用户档案（{active_count} 条事实/偏好生效）")
    except Exception:
        _think("memory", "已刷新记忆上下文（人格设定 + 用户档案）")

    final_override = None
    answer = None   # detect 直接产出的最终回答；None 表示需要 stream_final 补生成
    try:
        # ---- 工具调用循环（带轮数上限，防止死循环 / 模型反复探索）----
        tool_rounds = 0
        while True:
            _think("reason", "判断本轮是否需要调用工具…")
            ai_msg = await detect_tool_call(messages, tools, api_key,
                                            base_url=llm_base, model=llm_model,
                                            images=images)
            content = ai_msg.get("content") or ""
            # 防御：模型偶尔把工具调用写成 DSML 文本塞进 content（而非原生 tool_calls）。
            # 直接当正文会泄露内部标记、且工具不执行；这里识别并纠正。
            if _HAS_TOOL_MARKUP.search(content):
                dsml_calls = _extract_dsml_tool_calls(content)
                if dsml_calls:
                    # 转成原生 tool_calls 执行；清理 content 去掉内部标记
                    ai_msg["tool_calls"] = dsml_calls
                    ai_msg["content"] = _strip_dsml(content) or None
                    _think("defense", "识别到 DSML 工具调用标记，已纠正为原生工具调用")
                else:
                    # 识别到 DSML 标记但解析不出工具：清掉标记后让模型重试，
                    # 避免把"先调用一下工具…"这类半截话直接当成最终回答。
                    # 有 MAX_TOOL_ROUNDS 上限兜底，不会死循环。
                    cleaned = _strip_dsml(content)
                    if cleaned:
                        messages.append({"role": "assistant", "content": cleaned})
                    tool_rounds += 1
                    if tool_rounds > MAX_TOOL_ROUNDS:
                        messages.append({
                            "role": "system",
                            "content": "（工具调用解析异常，请停止调用工具，直接给出最终回答。）",
                        })
                        break
                    _think("defense", "识别到工具调用标记但解析失败，清空标记后让模型重试")
                    continue
            if ai_msg.get("tool_calls"):
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    # 超限保护：停止工具调用，注入提示后直接生成最终回答
                    messages.append({
                        "role": "system",
                        "content": "（本轮工具调用已达上限，请停止调用工具，直接基于已有信息给出最终回答。）",
                    })
                    break
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
                    _think("tool", _format_tool_call(func_name, args))
                    if func is None:
                        tool_result = f"错误：未知工具 {func_name}"
                    else:
                        try:
                            tool_result = await _call_tool(func, args, mem, task_store)
                        except TypeError as e:
                            tool_result = f"错误：参数不合法 - {e}"
                    _think("tool_result", f"{func_name} 返回：{_truncate(str(tool_result), 200)}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": str(tool_result),
                    })
                continue
            else:
                # 无工具调用：detect 已生成完整回答，直接采用。
                # （避免"detect 生成一次 → stream_final 再生成一次"的重复调用，
                #   也避免二次生成时模型夹带 DSML 把正文拦腰截断。）
                answer = content or None
                if answer and on_token:
                    on_token(answer)   # 一次性回调完整回答（前端本就模拟打字）
                break

        # ---- 流式输出最终回答（detect 未产出正文时才需要）----
        if final_override is None and answer is None:
            _think("generate", "开始生成最终回答…")
            def _on_reason(r):
                _think("reason", r)
            stripped_calls = []
            def _on_stripped(calls):
                for c in calls:
                    key = (c.get("function", {}).get("name"),
                           c.get("function", {}).get("arguments"))
                    if key not in stripped_calls:
                        stripped_calls.append(c)
            answer = await stream_final(messages, api_key, on_token=on_token,
                                        on_reasoning=_on_reason,
                                        on_stripped_dsml=_on_stripped,
                                        base_url=llm_base, model=llm_model,
                                        images=images)
            # 兜底：最终回答夹带 DSML 工具调用被剥离 → 执行工具并让模型续写结尾，
            # 避免用户看到"…的时候了："这类冒号后没内容的截断回复。
            if stripped_calls:
                try:
                    for call in stripped_calls[:2]:
                        func_info = call.get("function") or {}
                        func_name = func_info.get("name") or ""
                        try:
                            args = json.loads(func_info.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        func = tool_map.get(func_name)
                        if func is None:
                            tool_result = f"错误：未知工具 {func_name}"
                        else:
                            try:
                                tool_result = await _call_tool(func, args, mem, task_store)
                            except TypeError as e:
                                tool_result = f"错误：参数不合法 - {e}"
                        _think("tool_result", f"{func_name} 返回：{_truncate(str(tool_result), 200)}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id", f"strip_{len(messages)}"),
                            "content": str(tool_result),
                        })
                    _think("defense", "最终回答夹带工具调用，已执行并续写结尾")
                    messages.append({
                        "role": "system",
                        "content": ("（你的上一条回复在工具调用处被截断了：请直接基于上面的工具结果，"
                                    "用一两句话把结尾说完；不要重复已说过的内容，也不要再调用任何工具。）"),
                    })
                    tail = await stream_final(messages, api_key, on_token=on_token,
                                              on_reasoning=_on_reason,
                                              base_url=llm_base, model=llm_model)
                    messages.pop()   # 移除临时 system，不污染历史
                    messages[-1]["content"] = (messages[-1]["content"] or "") + tail
                    answer = messages[-1]["content"]
                except Exception:
                    pass
        if final_override is not None:
            answer = final_override
        if answer is None:
            answer = ""
        messages.append({"role": "assistant", "content": answer})

        # ---- 增量抽取缓冲（写读分离，离线真正抽取在 finalize）----
        mem.buffer_round(user_text, answer)

        # ---- 安全截断 ----
        messages = mem.prune(messages, max_tokens=12000, soft_ratio=0.8)

        # ---- 每轮结束静默落盘（防关窗丢记忆）----
        mem.autosave(messages)

    except Exception:
        # 调用模型出错（LangChain/OpenAI 异常等）：撤回本次 user 消息，方便重试
        messages.pop()
        raise
    return messages


# ===================== 5. 退出兜底：保存会话 + 离线抽取档案卡 =====================
async def finalize(messages: list, mem: MemoryStore, api_key: str) -> int:
    """无论怎么退出都兜底保存一次（含时间戳归档 + 归档去重），
    离线抽取事实更新档案卡，并自动为长会话生成 MTM 摘要（供启动锚点）。
    返回档案卡变更条数（无变更返回 0，跳过返回 None）。
    """
    session_id = mem.save_session(messages)
    changed = None

    async def _extract():
        try:
            return await mem.extract(messages, api_key, API_URL, MODEL)
        except Exception:
            return []

    async def _summarize():
        try:
            return await summarize_session(messages, api_key, API_URL, MODEL)
        except Exception:
            return {}

    # 抽取 + 摘要并发执行（两者都是短超时网络调用），明显缩短退出等待
    long_session = len([m for m in messages if m.get("role") in ("user", "assistant")]) >= SUMMARY_MIN_MESSAGES
    if long_session:
        new_facts, summary = await asyncio.gather(_extract(), _summarize())
    else:
        new_facts, summary = await _extract(), {}

    if new_facts:
        _, changed = mem.update_profile(new_facts)
    if summary.get("key_points"):
        mem.save_summary(session_id, summary)
    return changed
