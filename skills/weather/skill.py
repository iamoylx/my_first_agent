# -*- coding: utf-8 -*-
"""天气工具：Open-Meteo（免费、无需 API Key）。

定位走 geocoding API（支持中文城市名），天气走 forecast API。
网络失败时返回友好错误，不抛异常。
"""
import asyncio
import json
import urllib.parse
import urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 12

# WMO 天气代码 → 中文
_WMO = {
    0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "xiao-man-agent/1.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


async def get_weather(city: str = "", lat: float = None, lon: float = None) -> str:
    """查询城市当前天气 + 未来 3 天预报。city 支持中文城市名（如 重庆/江西/南昌）。"""
    try:
        if lat is not None and lon is not None:
            la, lo = float(lat), float(lon)
            name = (city or "").strip() or f"{la:.2f},{lo:.2f}"
        else:
            city = (city or "").strip()
            if not city:
                return "错误：请提供城市名（如 重庆 / 南昌 / 江西）"
            geo = await asyncio.to_thread(
                _http_json,
                f"{GEO_URL}?name={urllib.parse.quote(city)}&count=1&language=zh&format=json",
            )
            results = geo.get("results") or []
            if not results:
                return f"未找到城市「{city}」，试试更具体的城市名"
            la, lo = results[0]["latitude"], results[0]["longitude"]
            name = results[0].get("name", city)

        fc = await asyncio.to_thread(
            _http_json,
            f"{FORECAST_URL}?latitude={la}&longitude={lo}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&forecast_days=3&timezone=auto",
        )
        cur = fc.get("current", {}) or {}
        code = _WMO.get(cur.get("weather_code"), f"代码{cur.get('weather_code')}")
        lines = [
            f"📍 {name} 当前天气：{code}，{cur.get('temperature_2m')}°C，"
            f"湿度 {cur.get('relative_humidity_2m')}%，风速 {cur.get('wind_speed_10m')} km/h"
        ]
        daily = fc.get("daily", {}) or {}
        times = daily.get("time", []) or []
        for i in range(min(3, len(times))):
            lines.append(
                f"  {times[i]}：{daily['temperature_2m_min'][i]}~{daily['temperature_2m_max'][i]}°C，"
                f"降水概率 {daily['precipitation_probability_max'][i]}%"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"错误：天气查询失败 - {e}"
