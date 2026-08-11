# -*- coding: utf-8 -*-
"""主动触发配置：默认配置 + 可选 active/config.json 覆盖（不强制存在）。"""
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "enabled": True,              # 总开关（调试/临时关闭用）
    "tick_seconds": 20,           # 调度器 tick 周期（秒）
    "cooldown_seconds": 1800,     # 同一触发器触发后的冷却（秒），防止反复提醒
    "quiet": {
        "fullscreen": True,       # 前台有全屏程序（游戏/视频）时不打扰
    },
    "sources": {
        "clock": {"enabled": True},        # 定点提醒（作息/健身/牛奶/自定义规则）
        "idle": {                          # 空闲关心（用户长时间没说话）
            "enabled": True,
            "minutes": 20,                 # 超过 20 分钟没发消息触发
            "cooldown_minutes": 120,       # 触发后 2 小时内不再触发
        },
    },
    "rules": [
        # 额外固定规则示例（HH:MM 到点提醒；配置里不写 = 只用档案自动解析）
        # {"time": "23:30", "id": "custom_sleep", "text": "爸爸，该睡觉啦"},
    ],
}


def _deep_merge(base: dict, patch: dict) -> None:
    """递归合并用户配置到默认配置（用户覆盖默认）。"""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = Path(__file__).resolve().parent / "config.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            _deep_merge(cfg, user)
        except Exception:
            pass
    return cfg
