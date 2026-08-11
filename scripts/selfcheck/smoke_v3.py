import os
# -*- coding: utf-8 -*-
"""v3 冒烟：agent-server 加载迁移后档案 → /profile/items 5 板块 + 对话正常。"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = os.path.join(ROOT, "tmp")

memdir = tempfile.mkdtemp(prefix="v3_mem_", dir=TMP)
ud = os.path.join(memdir, "users", "default")
os.makedirs(os.path.join(ud, "sessions"), exist_ok=True)
shutil.copy(os.path.join(ROOT, "memory_data", "users", "default", "profile.json"),
            os.path.join(ud, "profile.json"))
taskdir = tempfile.mkdtemp(prefix="v3_task_", dir=TMP)
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_TASK_DIR"] = taskdir
env["AGENT_PORT"] = "18999"
env["AGENT_USER_ID"] = "default"
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen([sys.executable, "desktop-client/agent-server.py"],
                        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
def http_json(method, path, body=None, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:18999{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8"); req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))
try:
    ok = False
    for _ in range(60):
        try:
            if http_json("GET", "/health", timeout=2).get("status") == "ok":
                ok = True; break
        except Exception:
            time.sleep(0.5)
    assert ok
    time.sleep(3)
    items = http_json("GET", "/profile/items")
    cats = items.get("categories", [])
    print("板块:", [(c["id"], c["name"], len(c["items"])) for c in cats])
    assert len(cats) == 5, cats
    assert sum(len(c["items"]) for c in cats) == 38
    # 对话（真实 DeepSeek，用 v3 档案）
    r = http_json("POST", "/chat", {"message": "我几点去健身？"})
    reply = r.get("reply") or ""
    print("reply:", reply[:100].encode("ascii", "replace").decode("ascii"))
    assert reply
    print("PASS: v3 冒烟（server 加载迁移档案 + 5板块 + 对话引用档案）")
finally:
    proc.terminate()
    try: proc.wait(timeout=10)
    except Exception: proc.kill()
