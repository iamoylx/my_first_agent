# -*- coding: utf-8 -*-
"""主动触发集成测试 v2：注入未来2分钟规则 → WS 等待触发（不调 chat，避免错过分钟）。"""
import asyncio, os, hashlib, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import aiohttp
from memory.store import MemoryStore

ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = os.path.join(ROOT, "tmp")
os.makedirs(TMP, exist_ok=True)
memdir = tempfile.mkdtemp(prefix="active_e2e_mem_", dir=TMP)
port = "18999"

def mem_hashes():
    h = []
    for p in sorted(pathlib.Path(os.path.join(ROOT, "memory_data")).rglob("*")):
        if p.is_file():
            h.append(hashlib.md5(p.read_bytes()).hexdigest())
    return h
real_before = mem_hashes()

# 目标时间 = now + 2 分钟（进位到分钟）
target = datetime.now() + timedelta(minutes=2)
t_hm = target.strftime("%H:%M")
hh, mm = target.hour, target.minute
period = "下午" if hh >= 12 else "上午"
h12 = (hh - 12) if hh > 12 else hh
h12 = 12 if h12 == 0 else h12
gym_text = f"{period}{h12}点{mm}去健身房"
mem = MemoryStore(base_dir=memdir, user_id="e2e")
mem.add_profile_item("gym_time", gym_text, "fact")
print(f"注入规则: {gym_text} → 期望触发 {t_hm}（now={datetime.now().strftime('%H:%M:%S')}）")

log_out = open(os.path.join(TMP, "active_e2e_stdout.txt"), "wb")
log_err = open(os.path.join(TMP, "active_e2e_stderr.txt"), "wb")
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_PORT"] = port
env["AGENT_USER_ID"] = "e2e"
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen([sys.executable, "desktop-client/agent-server.py"],
                        cwd=ROOT, env=env, stdout=log_out, stderr=log_err, creationflags=flags)
print("server pid:", proc.pid)

def http_json(method, path, body=None, timeout=10):
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
    print("server ready, connecting WS...")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            print("WS connected")
            got = None
            deadline = time.time() + 160   # 目标时间 +2 分钟 + tick 余量
            while time.time() < deadline and got is None:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        raw = msg.data if isinstance(msg.data, str) else msg.data.decode()
                        data = json.loads(raw)
                        print("WS msg:", data.get("type"), data.get("id"), (data.get("text") or "")[:30])
                        if data.get("type") == "active":
                            got = data
                except asyncio.TimeoutError:
                    continue
            print("WS 收到主动消息:", got)
            assert got is not None, "160s 内未收到主动消息"
            assert got.get("id") == "gym_remind", got
            assert "爸爸" in got.get("text", ""), got

    # active 日志落盘
    logs = list(pathlib.Path(os.path.join(ROOT, "logs")).glob("active-*.jsonl"))
    assert logs, "active log missing"
    latest = max(logs, key=lambda p: p.stat().st_mtime)
    last = json.loads(latest.read_text(encoding="utf-8").strip().splitlines()[-1])
    print("active 日志最新一条 id:", last.get("id"))
    assert last.get("id") == "gym_remind"

    # 临时记忆目录无 active 混入
    mem_files = [str(p.relative_to(memdir)) for p in pathlib.Path(memdir).rglob("*") if p.is_file()]
    assert all("active" not in f and "thinking" not in f for f in mem_files), mem_files

    # 真实记忆零污染
    assert mem_hashes() == real_before, "REAL MEMORY DATA CHANGED!"
    print("PASS: 主动触发集成测试通过（WS 推送 + 日志 + 记忆零污染）")

try:
    asyncio.run(main())
except Exception as e:
    print("FAIL:", e)
    log_err.flush(); log_err.seek(0)
    err = log_err.read().decode("utf-8", "replace")
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
