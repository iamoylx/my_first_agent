# -*- coding: utf-8 -*-
"""WeComCarrier 测试：本地 mock webhook 接收主动消息。"""
import asyncio, json, sys
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
from aiohttp import web
from active.carriers import WeComCarrier

received = {}

async def hook_handler(request):
    body = await request.json()
    received["payload"] = body
    return web.json_response({"errcode": 0})

async def main():
    app = web.Application()
    app.router.add_post("/hook", hook_handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 19002); await site.start()

    c = WeComCarrier("http://127.0.0.1:19002/hook")
    assert c.available is True
    await c.send({"kind": "reminder", "text": "⏰ 爸爸，提醒你：测试企微推送"})
    await asyncio.sleep(0.5)
    assert received.get("payload", {}).get("msgtype") == "text", received
    assert "测试企微推送" in received["payload"]["text"]["content"], received
    print("收到企微 payload:", json.dumps(received["payload"], ensure_ascii=False).encode("ascii", "replace").decode("ascii"))

    # 未配置 webhook 时静默跳过
    c2 = WeComCarrier("")
    assert c2.available is False
    await c2.send({"text": "x"})
    print("PASS: WeComCarrier（发送 + 未配置静默）")
    await runner.cleanup()

asyncio.run(main())
