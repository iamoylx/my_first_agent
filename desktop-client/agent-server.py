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
import json
import os
import sys
import time
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
import core.agent_core as core
import active

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

PORT = int(os.getenv("AGENT_PORT", "18789"))
mem = MemoryStore(base_dir=os.getenv("AGENT_MEMORY_DIR") or None,
                  user_id=os.getenv("AGENT_USER_ID", "default"))

tools, tool_map = collect_tools((ws_tools, ws_map), (bt_tools, bt_map),
                                (ct_tools, ct_map), (mt_tools, mt_map),
                                (rt_tools, rt_map))

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

# ============ 思考轨迹黑匣子（运行时日志，不进记忆、不进仓库）============
LOG_DIR = PROJECT_ROOT / "logs"


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

    if not user_text:
        return web.json_response({"error": "empty message"}, status=400)

    async with chat_lock:
        # 用户刚发消息：通知主动触发调度器（空闲源据此重置计时）
        active_scheduler.on_user_activity()

        # 收集流式 token 到缓冲区 + 思考轨迹
        tokens_buffer = []
        thinking_trace = []

        def on_token(chunk):
            tokens_buffer.append(chunk)

        def on_thinking(ev):
            thinking_trace.append(ev)

        try:
            messages = await core.process_turn(
                messages=messages,
                user_text=user_text,
                mem=mem,
                tools=tools,
                tool_map=tool_map,
                api_key=API_KEY,
                on_token=on_token,
                on_thinking=on_thinking,
                task_store=task_store,
            )
            reply = "".join(tokens_buffer)
            _log_thinking(user_text, thinking_trace)
            return web.json_response({
                "reply": reply,
                "history_len": len(messages),
                "thinking": thinking_trace,
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

            messages = await core.process_turn(
                messages=messages,
                user_text=user_text,
                mem=mem,
                tools=tools,
                tool_map=tool_map,
                api_key=API_KEY,
                on_token=sse_send,
                on_thinking=_collect,
                task_store=task_store,
            )
            _log_thinking(user_text, thinking_trace)
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
    )
    if not ok:
        return web.json_response({"error": err}, status=400)
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
    # 停止主动触发调度器
    try:
        await active_scheduler.stop()
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
    app = web.Application()
    # API 路由
    app.router.add_get("/health", health)
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
    # 主动触发 WebSocket（阶段A1）
    app.router.add_get("/ws", ws_handler)

    # 素材静态代理
    app.router.add_get("/assets/{path:.*}", assets_proxy)
    # CORS
    app.router.add_options("/{path:.*}", cors_options)
    return app


if __name__ == "__main__":
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
