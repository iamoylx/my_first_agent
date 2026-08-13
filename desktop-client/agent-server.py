"""Agent HTTP Bridge —— 把 agent_core 暴露为本地 REST API。

Tauri 前端通过 localhost:18789 调用，实现：
  - POST /chat          发送消息，流式返回（SSE）
  - GET  /history       获取当前会话历史
  - POST /reset         重置会话（新建）
  - GET  /health        健康检查
  - GET  /assets/*      静态素材代理（解决跨域/路径问题）
  - POST /finalize      会话结束兜底：归档+离线抽取后退出服务（桌面退出时调用）

记忆系统：只读对接 memory_data/，不修改任何数据文件。
启动方式：python agent-server.py（由 Tauri Rust 侧自动拉起子进程）
端口：18789（可在环境变量 AGENT_PORT 覆盖）
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ============ 把项目根目录加入 sys.path，使 import core / memory / skills 正常工作 ============
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # desktop-client/../ -> AGENT/
sys.path.insert(0, str(PROJECT_ROOT))

from aiohttp import web, WSMsgType

# ============ Agent 核心导入（与 AGENT.py 一致）============
from memory.store import MemoryStore, format_summary_anchor
from skills import collect_tools
from skills.web_search import TOOLS as ws_tools, TOOL_MAP as ws_map
from skills.basic_tools import TOOLS as bt_tools, TOOL_MAP as bt_map
from skills.code_tools import TOOLS as ct_tools, TOOL_MAP as ct_map
from skills.memory_tools import TOOLS as mt_tools, TOOL_MAP as mt_map
from skills.reminder_tools import TOOLS as rt_tools, TOOL_MAP as rt_map
from skills.reminder_tools.store import TaskStore
from skills.weather import TOOLS as w_tools, TOOL_MAP as w_map
from skills.health_record import TOOLS as hr_tools, TOOL_MAP as hr_map
from skills.agnes_gen import TOOLS as ag_tools, TOOL_MAP as ag_map
import skills.agnes_gen as agnes_gen
try:
    # 视觉 skill（可插拔）：用户可安装更好的开源视觉 skill，只要提供
    # async describe_image(data_url, api_key, base_url, model) -> str 即可接入
    from skills.vision import describe_image as _vision_describe
except Exception:
    _vision_describe = None
import core.agent_core as core
import active
from mcp_bridge import MCPManager

# ============ 全局状态 ============
# API Key 读自「用户级永久环境变量」（本机已配置 DEEPSEEK_API_KEY / TAVILY_API_KEY）。
# Tauri 拉起本进程时会继承父进程环境，因此正常情况下无需任何额外设置。
API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "读不到 DEEPSEEK_API_KEY。该变量应已存在于用户级永久环境变量中；"
        "若此处报错，通常是启动进程未继承环境（如以服务/计划任务方式启动），"
        "或环境变量修改后终端未重启。"
    )

# ============ A3 双模型配置 ============
# 前端「切换按钮」选择对话大脑：
#   local    → 全部走本地 Qwen3-VL-4B（离线可用，原生工具调用 + 看图）
#   deepseek → 文本走 DeepSeek；仅当带图片时该轮走本地视觉（DeepSeek 无视觉能力）
#   ""(自动) → 兼容旧逻辑：图片→本地视觉；纯文本→DeepSeek（或 AGENT_LOCAL_TEXT=1 时走本地）
AGENT_LLM_BASE = os.getenv("AGENT_LLM_BASE", "https://api.deepseek.com/v1")
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "deepseek-chat")
AGENT_LOCAL_BASE = os.getenv("AGENT_LOCAL_BASE", "http://127.0.0.1:11434/v1")
AGENT_LOCAL_MODEL = os.getenv("AGENT_LOCAL_MODEL", "qwen3-vl:8b")
# AGENT_LOCAL_TEXT=1：纯文本也走本地模型（工具调用会变弱，谨慎开启）
AGENT_LOCAL_TEXT = os.getenv("AGENT_LOCAL_TEXT") == "1"
# ============ Agnes AI（免费生图/生视频/对话）============
# 一个 key 管全部：对话(agnes-2.5-flash) + 生图(agnes-image-2.1-flash) + 生视频(agnes-video-v2.0)。
# 免费限制：RPM ~20；Key 状态异常（401）时对话会返回清晰报错，不影响 DS/本地模式。
AGNES_BASE = os.getenv("AGNES_BASE", "https://apihub.agnes-ai.com/v1")
AGNES_CHAT_MODEL = os.getenv("AGNES_CHAT_MODEL", "agnes-2.5-flash")
def _read_agnes_key() -> str:
    """Agnes API Key：优先读 desktop-client/.agnes_key（本地配置，覆盖环境变量，
    避免启动路径继承了过期/错误的会话变量导致 401）；没有再读 AGNES_API_KEY 环境变量。"""
    try:
        f = Path(__file__).resolve().parent / ".agnes_key"
        if f.exists():
            k = f.read_text(encoding="utf-8").strip()
            if k:
                return k
    except Exception:
        pass
    return (os.getenv("AGNES_API_KEY") or "").strip()


AGNES_API_KEY = _read_agnes_key()


def _intimacy_intimate() -> bool:
    """当前档案亲密程度是否为亲密（intimate）。温馨=warm/未设置=非亲密。"""
    try:
        prof = mem.load_profile()
        facts = prof.get("facts", {}) or {}
        v = facts.get("rule_intimacy_level")
        if isinstance(v, dict):
            if v.get("active") is False:
                return False
            return str(v.get("value") or "") == "intimate"
        return str(v or "") == "intimate"
    except Exception:
        return False


def _route_llm(images: bool, provider: str = "") -> tuple:
    """路由：根据前端 provider 选择本轮对话大脑。返回 (base_url, model, api_key)。

    provider: "deepseek" | "local" | "agnes" | ""(自动)。
    - agnes  ：文本/看图/工具全走 Agnes（agnes-2.5-flash 支持视觉+工具），key 用 AGNES_API_KEY
    - local  ：Ollama 本地模型（key 无所谓，Ollama 忽略）
    - deepseek + 图：DeepSeek 无视觉，先走本地视觉转文字描述（在调用方处理）
    - 亲密模式下发的图一律只走本地视觉（隐私保护：图片不上 Agnes/DeepSeek 云端）；
      温馨模式的日常图在 Agnes 模式下交给 Agnes 处理
    """
    intimate = _intimacy_intimate()
    if images and intimate:
        # 隐私优先：亲密模式发图 → 全部走本地模型（qwen3-vl 本地看图，不上云端）
        return AGENT_LOCAL_BASE, AGENT_LOCAL_MODEL, API_KEY
    if provider == "agnes":
        return AGNES_BASE, AGNES_CHAT_MODEL, AGNES_API_KEY
    if provider == "local":
        return AGENT_LOCAL_BASE, AGENT_LOCAL_MODEL, API_KEY
    if images:
        # 任何模式带图都必须走本地视觉（DeepSeek 无视觉）
        return AGENT_LOCAL_BASE, AGENT_LOCAL_MODEL, API_KEY
    if provider == "deepseek":
        return AGENT_LLM_BASE, AGENT_LLM_MODEL, API_KEY
    if AGENT_LOCAL_TEXT:
        return AGENT_LOCAL_BASE, AGENT_LOCAL_MODEL, API_KEY
    return AGENT_LLM_BASE, AGENT_LLM_MODEL, API_KEY


PORT = int(os.getenv("AGENT_PORT", "18789"))
mem = MemoryStore(base_dir=os.getenv("AGENT_MEMORY_DIR") or None,
                  user_id=os.getenv("AGENT_USER_ID", "default"))

base_tools, base_tool_map = collect_tools((ws_tools, ws_map), (bt_tools, bt_map),
                                (ct_tools, ct_map), (mt_tools, mt_map),
                                (rt_tools, rt_map),
                                (w_tools, w_map),
                                (hr_tools, hr_map),
                                (ag_tools, ag_map))
tools, tool_map = base_tools, dict(base_tool_map)

# 技能分组（用于前端「技能」按钮展示：自选 skill 命令 agent 执行）
SKILL_GROUPS = [
    ("web_search", "联网搜索", ws_tools),
    ("basic", "基础工具", bt_tools),
    ("code", "代码工具", ct_tools),
    ("memory", "记忆工具", mt_tools),
    ("reminder", "提醒任务", rt_tools),
    ("weather", "天气", w_tools),
    ("health", "健康记录", hr_tools),
    ("agnes", "Agnes 生成", ag_tools),
]

# 本地模型（qwen3-vl:4b）上下文有限（16K），只注入离线可用的核心工具，
# 避免全部工具 schema + 历史 + 图片撑爆上下文；web_search/code_tools/MCP 不注入。
LOCAL_TOOL_NAMES = {
    "get_current_time", "calculator", "web_search",
    "write_memory", "save_important", "recall_important",
    "create_reminder", "list_reminders", "delete_reminder",
    "get_weather", "record_health", "health_records",
}


def _local_tools(full_tools: list) -> list:
    """按名称过滤出本地模式可用的工具（schema 子集；tool_map 保持全量以便执行）。"""
    return [t for t in full_tools
            if (t.get("function", {}).get("name") or t.get("name")) in LOCAL_TOOL_NAMES]

# 通用 MCP 桥（B1）：启动时连接 MCP server，动态合并工具
mcp_manager = MCPManager(config_dir=PROJECT_ROOT / "mcp")


async def _sync_mcp_tools():
    """连接 MCP servers 并把工具合并进 tools/tool_map。"""
    global tools, tool_map
    await mcp_manager.start()
    mcp_tools = mcp_manager.openai_tools()
    mcp_map = mcp_manager.tool_map()
    if mcp_tools:
        tools = base_tools + mcp_tools
        tool_map = {**base_tool_map, **mcp_map}
        print(f"[MCP] 已合并 {len(mcp_tools)} 个 MCP 工具（当前共 {len(tools)} 个）")

# 当前会话 messages（内存中，启动时从记忆恢复）
messages = None
system_msg = None
chat_lock = asyncio.Lock()   # 防止并发请求打乱消息顺序

# ============ 主动触发（阶段A1）============
# 调度器只读记忆；主动消息经 WS 载体推给桌宠/主窗口 + 日志黑匣子，不进会话。
TASK_DIR = Path(os.getenv("AGENT_TASK_DIR") or (PROJECT_ROOT / "task_data"))
task_store = TaskStore(base_dir=TASK_DIR,
                     user_id=os.getenv("AGENT_USER_ID", "default"))
active_scheduler = active.ActiveScheduler(mem, log_dir=PROJECT_ROOT / "logs",
                                          task_dir=TASK_DIR,
                                          task_user_id=os.getenv("AGENT_USER_ID", "default"))
ws_carrier = active.WebSocketCarrier()
active_scheduler.register_carrier(ws_carrier)


# ============ Agnes 生成资产收集 ============
_ASSET_TAG = "@@ASSET@@"
_ASSET_RE = re.compile(r"@@ASSET@@(.*?)@@ASSET@@", re.DOTALL)


def _collect_assets(messages: list) -> list:
    """扫描会话工具结果，收集 Agnes 生成的图片/视频资产；并清理 @@ASSET@@ 机器标记，
    避免把内部标记留在历史里污染后续上下文。返回 [{kind, path}, ...]。"""
    assets = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        content = m.get("content") or ""
        if _ASSET_TAG not in content:
            continue
        for mm in _ASSET_RE.finditer(content):
            try:
                info = json.loads(mm.group(1))
                if info.get("kind") in ("image", "video") and info.get("path"):
                    assets.append({"kind": info["kind"], "path": str(info["path"])})
            except Exception:
                pass
        m["content"] = _ASSET_RE.sub("", content).strip()
    return assets


async def _asset_done_push(kind: str, path: str) -> None:
    """后台图片/视频生成完成：把文件推给前端展示（WS 下发给主窗口/桌宠）。"""
    try:
        await ws_carrier.send({"type": "asset", "kind": kind, "path": path,
                               "ts": time.time()})
        if kind == "video":
            await ws_carrier.send({"type": "active",
                                   "text": "视频做好啦！🎬 已经放到聊天里，快看看吧～",
                                   "ts": time.time()})
        else:
            await ws_carrier.send({"type": "active",
                                   "text": "图片生成好啦～🖼️ 已经放到聊天里，快看看吧！",
                                   "ts": time.time()})
    except Exception:
        pass


agnes_gen.register_asset_done_callback(_asset_done_push)
wecom_url = os.getenv("WECOM_WEBHOOK_URL") or ""
if wecom_url:
    active_scheduler.register_carrier(active.WeComCarrier(wecom_url))
    print("[Agent Server] 企业微信推送已启用（WECOM_WEBHOOK_URL）")
wechat_push_url = os.getenv("WECHAT_PUSH_URL") or ""
if wechat_push_url:
    active_scheduler.register_carrier(
        active.WeChatCarrier(wechat_push_url, token=os.getenv("WECHAT_PUSH_TOKEN", "")))
    print("[Agent Server] 个人微信推送已启用（WECHAT_PUSH_URL）")

# ============ 思考轨迹黑匣子（运行时日志，不进记忆、不进仓库）============
LOG_DIR = PROJECT_ROOT / "logs"


UPLOAD_DIR = PROJECT_ROOT / "logs" / "uploads"


def _save_upload(data_url: str) -> str:
    """保存 base64 图片到 logs/uploads/，返回绝对路径；失败返回空字符串。
    为多模态模型（Ollama gemma3 等）预留：图片先落盘，模型接入后可直接读取。
    图片数据绝不写入记忆 / 消息历史（只注入文本路径提示）。
    """
    try:
        s = (data_url or "").strip()
        m = re.match(r"^data:(image/\w+);base64,(.*)$", s, re.DOTALL)
        if m:
            mime, b64 = m.group(1), m.group(2)
        else:
            mime, b64 = "image/png", s
        raw = base64.b64decode(b64, validate=False)
        if not raw or len(raw) > 20 * 1024 * 1024:
            return ""
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
               "image/gif": ".gif", "image/webp": ".webp"}.get(mime, ".png")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        p = UPLOAD_DIR / f"upload-{int(time.time() * 1000)}{ext}"
        p.write_bytes(raw)
        return str(p)
    except Exception:
        return ""


def _log_thinking(user_text: str, trace: list) -> None:
    """把本轮思考轨迹追加写入 logs/thinking-YYYYMMDD.jsonl。
    纯展示/排障用：不触碰 memory_data/，不改变任何记忆写入。
    """
    if not trace:
        return
    try:
        LOG_DIR.mkdir(exist_ok=True)
        day = time.strftime("%Y%m%d")
        line = {"ts": round(time.time(), 3), "user": user_text, "trace": trace}
        with open(LOG_DIR / f"thinking-{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ============ 本地模型（Ollama）按需启停 ============
def _ollama_base() -> str:
    return AGENT_LOCAL_BASE.replace("/v1", "")


def _ollama_running() -> bool:
    """Ollama 服务是否可达（127.0.0.1:11434）。"""
    try:
        with urllib.request.urlopen(_ollama_base() + "/api/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _ollama_exe() -> str:
    exe = os.getenv("OLLAMA_BIN")
    if exe and os.path.exists(exe):
        return exe
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if cand.exists():
        return str(cand)
    return "ollama"


def _ollama_model_ready(model: str) -> bool:
    """Ollama 里是否已导入该模型。"""
    try:
        with urllib.request.urlopen(_ollama_base() + "/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            names = [m.get("name", "") for m in data.get("models", [])]
        return any(n == model or n.startswith(model + ":") for n in names)
    except Exception:
        return False


def _start_ollama() -> bool:
    """按需启动 Ollama（隐藏窗口）；已在运行则直接返回。"""
    if _ollama_running():
        return True
    try:
        subprocess.Popen([_ollama_exe(), "serve"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(30):  # 最多等 15s
        if _ollama_running():
            return True
        time.sleep(0.5)
    return False


def _unload_local(model: str = None) -> bool:
    """卸载本地模型（keep_alive=0），释放显存；不杀 Ollama 进程。"""
    model = model or AGENT_LOCAL_MODEL
    try:
        req = urllib.request.Request(
            _ollama_base() + "/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _warm_local(model: str = None) -> bool:
    """预加载本地模型（发一个 1 token 的热身请求），让首条消息不用等冷启动。"""
    model = model or AGENT_LOCAL_MODEL
    try:
        req = urllib.request.Request(
            _ollama_base() + "/api/chat",
            data=json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": {"num_predict": 1},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status == 200
    except Exception:
        return False


def _deepseek_ping() -> bool:
    """轻量探测 DeepSeek API 连通性（max_tokens=1，5s 超时）。"""
    key = API_KEY or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return False
    try:
        req = urllib.request.Request(
            AGENT_LLM_BASE + "/chat/completions",
            data=json.dumps({
                "model": AGENT_LLM_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


async def local_status_handler(request):
    """GET /local/status — 本地模型状态（Ollama 是否运行 + 模型是否就绪）。"""
    ollama = _ollama_running()
    return web.json_response({
        "ollama_running": ollama,
        "model_ready": ollama and _ollama_model_ready(AGENT_LOCAL_MODEL),
        "model": AGENT_LOCAL_MODEL,
    })


async def local_start_handler(request):
    """POST /local/start — 按需启动 Ollama + 校验本地模型就绪。"""
    ok = await asyncio.to_thread(_start_ollama)
    if not ok:
        return web.json_response({"ok": False, "error": "Ollama 启动失败，请手动打开 Ollama"}, status=500)
    if not _ollama_model_ready(AGENT_LOCAL_MODEL):
        return web.json_response({"ok": False, "error": "本地模型 " + AGENT_LOCAL_MODEL + " 未安装，请先导入"}, status=500)
    # 预热：立即把模型加载进显存（约 20-40s），首条消息不再等冷启动
    await asyncio.to_thread(_warm_local)
    return web.json_response({"ok": True, "model": AGENT_LOCAL_MODEL})


async def local_stop_handler(request):
    """POST /local/stop — 卸载本地模型（释放显存），不杀 Ollama。"""
    ok = await asyncio.to_thread(_unload_local)
    return web.json_response({"ok": True, "unloaded": ok})


async def health_full_handler(request):
    """GET /health/full — 启动连通性检测：DeepSeek 可达 + Ollama + 本地模型就绪。"""
    deepseek = await asyncio.to_thread(_deepseek_ping)
    ollama = _ollama_running()
    return web.json_response({
        "status": "ok",
        "deepseek_ok": deepseek,
        "ollama_running": ollama,
        "local_model_ready": ollama and _ollama_model_ready(AGENT_LOCAL_MODEL),
        "local_model": AGENT_LOCAL_MODEL,
    })


async def init_session():
    """构建初始会话（恢复历史 + 注入档案）。"""
    global messages, system_msg
    msgs, sys_msg = core.build_initial_messages(mem)
    messages = msgs
    system_msg = sys_msg


# ===================== HTTP Handlers =====================

async def health(request):
    return web.json_response({"status": "ok", "ts": time.time()})


async def chat_handler(request):
    """POST /chat  — 发送用户消息，返回 AI 回复（非流式简化版）。
    
    Body JSON: {"message": "用户输入文本"}
    返回: {"reply": "AI完整回复", "history_len": N}
    """
    global messages
    try:
        data = await request.json()
        user_text = str(data.get("message", "")).strip()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    # 只发图不带文字也算有效消息（微信/桌面图片消息）；纯空请求才拒绝
    if not user_text and not (str(data.get("image_base64") or "").strip()):
        return web.json_response({"error": "empty message"}, status=400)

    provider = str(data.get("provider") or "")  # "deepseek" | "local" | ""

    async with chat_lock:
        # 用户刚发消息：通知主动触发调度器（空闲源据此重置计时）
        active_scheduler.on_user_activity()

        # 图片附件（A3 多模态）：保存 logs/uploads + 转 data URI 喂本地视觉模型（不进记忆/历史）
        images = None
        try:
            img_b64 = str(data.get("image_base64") or "")
            if img_b64:
                saved = _save_upload(img_b64)
                if saved:
                    images = [img_b64 if img_b64.startswith("data:") else "data:image/png;base64," + img_b64]
        except Exception:
            pass
        # 只发图没文字：补一个默认提示词，让视觉链路正常工作
        if not user_text:
            user_text = "请描述这张图片的内容"
        # 视觉 skill：DeepSeek 模式发图 → 本地模型先把图片转成文字描述，
        # 注入用户消息后交给 DeepSeek（相当于给 DeepSeek 接上"眼睛"）
        if provider == "deepseek" and images and not _intimacy_intimate() and _vision_describe is not None:
            try:
                desc = await _vision_describe(images[0], API_KEY,
                                              base_url=AGENT_LOCAL_BASE, model=AGENT_LOCAL_MODEL)
            except Exception:
                desc = ""
            if desc:
                user_text = (f"（用户发来一张图片，图片内容：{desc[:800]}）\n\n{user_text}")
                images = None   # DeepSeek 已通过描述"看到"图片，不再走本地视觉
        llm_base, llm_model, llm_key = _route_llm(bool(images), provider)
        is_local = llm_base == AGENT_LOCAL_BASE
        call_tools = _local_tools(tools) if is_local else tools
        history_budget = 3000 if is_local else 12000
        # 本地模型看图：不绑工具（纯视觉问答），避免 4B 模型在"工具+图"下乱选工具/截断
        enable_tools = not (is_local and images)   # 本地文本有工具（8B 可靠）；本地看图走视觉聚焦
        vision_focus = bool(is_local and images)
        light_context = False                   # 8B 用完整人设+记忆（不再轻量）

        # 收集流式 token 到缓冲区 + 思考轨迹
        tokens_buffer = []
        thinking_trace = []

        def on_token(chunk):
            tokens_buffer.append(chunk)

        def on_thinking(ev):
            thinking_trace.append(ev)

        pend_before = len(PENDING_PROFILE)   # 本轮新增的记忆提案（写读分离，勾选才落盘）
        try:
            messages = await core.process_turn(
                messages=messages,
                user_text=user_text,
                mem=mem,
                tools=call_tools,
                tool_map=tool_map,
                api_key=llm_key,
                on_token=on_token,
                on_thinking=on_thinking,
                task_store=task_store,
                llm_base=llm_base,
                llm_model=llm_model,
                images=images,
                history_budget=history_budget,
                enable_tools=enable_tools,
                vision_focus=vision_focus,
                light_context=light_context,
            )
            reply = "".join(tokens_buffer)
            pending_memory = PENDING_PROFILE[pend_before:]
            _log_thinking(user_text, thinking_trace)
            assets = _collect_assets(messages)
            return web.json_response({
                "reply": reply,
                "history_len": len(messages),
                "thinking": thinking_trace,
                "model": llm_model,
                "pending_memory": pending_memory,
                "assets": assets,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


async def chat_stream_handler(request):
    """POST /chat/stream  — SSE 流式聊天接口。
    
    前端用 EventSource 或 fetch+ReadableStream 接收逐字回复。
    每个 SSE event: data: {"token":"xxx"}\n\n
    结束: data: [DONE]\n\n
    """
    global messages
    try:
        data = await request.json()
        user_text = str(data.get("message", "")).strip()
    except Exception:
        return web.Response(status=400, text="invalid json")

    if not user_text:
        return web.Response(status=400, text="empty message")

    provider = str(data.get("provider") or "")  # "deepseek" | "local" | ""

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    async with chat_lock:
        try:
            async def sse_send(token):
                chunk = json.dumps({"token": token}, ensure_ascii=False)
                await response.write(f"data: {chunk}\n\n".encode("utf-8"))

            async def sse_thinking(ev):
                chunk = json.dumps({"type": "thinking", "data": ev}, ensure_ascii=False)
                await response.write(f"data: {chunk}\n\n".encode("utf-8"))

            thinking_trace = []
            def _collect(ev):
                thinking_trace.append(ev)
                asyncio.ensure_future(sse_thinking(ev))

            images = None
            try:
                img_b64 = str(data.get("image_base64") or "")
                if img_b64:
                    saved = _save_upload(img_b64)
                    if saved:
                        images = [img_b64 if img_b64.startswith("data:") else "data:image/png;base64," + img_b64]
            except Exception:
                pass
            # 视觉 skill：DeepSeek 发图 → 本地模型转文字描述，交给 DeepSeek
            if provider == "deepseek" and images and not _intimacy_intimate() and _vision_describe is not None:
                try:
                    desc = await _vision_describe(images[0], API_KEY,
                                                  base_url=AGENT_LOCAL_BASE, model=AGENT_LOCAL_MODEL)
                except Exception:
                    desc = ""
                if desc:
                    user_text = (f"（用户发来一张图片，图片内容：{desc[:800]}）\n\n{user_text}")
                    images = None
            llm_base, llm_model, llm_key = _route_llm(bool(images), provider)
            is_local = llm_base == AGENT_LOCAL_BASE
            call_tools = _local_tools(tools) if is_local else tools
            history_budget = 3000 if is_local else 12000
            enable_tools = not (is_local and images)
            vision_focus = bool(is_local and images)
            light_context = False

            messages = await core.process_turn(
                messages=messages,
                user_text=user_text,
                mem=mem,
                tools=call_tools,
                tool_map=tool_map,
                api_key=llm_key,
                on_token=sse_send,
                on_thinking=_collect,
                task_store=task_store,
                llm_base=llm_base,
                llm_model=llm_model,
                images=images,
                history_budget=history_budget,
                enable_tools=enable_tools,
                vision_focus=vision_focus,
                light_context=light_context,
            )
            _log_thinking(user_text, thinking_trace)
            for _a in _collect_assets(messages):
                await response.write(
                    ("data: " + json.dumps({"type": "asset", "kind": _a["kind"],
                                            "path": _a["path"]}, ensure_ascii=False) + "\n\n").encode("utf-8"))
            await response.write(b"data: [DONE]\n\n")
        except Exception as e:
            err = json.dumps({"error": str(e)}, ensure_ascii=False)
            await response.write(f"data: {err}\n\n".encode("utf-8"))

    await response.write_eof()
    return response


async def history_handler(request):
    """GET /history — 返回当前会话历史（不含 system prompt 的敏感细节）。"""
    global messages
    if not messages:
        return web.json_response({"messages": [], "count": 0})
    # 只返回 role + content，去掉内部字段
    clean = [{"role": m["role"], "content": m.get("content", "")}
             for m in messages if m["role"] in ("user", "assistant")]
    return web.json_response({"messages": clean, "count": len(clean)})


async def reset_handler(request):
    """POST /reset — 新建对话：把当前会话归档保留，然后开启一个空白新会话。

    不删除任何记录/档案：旧对话存为时间戳归档（含摘要锚点机制），
    档案卡 profile 原样保留；新会话只注入人格 + 档案，不带旧对话历史。
    """
    global messages, system_msg
    if messages:
        msgs_with_chat = [m for m in messages if m.get("role") in ("user", "assistant")]
        if len(msgs_with_chat) > 1:
            mem.save_session(messages)          # 归档旧会话（不删，历史全保留）
    msgs, sys_msg = core.build_initial_messages(mem, with_history=False)
    messages, system_msg = msgs, sys_msg
    return web.json_response({"status": "reset_ok", "archived": True})


async def profile_handler(request):
    """GET /profile — 返回用户档案卡摘要（只读）。"""
    text = mem.profile_context()
    facts = mem.load_profile().get("facts", {})
    return web.json_response({
        "summary": text,
        "facts_count": len(facts),
    })




# ===================== 档案卡人工管理（客户端记忆 UI） =====================

# ============ 待审批记忆写入（用户勾选才落盘）============
# write_memory / save_important 不再直接写档案卡，改为生成「提案」，
# 前端在消息下方展示（灰色小字、默认折叠、默认不勾选），勾选后才真正写入。
PENDING_PROFILE = []   # 提案列表：{id, op, key, value, type, confidence, ts}
_pending_seq = 0


async def _propose_memory(key, value, ftype, confidence, op="set"):
    """生成一条记忆写入提案（不落盘）。已存在同 key 同 value 且生效时不重复提案。"""
    global _pending_seq
    try:
        prof = mem.load_profile()
        old = (prof.get("facts", {}) or {}).get(key)
        if isinstance(old, dict) and old.get("active") is not False:
            if str(old.get("value")) == str(value):
                return f"该记忆已存在，未重复提交：{key} = {value}"
    except Exception:
        pass
    _pending_seq += 1
    PENDING_PROFILE.append({
        "id": f"p{_pending_seq}",
        "op": op,
        "key": key,
        "value": value,
        "type": ftype,
        "confidence": confidence,
        "ts": time.time(),
    })
    return f"已提交记忆写入申请：{key} = {value}（在客户端「写入的记忆」勾选后才会写入档案卡）"


async def _propose_write_memory(mem=None, key="", value="", fact_type="fact",
                                confidence=0.9, text="", content="", note="", **extra) -> str:
    if mem is None:
        return "错误：记忆系统不可用（未注入 MemoryStore）"
    if not key:
        key = "important_note"
    if not value:
        value = text or content or note
    key = (key or "").strip()
    value = str(value or "").strip()
    if not key or not value:
        return "错误：key 和 value 都不能为空"
    ftype = fact_type if fact_type in ("fact", "preference") else "fact"
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.9
    conf = max(0.0, min(1.0, conf))
    return await _propose_memory(key, value, ftype, conf, op="set")


async def _propose_save_important(mem=None, text="", content="", note="", title="", **extra) -> str:
    if mem is None:
        return "错误：记忆系统不可用（未注入 MemoryStore）"
    item = (text or content or note or title or "").strip()
    if not item:
        return "错误：内容为空"
    return await _propose_memory("important_notes", item, "fact", 0.95, op="append")


# 记忆写入工具改为「提案」模式：不再直接写档案卡，先提交待审批（勾选才落盘）
for _mt_name in ("write_memory", "save_important"):
    if _mt_name in tool_map:
        tool_map[_mt_name] = _propose_write_memory if _mt_name == "write_memory" else _propose_save_important


async def profile_pending_handler(request):
    """GET /profile/pending — 待审批记忆写入提案列表。"""
    return web.json_response({"items": PENDING_PROFILE})


async def profile_pending_apply_handler(request):
    """POST /profile/pending/apply — 勾选生效：把选中的提案写入档案卡。Body: {"ids": [...]}"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    ids = set(str(i) for i in (data.get("ids") or []))
    applied, failed = 0, 0
    keep = []
    for it in PENDING_PROFILE:
        if it["id"] in ids:
            ok = False
            try:
                if it.get("op") == "append":
                    prof = mem.load_profile()
                    old = (prof.get("facts", {}) or {}).get("important_notes", {}).get("value")
                    if isinstance(old, list):
                        notes = old + [it["value"]]
                    elif isinstance(old, str) and old.strip():
                        notes = [old, it["value"]]
                    else:
                        notes = [it["value"]]
                    extracted = [{"key": "important_notes", "value": notes,
                                  "confidence": 0.95, "type": "fact"}]
                else:
                    extracted = [{"key": it["key"], "value": it["value"],
                                  "confidence": it["confidence"], "type": it["type"]}]
                mem.update_profile(extracted)
                ok = True
            except Exception:
                ok = False
            if ok:
                applied += 1
            else:
                failed += 1
                keep.append(it)
        else:
            keep.append(it)
    PENDING_PROFILE[:] = keep
    return web.json_response({"ok": True, "applied": applied, "failed": failed})


async def profile_pending_discard_handler(request):
    """POST /profile/pending/discard — 放弃：从待审批列表移除。Body: {"ids": [...]} 或 {"all": true}"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    if data.get("all"):
        n = len(PENDING_PROFILE)
        PENDING_PROFILE.clear()
        return web.json_response({"ok": True, "discarded": n})
    ids = set(str(i) for i in (data.get("ids") or []))
    n = 0
    keep = []
    for it in PENDING_PROFILE:
        if it["id"] in ids:
            n += 1
        else:
            keep.append(it)
    PENDING_PROFILE[:] = keep
    return web.json_response({"ok": True, "discarded": n})


async def profile_items_handler(request):
    """GET /profile/items — 返回档案卡全部事实/偏好（含 active 生效开关），供管理页展示。"""
    return web.json_response(mem.list_profile_items())


async def profile_toggle_handler(request):
    """POST /profile/toggle — 生效/停用一条档案事实。Body: {"key","active"}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    key = str(data.get("key", "")).strip()
    if not key:
        return web.json_response({"error": "empty key"}, status=400)
    ok = mem.toggle_profile_item(key, bool(data.get("active", True)))
    return web.json_response({"ok": ok, "key": key})


async def profile_delete_handler(request):
    """POST /profile/delete — 删除一条档案事实（先记入 discarded 审计）。Body: {"key"}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    key = str(data.get("key", "")).strip()
    if not key:
        return web.json_response({"error": "empty key"}, status=400)
    ok = mem.delete_profile_item(key)
    return web.json_response({"ok": ok, "key": key})


async def profile_add_handler(request):
    """POST /profile/add — 新增一条自定义事实/偏好。
    Body: {"key","value","type":"fact|preference","confidence":0.9}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    ok, err = mem.add_profile_item(
        str(data.get("key", "")),
        data.get("value", ""),
        str(data.get("type", "fact")),
        data.get("confidence", 0.9),
        str(data.get("category", "")),
    )
    if not ok:
        return web.json_response({"error": err}, status=400)
    return web.json_response({"ok": True})


async def profile_update_handler(request):
    """POST /profile/update — 编辑一条档案项（改内容/板块/置信度；key 不变）。
    Body: {"key","value","category","confidence"}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    key = str(data.get("key", "")).strip()
    if not key:
        return web.json_response({"error": "empty key"}, status=400)
    ok = mem.update_profile_item(
        key,
        value=data.get("value"),
        category=str(data.get("category", "")),
        confidence=data.get("confidence"),
    )
    if not ok:
        return web.json_response({"error": "key 不存在"}, status=404)
    return web.json_response({"ok": True, "key": key})


async def wechat_carrier_handler(request):
    """POST /carriers/wechat — 运行时注册微信推送 carrier（微信桥启动时调用）。

    桥复用桌面端已运行的后端时，启动时后端环境里可能没有 WECHAT_PUSH_URL，
    通过本接口把推送端点注册进主动触发调度器，幂等（重复调用只注册一次）。
    Body: {"push_url": "http://127.0.0.1:18888/push", "token": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    push_url = str(data.get("push_url") or "").strip()
    token = str(data.get("token") or "")
    if not push_url:
        return web.json_response({"error": "push_url required"}, status=400)
    for c in active_scheduler._carriers:
        if getattr(c, "name", "") == "wechat":
            return web.json_response({"ok": True, "already": True})
    active_scheduler.register_carrier(active.WeChatCarrier(push_url, token=token))
    print(f"[Agent Server] 运行时注册微信推送 carrier: {push_url}")
    return web.json_response({"ok": True})


async def assets_proxy(request):
    """GET /assets/<path> — 代理素材文件，解决跨域和路径问题。
    
    素材目录：PROJECT_ROOT/素材/
    子目录：状态图/, 对话框/, 三视图/
    """
    rel_path = request.match_info.get("path", "")
    # 安全：防止目录穿越
    safe_path = (Path(rel_path).as_posix()).replace("..", "")
    assets_dir = PROJECT_ROOT / "素材"
    file_path = assets_dir / safe_path

    if not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="asset not found")

    # 根据扩展名设置 Content-Type
    suffix = file_path.suffix.lower()
    ct_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
    content_type = ct_map.get(suffix, "application/octet-stream")

    data = file_path.read_bytes()
    return web.Response(
        body=data,
        content_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )




async def skills_handler(request):
    """GET /skills — 返回全部已安装技能（按组），供前端「技能」按钮自选并命令 agent 执行。"""
    groups = []
    for gid, gname, gtools in SKILL_GROUPS:
        items = [{
            "name": t.get("function", {}).get("name") or t.get("name"),
            "description": ((t.get("function", {}).get("description") or t.get("description") or "")[:120]),
        } for t in gtools if t.get("function", {}).get("name") or t.get("name")]
        if items:
            groups.append({"id": gid, "name": gname, "tools": items})
    # MCP 工具并入"外部服务"组
    try:
        mcp_tools = mcp_manager.openai_tools()
        mcp_items = [{
            "name": t["function"]["name"],
            "description": (t["function"].get("description") or "")[:120],
        } for t in mcp_tools]
        if mcp_items:
            groups.append({"id": "mcp", "name": "MCP 外部服务", "tools": mcp_items})
    except Exception:
        pass
    return web.json_response({"groups": groups, "count": sum(len(g["tools"]) for g in groups)})


async def mcp_tools_handler(request):
    """GET /mcp/tools — 查看已注册的 MCP 工具（调试/管理用）。"""
    try:
        tools = mcp_manager.openai_tools()
        return web.json_response({
            "count": len(tools),
            "available": mcp_manager.available_tools(),
            "tools": [{"name": t["function"]["name"],
                       "description": (t["function"].get("description") or "")[:120]}
                      for t in tools],
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def ws_handler(request):
    """GET /ws — WebSocket：桌宠/主窗口连接后接收主动触发消息。
    只下发（服务端→前端），前端无需发送内容。
    """
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    ws_carrier.add(ws)
    try:
        async for _msg in ws:
            pass   # 前端单向接收，不处理上行
    except Exception:
        pass
    finally:
        ws_carrier.remove(ws)
    return ws


async def finalize_handler(request):
    """POST /finalize — 会话结束兜底：归档 + 离线抽取档案卡，完成后退出服务。

    桌面端退出时由 Rust 侧调用，与终端模式 AGENT.py 的 finally 行为对齐，
    保证桌面模式也会保存时间戳归档并更新 LTM 档案卡。
    返回后 1 秒内停止 aiohttp 事件循环，进程自行退出（由 Rust 等待回收）。
    """
    async with chat_lock:
        try:
            changed = await core.finalize(messages, mem, API_KEY)
            result = {"status": "finalized", "changed": changed}
        except Exception as e:
            result = {"status": "error", "error": str(e)}
    # 退出前卸载本地模型（释放显存，下次进软件按需再启动）
    try:
        await asyncio.to_thread(_unload_local)
    except Exception:
        pass
    # 停止主动触发调度器 + 关闭 MCP 连接
    try:
        await active_scheduler.stop()
    except Exception:
        pass
    try:
        await mcp_manager.stop()
    except Exception:
        pass
    loop = asyncio.get_running_loop()
    loop.call_later(1.0, loop.stop)
    return web.json_response(result)


async def cors_options(request):
    """处理 CORS preflight。"""
    return web.Response(
        status=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


# ===================== 启动 =====================

def create_app():
    # client_max_size=30MB：图片 base64 请求体可能 >1MB（aiohttp 默认 1MB 会直接拒掉）
    app = web.Application(client_max_size=30 * 1024 * 1024)
    # API 路由
    app.router.add_get("/health", health)
    app.router.add_get("/health/full", health_full_handler)
    app.router.add_get("/local/status", local_status_handler)
    app.router.add_post("/local/start", local_start_handler)
    app.router.add_post("/local/stop", local_stop_handler)
    app.router.add_post("/chat", chat_handler)
    app.router.add_post("/chat/stream", chat_stream_handler)
    app.router.add_get("/history", history_handler)
    app.router.add_post("/reset", reset_handler)
    app.router.add_post("/finalize", finalize_handler)
    app.router.add_get("/profile", profile_handler)
    app.router.add_get("/profile/items", profile_items_handler)
    app.router.add_post("/profile/toggle", profile_toggle_handler)
    app.router.add_post("/profile/delete", profile_delete_handler)
    app.router.add_post("/profile/add", profile_add_handler)
    app.router.add_post("/profile/update", profile_update_handler)
    # 主动触发 WebSocket（阶段A1）
    app.router.add_get("/ws", ws_handler)
    # MCP 工具状态（B1）
    app.router.add_get("/skills", skills_handler)
    app.router.add_get("/mcp/tools", mcp_tools_handler)

    # 素材静态代理
    app.router.add_get("/assets/{path:.*}", assets_proxy)
    app.router.add_post("/carriers/wechat", wechat_carrier_handler)
    app.router.add_get("/profile/pending", profile_pending_handler)
    app.router.add_post("/profile/pending/apply", profile_pending_apply_handler)
    app.router.add_post("/profile/pending/discard", profile_pending_discard_handler)
    # CORS
    app.router.add_options("/{path:.*}", cors_options)
    return app


def _port_in_use(port: int) -> bool:
    """探测端口是否已被其他实例占用（桌面客户端 / 微信桥）。"""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


if __name__ == "__main__":
    # 端口占用守卫：已有实例在跑（桌面客户端 / 微信桥）就直接静默退出，
    # 避免第二个实例 EADDRINUSE 崩溃刷屏。前端连的是端口，不受影响。
    if _port_in_use(PORT):
        print(f"[Agent Server] 端口 {PORT} 已被其他实例占用，本实例退出（复用现有后端）")
        sys.exit(0)

    print(f"[Agent Server] 项目根目录: {PROJECT_ROOT}")
    print(f"[Agent Server] 素材目录: {PROJECT_ROOT / '素材'}")
    print(f"[Agent Server] 监听端口: {PORT}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_session())

    msg_count = len(messages) - 1 if messages else 0
    if msg_count > 0:
        print(f"[Agent Server] 已恢复上次会话（{msg_count} 条历史消息）")
    if mem.profile_context():
        print("[Agent Server] 已载入用户档案卡（LTM）")

    app = create_app()

    # 用 AppRunner 手动托管，便于 /finalize 里 loop.stop() 优雅退出
    # （web.run_app 会把服务包进 run_until_complete(main_task)，直接 stop 会抛
    #   "Event loop stopped before Future completed"，导致退出码非 0 + 堆栈）。
    async def _start_server():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", PORT)
        await site.start()
        # 连接 MCP servers（失败单点隔离，不影响主服务）
        await _sync_mcp_tools()

        # 启动主动触发调度器（周期 tick）
        await active_scheduler.start()
        print(f"[Agent Server] 服务已启动: http://127.0.0.1:{PORT}  (主动触发: 开)")
        return runner

    runner = None
    try:
        runner = loop.run_until_complete(_start_server())
        loop.run_forever()   # 一直运行；/finalize 里 loop.stop() 结束此循环
    except KeyboardInterrupt:
        pass
    finally:
        if runner is not None:
            loop.run_until_complete(runner.cleanup())
        loop.close()

