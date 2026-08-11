# -*- coding: utf-8 -*-
"""主动消息载体（Carrier）。

稳定接口：async def send(msg) -> None；msg 为 dict（含 text/kind/id/ts）。
  - LogCarrier       ：主动触发黑匣子，落盘 logs/active-YYYYMMDD.jsonl（不进记忆、不进仓库）。
  - WebSocketCarrier ：推送给所有连接的前端（桌宠气泡 + 主窗口）。
  - ToastCarrier     ：Windows 通知（可选；装了 winotify 才启用，缺失时静默跳过）。

扩展：新增微信推送只需实现 Carrier.send 并 register 到 scheduler。
"""
import asyncio
import json
import time


class Carrier:
    name = "base"

    async def send(self, msg: dict):
        raise NotImplementedError

    def close(self):
        pass


class LogCarrier(Carrier):
    """主动触发黑匣子日志。"""
    name = "log"

    def __init__(self, log_dir=None):
        self.log_dir = log_dir

    async def send(self, msg: dict):
        try:
            if not self.log_dir:
                return
            self.log_dir.mkdir(exist_ok=True)
            day = time.strftime("%Y%m%d")
            line = {"ts": msg.get("ts") or time.time(),
                    "kind": msg.get("kind"), "id": msg.get("id"),
                    "text": msg.get("text")}
            with open(self.log_dir / f"active-{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception:
            pass


class WebSocketCarrier(Carrier):
    """把主动消息广播给所有已连接的前端（桌宠 / 主窗口）。"""
    name = "ws"

    def __init__(self):
        self._connections = set()

    def add(self, ws):
        self._connections.add(ws)

    def remove(self, ws):
        self._connections.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def send(self, msg: dict):
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_bytes(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    def close(self):
        for ws in list(self._connections):
            self._connections.discard(ws)


class ToastCarrier(Carrier):
    """Windows 系统通知（可选增强：pip install winotify 后启用）。"""
    name = "toast"

    def __init__(self):
        self.available = False
        try:
            import winotify  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False

    async def send(self, msg: dict):
        if not self.available:
            return
        try:
            from winotify import Notification
            n = Notification(app_id="小满", title="小满",
                             msg=str(msg.get("text", "")), duration="short")
            n.show()
        except Exception:
            pass
