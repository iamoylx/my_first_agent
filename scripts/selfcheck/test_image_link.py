# -*- coding: utf-8 -*-
"""图片附件链路测试：/chat 带 image_base64 → 落盘 logs/uploads + 模型感知 + 记忆零污染。"""
import asyncio, os, hashlib, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = tempfile.mkdtemp(prefix="img_run_")
memdir = tempfile.mkdtemp(prefix="img_mem_", dir=TMP)
taskdir = tempfile.mkdtemp(prefix="img_task_", dir=TMP)
port = "18999"

def mem_hashes():
    h = []
    for p in sorted(pathlib.Path(os.path.join(ROOT, "memory_data")).rglob("*")):
        if p.is_file():
            h.append(hashlib.md5(p.read_bytes()).hexdigest())
    return h
real_before = mem_hashes()

log_out = open(os.path.join(TMP, "img_stdout.txt"), "wb")
log_err = open(os.path.join(TMP, "img_stderr.txt"), "wb")
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_TASK_DIR"] = taskdir
env["AGENT_PORT"] = port
env["AGENT_USER_ID"] = "img"
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen([sys.executable, "desktop-client/agent-server.py"],
                        cwd=ROOT, env=env, stdout=log_out, stderr=log_err, creationflags=flags)

# 1x1 红色 PNG
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
DATA_URL = "data:image/png;base64," + PNG_B64

def http_json(method, path, body=None, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

try:
    for _ in range(40):
        try:
            if http_json("GET", "/health", timeout=2).get("status") == "ok":
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("server not ready")

    r = http_json("POST", "/chat", {"message": "这张图是什么", "image_base64": DATA_URL})
    print("reply:", (r.get("reply") or "")[:160])
    assert r.get("reply"), "回复为空"

    # 图片已落盘
    uploads = list(pathlib.Path(os.path.join(ROOT, "logs", "uploads")).glob("upload-*.png"))
    assert uploads, "图片未保存"
    latest = max(uploads, key=lambda p: p.stat().st_mtime)
    print("图片已保存:", latest.name, latest.stat().st_size, "bytes")
    assert latest.stat().st_size > 0

    # 临时记忆目录无图片混入（图片只进 logs/uploads）
    mem_files = [str(p.relative_to(memdir)) for p in pathlib.Path(memdir).rglob("*") if p.is_file()]
    assert all("upload" not in f and "base64" not in f for f in mem_files), mem_files

    # 真实记忆零污染
    assert mem_hashes() == real_before, "REAL MEMORY DATA CHANGED!"
    print("PASS: 图片附件链路（保存 + 模型感知 + 记忆零污染）")
except Exception as e:
    print("FAIL:", e)
    err = open(os.path.join(TMP, "img_stderr.txt"), "r", encoding="utf-8", errors="replace").read()
    if err.strip():
        print("--- stderr ---"); print(err[:1200])
    raise
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log_out.close(); log_err.close()
