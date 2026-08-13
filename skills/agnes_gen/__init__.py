# -*- coding: utf-8 -*-
"""Agnes AI 生成技能：生图 + 生视频。"""
from .skill import generate_image, generate_video, register_asset_done_callback

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "用 Agnes AI 生成一张图片（免费）。用户要画图/生成图片/配图/海报/插画等时使用。"
                "prompt 请用英文描述（主体+场景+风格+光线+构图+质量）；如果用户说的是中文，先把描述翻译成英文再传入。"
                "生成完成后会保存到本地并展示在聊天里。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "英文图片描述（主体+场景+风格+光线+构图+质量）"},
                    "size": {"type": "string", "description": "图片尺寸，如 1024x768 / 768x1024 / 1024x1024，默认 1024x768"},
                },
                "required": ["prompt"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": (
                "用 Agnes AI 生成一段短视频（约 5 秒，免费）。用户要生成视频/动态画面/短片时使用。"
                "prompt 请用英文描述（主体+动作+场景+镜头运动+光线+风格）；如果用户说的是中文，先把描述翻译成英文再传入。"
                "生成需要 1~3 分钟：完成前先告诉用户正在生成，完成后视频会自动展示在聊天里。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "英文视频描述（主体+动作+场景+镜头运动+光线+风格）"},
                    "width": {"type": "integer", "description": "宽度，64 的倍数，默认 1152"},
                    "height": {"type": "integer", "description": "高度，64 的倍数，默认 768"},
                    "num_frames": {"type": "integer", "description": "总帧数（8n+1），默认 121（约5秒）"},
                    "frame_rate": {"type": "integer", "description": "帧率 1-60，默认 24"},
                },
                "required": ["prompt"],
            },
        }
    },
]

TOOL_MAP = {"generate_image": generate_image, "generate_video": generate_video}
