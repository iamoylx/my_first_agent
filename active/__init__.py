# -*- coding: utf-8 -*-
"""主动触发模块（阶段A1）—— 模块隔离设计。

职责边界：
  - TriggerSource（事件源）：只负责"这个契机到没到"，返回 Trigger 或 None。
  - Carrier（载体）：只负责"把主动消息送出去"（桌宠气泡 / 主窗口 / 日志 / 微信）。
  - DoNotDisturbPolicy：只负责"此刻该不该打扰"（前台全屏程序免打扰）。
  - ActiveScheduler：只协调以上三者，不关心具体实现。

扩展方式（后续 MCP / 微信无需改动本模块以外的东西）：
  - 新增数据源：实现 TriggerSource.check(now)，register 到 scheduler 即可
    （例：MCP 健康数据 → HealthSource；微信新消息 → WeChatSource）。
  - 新增载体：实现 Carrier.send(msg)，register 到 scheduler 即可
    （例：微信推送 → WeChatCarrier；Windows 通知 → ToastCarrier）。

铁律：本模块只读记忆（load_profile），绝不写 memory_data；
主动消息只经 carrier 推送与日志落盘，绝不进入会话 messages。
"""
from .scheduler import ActiveScheduler
from .carriers import (
    Carrier,
    LogCarrier,
    WebSocketCarrier,
    ToastCarrier,
    WeComCarrier,
    WeChatCarrier,
)

__all__ = [
    "Carrier",
    "ActiveScheduler",
    "LogCarrier",
    "WebSocketCarrier",
    "ToastCarrier",
    "WeComCarrier",
    "WeChatCarrier",
]
