# -*- coding: utf-8 -*-
"""健康记录工具：手动记录睡眠/体重/步数/心率/饮水/心情 → 记忆 events。

数据写入 MemoryStore.record_event（key=health_<metric>_<date>，独立于档案卡 facts），
为后续 MCP 健康数据源（手表）打底：数据源接入后统一走同一事件模型。
"""
import re
from datetime import datetime

_METRICS = {
    "sleep_hours": "睡眠时长(小时)",
    "weight_kg": "体重(kg)",
    "steps": "步数",
    "heart_rate": "心率(bpm)",
    "blood_pressure": "血压(如 120/80)",
    "water_ml": "饮水量(ml)",
    "mood": "心情(如 很好/一般/疲惫)",
}
_VALID = set(_METRICS)


def _norm_date(s: str) -> str:
    if not s:
        return datetime.now().strftime("%Y-%m-%d")
    m = re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip())
    if m:
        return m.group(0)
    if s.strip() == "今天":
        return datetime.now().strftime("%Y-%m-%d")
    if s.strip() == "昨天":
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


async def record_health(metric: str, value, date: str = "", note: str = "", mem=None) -> str:
    """记录一条健康数据。metric ∈ sleep_hours/weight_kg/steps/heart_rate/blood_pressure/water_ml/mood。"""
    if mem is None:
        return "错误：记忆服务未初始化"
    metric = (metric or "").strip().lower()
    if metric not in _VALID:
        return f"错误：不支持的指标「{metric}」（支持：{'、'.join(sorted(_VALID))}）"
    d = _norm_date(date)
    if d is None:
        return "错误：日期格式应为 YYYY-MM-DD（或 今天/昨天）"
    key = f"health_{metric}_{d}"
    mem.record_event(key, {"value": str(value), "note": note}, confidence=0.9)
    return f"✅ 已记录健康数据：{_METRICS[metric]} = {value}（{d}）"


async def health_records(metric: str = "", days: int = 7, mem=None) -> str:
    """查询最近 N 天健康记录（不传 metric 则全部）。"""
    if mem is None:
        return "错误：记忆服务未初始化"
    events = mem.list_events() or {}
    prefix = f"health_{metric.strip().lower()}_" if metric.strip() else "health_"
    rows = []
    for k, v in events.items():
        if not k.startswith(prefix):
            continue
        date = k.rsplit("_", 1)[-1]
        raw = v.get("value") if isinstance(v, dict) else v
        if isinstance(raw, dict):
            val = raw.get("value", raw)
            note = raw.get("note") or ""
        else:
            val = raw
            note = v.get("note") if isinstance(v, dict) else ""
        rows.append((date, k, val, note))
    rows.sort(reverse=True)
    rows = rows[: max(1, min(int(days or 7), 90))]
    if not rows:
        return "暂时没有健康记录～"
    lines = [f"最近 {len(rows)} 条健康记录："]
    for date, k, val, note in rows:
        metric_cn = _METRICS.get(k.split("_")[1], k)
        lines.append(f"  {date} {metric_cn} = {val}" + (f"（{note}）" if note else ""))
    return "\n".join(lines)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_health",
            "description": (
                "记录一条健康数据（睡眠/体重/步数/心率/血压/饮水/心情）。"
                "用户说「记一下/记录/我昨晚睡了X小时/体重多少/今天走了X步」时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string",
                               "description": "指标：sleep_hours/weight_kg/steps/heart_rate/blood_pressure/water_ml/mood"},
                    "value": {"type": "string", "description": "数值或描述，如 7.5 / 120/80 / 很好"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD，默认今天"},
                    "note": {"type": "string", "description": "可选备注"},
                },
                "required": ["metric", "value"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "health_records",
            "description": "查询最近几天的健康记录（用户问「最近睡眠/体重/记录」时使用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "可选：sleep_hours/weight_kg/steps/... 不传则全部"},
                    "days": {"type": "integer", "description": "最近 N 天，默认 7"},
                },
            },
        }
    },
]

TOOL_MAP = {"record_health": record_health, "health_records": health_records}
