import asyncio, sys
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from aiohttp import web, WSMsgType
import aiohttp
from active.carriers import WebSocketCarrier

async def main():
    carrier = WebSocketCarrier()
    app = web.Application()
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        carrier.add(ws)
        async for _ in ws:
            pass
        return ws
    app.router.add_get("/ws", ws_handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 19001); await site.start()
    print("client_count after start:", carrier.client_count)
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:19001/ws") as ws:
            await asyncio.sleep(0.5)
            print("client_count after connect:", carrier.client_count)
            await carrier.send({"type": "active", "id": "t", "text": "hello 小满", "ts": 1})
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            print("received type:", msg.type, "| data:", msg.data)
            assert "hello" in str(msg.data)
    await runner.cleanup()
    print("PASS: WS broadcast 链路正常")

asyncio.run(main())
