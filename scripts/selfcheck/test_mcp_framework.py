# -*- coding: utf-8 -*-
"""MCP 框架单元测试：假 server 连接/注册/调用。"""
import asyncio, os, json, os, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mcp_bridge import MCPManager

async def main():
    with tempfile.TemporaryDirectory() as td:
        cfg = {"servers": {"fake": {
            "enabled": True,
            "command": sys.executable,
            "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_mcp_server.py")],
        }}}
        with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        m = MCPManager(config_dir=td)
        await m.start()
        names = [t["function"]["name"] for t in m.openai_tools()]
        print("注册工具:", names)
        assert "mcp_fake_echo" in names and "mcp_fake_add" in names, names
        r1 = await m.call("mcp_fake_echo", {"text": "你好小满"})
        print("echo 调用:", r1)
        assert "你好小满" in r1
        r2 = await m.call("mcp_fake_add", {"a": 3, "b": 4})
        print("add 调用:", r2)
        assert "7" in r2
        # 未知工具
        r3 = await m.call("mcp_fake_nope", {})
        assert "未知" in r3 or "错误" in r3
        await m.stop()
        print("PASS: MCP 框架（连接/注册/调用/容错）")

asyncio.run(main())
