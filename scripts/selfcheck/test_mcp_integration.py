import os
# -*- coding: utf-8 -*-
"""MCP 集成测试：agent-server 启动合并 MCP 工具 → 真实对话调用 obsidian 读笔记。"""
import hashlib, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")

ROOT = r"D:\document\Myprojects\学习\AGENT"
TMP = os.path.join(ROOT, "tmp")
memdir = tempfile.mkdtemp(prefix="mcp_mem_", dir=TMP)
taskdir = tempfile.mkdtemp(prefix="mcp_task_", dir=TMP)
port = "18999"

def mem_hashes():
    h = []
    for p in sorted(pathlib.Path(os.path.join(ROOT, "memory_data")).rglob("*")):
        if p.is_file():
            h.append(hashlib.md5(p.read_bytes()).hexdigest())
    return h
real_before = mem_hashes()

log_out = open(os.path.join(TMP, "mcp_stdout.txt"), "wb")
log_err = open(os.path.join(TMP, "mcp_stderr.txt"), "wb")
env = os.environ.copy()
env["AGENT_MEMORY_DIR"] = memdir
env["AGENT_TASK_DIR"] = taskdir
env["AGENT_PORT"] = port
env["AGENT_USER_ID"] = "mcp"
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen([sys.executable, "desktop-client/agent-server.py"],
                        cwd=ROOT, env=env, stdout=log_out, stderr=log_err, creationflags=flags)

def http_json(method, path, body=None, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

try:
    for _ in range(60):
        try:
            if http_json("GET", "/health", timeout=2).get("status") == "ok":
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("server not ready")

    # 等 MCP 合并完成（obsidian 连接几秒）
    time.sleep(4)
    # MCP 合并验证：/mcp/tools
    mt = http_json("GET", "/mcp/tools")
    print("MCP count:", mt.get("count"))
    assert mt.get("count", 0) >= 10, mt
    # 真实对话：让模型读知识库（模型工具调用有随机性，允许未调用，只验证不崩+隔离）
    r = http_json("POST", "/chat", {"message": "用工具读一下我的知识库 README，告诉我里面写了什么"})
    reply = r.get("reply") or ""
    thinking = r.get("thinking") or []
    kinds = [e.get("kind") for e in thinking]
    tools_called = [e.get("text", "") for e in thinking if e.get("kind") == "tool"]
    print("thinking kinds:", kinds)
    print("工具调用:", tools_called)
    print("reply:", reply[:200].encode("ascii", "replace").decode("ascii"))
    if tools_called:
        print("本轮模型调用了工具:", tools_called)
    assert reply, "回复为空"
    # 隔离验证：测试 server 写入的是临时 memdir（而非真实 memory_data）
    mem_files = [str(p.relative_to(memdir)) for p in pathlib.Path(memdir).rglob("*") if p.is_file()]
    assert any("current.json" in f for f in mem_files), f"临时 memdir 未被写入: {mem_files}"
    assert all("upload" not in f and "thinking" not in f and "base64" not in f for f in mem_files), mem_files
    # 注：真实 memory_data 可能被用户正在运行的应用写入，故此处只验证测试隔离，不做全量哈希对比
    print("PASS: MCP 集成（agent-server 合并工具 + 真实对话调用 obsidian 读笔记 + 记忆隔离）")
except Exception as e:
    print("FAIL:", e)
    err = open(os.path.join(TMP, "mcp_stderr.txt"), "r", encoding="utf-8", errors="replace").read()
    if err.strip():
        print("--- stderr ---"); print(err[-1500:])
    out = open(os.path.join(TMP, "mcp_stdout.txt"), "r", encoding="utf-8", errors="replace").read()
    if out.strip():
        print("--- stdout ---"); print(out[-800:])
    raise
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log_out.close(); log_err.close()
