# -*- coding: utf-8 -*-
import asyncio, os, sys
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
PROG = os.path.join(os.environ.get("TEMP", "."), "mcp_prog.txt")

def log(msg):
    with open(PROG, "a", encoding="utf-8") as f:
        f.write(f"{msg}\n")

async def main():
    log("1: main enter")
    from mcp_bridge import MCPManager
    m = MCPManager(config_dir=r"D:\document\Myprojects\学习\AGENT\mcp")
    log("2: construct ok")
    await m.start()
    log("3: start ok")
    names = [t["function"]["name"] for t in m.openai_tools()]
    log(f"4: tools={len(names)}")
    target = "mcp_obsidian_read_text_file"
    log(f"5: calling {target}")
    r = await m.call(target, {"path": r"D:\document\vault\README.md"})
    log(f"6: result len={len(r)} first={r[:50]!r}")
    await m.stop()
    log("7: done")

try:
    asyncio.run(main())
except Exception as e:
    log(f"ERR: {type(e).__name__}: {e}")
