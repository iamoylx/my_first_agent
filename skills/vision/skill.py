# skills/vision/skill.py
# -*- coding: utf-8 -*-
"""视觉 skill：把图片转成文字描述（物体/场景/颜色/OCR），供纯文本模型（DeepSeek）理解。

DeepSeek 无视觉能力；发图时服务端先调用本地视觉模型（qwen3-vl）生成详细描述，
再把描述注入 DeepSeek 上下文 —— 相当于给 DeepSeek 接上了"眼睛"。
"""
import asyncio
from core import agent_core as core

_DESCRIBE_SYSTEM = (
    "你是图片分析助手。请用中文详细描述这张图片："
    "主要物体、场景、颜色、布局、人物动作表情，以及图中所有可见文字（如有，逐字列出，包括屏幕/招牌/字幕）。"
    "不要编造图片里不存在的内容。"
)


async def describe_image(data_url: str, api_key: str,
                         base_url: str = None, model: str = None,
                         timeout: float = 120.0) -> str:
    """用本地视觉模型把图片转成文字描述。失败返回空字符串（调用方降级）。"""
    msgs = [
        {"role": "system", "content": _DESCRIBE_SYSTEM},
        {"role": "user", "content": "描述这张图片"},
    ]
    tokens = []
    try:
        await asyncio.wait_for(
            core.stream_final(msgs, api_key, on_token=tokens.append,
                              base_url=base_url, model=model, images=[data_url]),
            timeout=timeout,
        )
    except Exception:
        return ""
    return "".join(tokens).strip()


# 供外部 import 的统一入口
__all__ = ["describe_image"]
