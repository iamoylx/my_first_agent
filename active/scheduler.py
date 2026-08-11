# -*- coding: utf-8 -*-
"""主动触发调度器（ActiveScheduler）。

职责：周期 tick → 免打扰判定 → 各事件源 check → 冷却去重 → 广播给所有载体。

隔离原则：
  - 不关心 TriggerSource / Carrier 的具体实现（接口驱动，后续 MCP/微信即插即用）。
  - 只读记忆（load_profile），绝不写 memory_data；主动消息不进会话 messages。
"""
import asyncio
from datetime import datetime

from .carriers import LogCarrier
from .config import load_config
from .policy import DoNotDisturbPolicy
from .sources import ClockSource, IdleSource


class ActiveScheduler:
    def __init__(self, mem, config: dict = None, log_dir=None):
        self.config = config or load_config()
        self.mem = mem
        self._carriers = [LogCarrier(log_dir)]
        self.policy = DoNotDisturbPolicy(self.config.get("quiet", {}))
        self._sources = []
        sources_cfg = self.config.get("sources", {}) or {}
        if sources_cfg.get("clock", {}).get("enabled", True):
            self._sources.append(ClockSource(mem, self.config))
        if sources_cfg.get("idle", {}).get("enabled", True):
            self._sources.append(IdleSource(sources_cfg.get("idle", {}) or {}))
        self._fired = {}       # trigger.id -> 上次触发 ts
        self._task = None
        self._tick_seconds = max(1, int(self.config.get("tick_seconds", 20)))
        self._cooldown = max(1, int(self.config.get("cooldown_seconds", 1800)))

    # ---------- 对外接口 ----------
    def register_carrier(self, carrier):
        self._carriers.append(carrier)

    def on_user_activity(self):
        """用户发消息时调用：让空闲源知道"用户还在"。"""
        for s in self._sources:
            try:
                s.on_user_activity()
            except Exception:
                pass

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for c in self._carriers:
            try:
                c.close()
            except Exception:
                pass

    # ---------- 内部 ----------
    async def _run(self):
        while True:
            try:
                await self._tick()
            except Exception:
                pass
            await asyncio.sleep(self._tick_seconds)

    async def _tick(self, now: datetime = None):
        if not self.config.get("enabled", True):
            return
        now = now or datetime.now()
        if self.policy.is_quiet():
            return
        for src in self._sources:
            trig = None
            try:
                trig = src.check(now)
            except Exception:
                trig = None
            if trig is not None and self._allow(trig):
                msg = {
                    "type": "active",
                    "kind": trig.kind,
                    "id": trig.id,
                    "text": trig.text,
                    "ts": now.timestamp(),
                }
                for c in self._carriers:
                    try:
                        await c.send(msg)
                    except Exception:
                        pass

    def _allow(self, trig) -> bool:
        now_ts = datetime.now().timestamp()
        last = self._fired.get(trig.id, 0)
        if now_ts - last < self._cooldown:
            return False
        self._fired[trig.id] = now_ts
        return True
