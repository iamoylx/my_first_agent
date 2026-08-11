import os
import json, os, subprocess, sys, tempfile, time, urllib.request, pathlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = os.path.join(ROOT, "tmp")
os.makedirs(TMP, exist_ok=True)
memdir = tempfile.mkdtemp(prefix="agent_e2e_mem_", dir=TMP)
port = "18999"
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_PORT"] = port
env["AGENT_USER_ID"] = "e2e_test"

log_out = open(os.path.join(TMP, "e2e_stdout.txt"), "wb")
log_err = open(os.path.join(TMP, "e2e_stderr.txt"), "wb")
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen(
    [sys.executable, "desktop-client/agent-server.py"],
    cwd=ROOT, env=env, stdout=log_out, stderr=log_err, creationflags=flags,
)
print("server pid:", proc.pid)

def http_json(method, path, body=None, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

try:
    ok = False
    for _ in range(40):
        try:
            if http_json("GET", "/health", timeout=2).get("status") == "ok":
                ok = True; break
        except Exception:
            time.sleep(0.5)
    assert ok, "server not ready"
    print("server ready")

    r1 = http_json("POST", "/chat", {"message": "现在几点"})
    print("== reply1:", (r1.get("reply") or "")[:80])
    t1 = r1.get("thinking") or []
    print("== thinking1 kinds:", [e.get("kind") for e in t1])
    for e in t1:
        print("   -", e.get("kind"), "|", (e.get("text") or "")[:100])
    assert t1, "thinking missing in /chat response"
    assert any(e.get("kind") == "tool" for e in t1), "tool thinking missing"

    r2 = http_json("POST", "/chat", {"message": "你好，简单介绍一下你自己"})
    print("== reply2:", (r2.get("reply") or "")[:80])
    t2 = r2.get("thinking") or []
    print("== thinking2 kinds:", [e.get("kind") for e in t2])
    assert t2, "thinking missing in /chat response #2"

    logs = list(pathlib.Path(os.path.join(ROOT, "logs")).glob("thinking-*.jsonl"))
    assert logs, "thinking log not written"
    latest = max(logs, key=lambda p: p.stat().st_mtime)
    lines = latest.read_text(encoding="utf-8").strip().splitlines()
    print("== log:", latest.name, "lines:", len(lines))
    last = json.loads(lines[-1])
    assert last.get("trace"), "log trace empty"

    mem_files = [str(p.relative_to(memdir)) for p in pathlib.Path(memdir).rglob("*") if p.is_file()]
    print("== temp mem files:", mem_files)
    assert all("thinking" not in f for f in mem_files), "thinking polluted temp memory!"

    print("PASS: 阶段0 端到端测试通过")
except Exception as e:
    print("FAIL:", e)
    log_err.flush()
    log_err.seek(0)
    print("--- stderr ---")
    print(log_err.read().decode("utf-8", "replace"))
    raise
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log_out.close(); log_err.close()
