# -*- coding: utf-8 -*-
"""A2 端到端：真实 DeepSeek 创建提醒 → 任务落盘 task_data → 到点经 WS 推送。"""
import asyncio, os, hashlib, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import aiohttp
from skills.reminder_tools.store import TaskStore

ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = os.path.join(ROOT, "tmp")
os.makedirs(TMP, exist_ok=True)
memdir = tempfile.mkdtemp(prefix="a2_mem_", dir=TMP)
taskdir = tempfile.mkdtemp(prefix="a2_task_", dir=TMP)
port = "18999"

def mem_hashes():
    h = []
    for p in sorted(pathlib.Path(os.path.join(ROOT, "memory_data")).rglob("*")):
        if p.is_file():
            h.append(hashlib.md5(p.read_bytes()).hexdigest())
    return h
real_before = mem_hashes()

log_out = open(os.path.join(TMP, "a2_stdout.txt"), "wb")
log_err = open(os.path.join(TMP, "a2_stderr.txt"), "wb")
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_TASK_DIR"] = taskdir
env["AGENT_PORT"] = port
env["AGENT_USER_ID"] = "a2"
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen([sys.executable, "desktop-client/agent-server.py"],
                        cwd=ROOT, env=env, stdout=log_out, stderr=log_err, creationflags=flags)
print("server pid:", proc.pid)

def http_json(method, path, body=None, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

async def main():
    for _ in range(40):
        try:
            if http_json("GET", "/health", timeout=2).get("status") == "ok":
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("server not ready")
    print("server ready")

    # 1) 真实对话：让模型创建提醒
    r = http_json("POST", "/chat", {"message": "提醒我明天下午3点开会"})
    print("reply:", (r.get("reply") or "")[:100])
    kinds = [e.get("kind") for e in (r.get("thinking") or [])]
    print("thinking kinds:", kinds)
    assert any(k == "tool" for k in kinds), "模型未调用工具"
    # 任务已写入临时 task_data
    tasks = TaskStore(base_dir=taskdir, user_id="a2").list()
    print("tasks:", tasks)
    assert tasks, "任务未落盘"
    assert any("开会" in t.get("reminder", "") for t in tasks), tasks

    # 2) 注入一条 1 分钟内到点的任务，WS 等待主动推送
    ts = TaskStore(base_dir=taskdir, user_id="a2")
    soon = datetime.now() + timedelta(minutes=1)
    ts.add("集成测试提醒到点", soon.strftime("%Y-%m-%d %H:%M"), "none")
    print("注入任务，期望触发:", soon.strftime("%H:%M"))

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            got = None
            deadline = time.time() + 150
            while time.time() < deadline and got is None:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        raw = msg.data if isinstance(msg.data, str) else msg.data.decode()
                        data = json.loads(raw)
                        if data.get("type") == "active" and data.get("kind") == "reminder":
                            got = data
                except asyncio.TimeoutError:
                    continue
            print("WS 收到提醒:", got)
            assert got is not None, "未收到提醒推送"
            assert "集成测试提醒到点" in got.get("text", ""), got

    # 3) 到点的注入任务已标记完成（明天的开会任务保留）
    tasks_after = TaskStore(base_dir=taskdir, user_id="a2").list()
    fired_ids = {t["id"] for t in tasks_after if t.get("done")}
    assert not any("集成测试提醒到点" in t.get("reminder", "") for t in tasks_after), "到点任务应已处理"
    assert any("开会" in t.get("reminder", "") for t in tasks_after), "未到点任务应保留"

    # 4) 记忆零污染
    assert mem_hashes() == real_before, "REAL MEMORY DATA CHANGED!"
    print("PASS: A2 端到端（真实创建任务 + 到点WS推送 + 记忆零污染）")

try:
    asyncio.run(main())
except Exception as e:
    print("FAIL:", e)
    err = open(os.path.join(TMP, "a2_stderr.txt"), "r", encoding="utf-8", errors="replace").read()
    if err.strip():
        print("--- stderr ---")
        print(err)
    raise
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log_out.close(); log_err.close()
