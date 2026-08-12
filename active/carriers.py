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


class WeComCarrier(Carrier):
    """企业微信推送（群机器人 webhook）。

    配置：环境变量 WECOM_WEBHOOK_URL（企微群里添加机器人得到的 webhook 地址）。
    合规、免费、无需企业认证；主动触发消息会同时推送到企微群，手机也能收到。
    """
    name = "wecom"

    def __init__(self, webhook_url=None):
        self.webhook_url = (webhook_url or "").strip()
        self.available = bool(self.webhook_url)

    async def send(self, msg: dict):
        if not self.available:
            return
        try:
            import aiohttp
            payload = {"msgtype": "text", "text": {"content": str(msg.get("text", ""))}}
            async with aiohttp.ClientSession() as s:
                async with s.post(self.webhook_url, json=payload, timeout=8):
                    pass
        except Exception:
            pass

class WeChatCarrier(Carrier):
    """个人微信推送（官方 ClawBot / iLink 桥接）。

    桥接进程跑在 wechat_bridge/（weixin-agent-sdk），监听本地 HTTP 端点
    WECHAT_PUSH_URL（默认 http://127.0.0.1:18888/push），收到主动触发消息后
    通过 bot.sendMessage() 推送到用户个人微信。

    配置：
      WECHAT_PUSH_URL   桥接推送端点（设置后才启用）
      WECHAT_PUSH_TOKEN 可选鉴权 token（与桥接端保持一致）
    """
    name = "wechat"

    def __init__(self, push_url=None, token=""):
        self.push_url = (push_url or "").strip()
        self.token = (token or "").strip()
        self.available = bool(self.push_url)

    async def send(self, msg: dict):
        if not self.available:
            return
        try:
            import aiohttp
            headers = {}
            if self.token:
                headers["X-Push-Token"] = self.token
            payload = {
                "text": str(msg.get("text", "")),
                "kind": str(msg.get("kind", "")),
                "id": str(msg.get("id", "")),
                "ts": msg.get("ts"),
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(self.push_url, json=payload, headers=headers, timeout=5):
                    pass
        except Exception:
            pass
