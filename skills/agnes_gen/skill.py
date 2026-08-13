# -*- coding: utf-8 -*-
"""Agnes AI 生图 / 生视频工具。

通过 Agnes 免费 API（apihub.agnes-ai.com，OpenAI 兼容）生成图片/视频：
  - 生图  ：POST /v1/images/generations  模型 agnes-image-2.1-flash（同步）
  - 生视频：POST /v1/videos              模型 agnes-video-v2.0（异步：建任务→轮询→下载）

生成文件统一保存到项目根目录 generated/（可用环境变量 AGENT_GEN_DIR 覆盖，测试隔离用），
工具返回值带 @@ASSET@@ 机器可读标记（后端据此把文件推给前端展示），
并附一段人类可读的说明文字给模型，保证模型能自然地向用户交代结果。

API Key：AGNES_API_KEY（用户级环境变量）。未配置时工具返回友好错误，不抛异常。
"""
import asyncio
import base64
import json
import os
import re
import time
import urllib.request
from pathlib import Path

API_ROOT = os.getenv("AGNES_API_BASE", "https://apihub.agnes-ai.com").rstrip("/")
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"
_ASSET_TAG = "@@ASSET@@"

# 后台生成完成回调（由 agent-server 注入：async (kind, path) -> None）。
# 图片/视频未在工具内等完时，由后台任务完成后调用，把文件推给前端展示。
_on_asset_done = None


def register_asset_done_callback(fn):
    """agent-server 启动时注入：async fn(kind: str, path: str)。kind ∈ image/video。"""
    global _on_asset_done
    _on_asset_done = fn


def _project_root() -> Path:
    # skills/agnes_gen/skill.py -> 项目根
    return Path(__file__).resolve().parents[2]


def _gen_dir() -> Path:
    d = Path(os.getenv("AGENT_GEN_DIR") or (_project_root() / "generated"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key() -> str:
    """优先读 desktop-client/.agnes_key（本地配置，覆盖环境变量，防过期会话变量），
    再退回 AGNES_API_KEY 环境变量。"""
    try:
        f = _project_root() / "desktop-client" / ".agnes_key"
        if f.exists():
            k = f.read_text(encoding="utf-8").strip()
            if k:
                return k
    except Exception:
        pass
    return (os.getenv("AGNES_API_KEY") or "").strip()


def _asset_mark(kind: str, path) -> str:
    return f"{_ASSET_TAG}{json.dumps({'kind': kind, 'path': str(path)}, ensure_ascii=False)}{_ASSET_TAG}"


def _http_json(method: str, url: str, payload: dict = None, timeout: int = 90) -> dict:
    key = _key()
    if not key:
        raise RuntimeError("未配置 AGNES_API_KEY（用户级环境变量）。请在 Agnes 平台创建并激活 API Key")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _download(url: str, dest_dir: Path, prefix: str) -> Path:
    """下载文件到 dest_dir，按 Content-Type 推断扩展名；返回最终路径。"""
    req = urllib.request.Request(url, headers={"User-Agent": "xiao-man-agent/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        ctype = (r.headers.get("Content-Type") or "").lower()
    ext = ".png"
    if "jpeg" in ctype or "jpg" in ctype:
        ext = ".jpg"
    elif "webp" in ctype:
        ext = ".webp"
    elif "gif" in ctype:
        ext = ".gif"
    elif "mp4" in ctype or "video" in ctype:
        ext = ".mp4"
    path = dest_dir / f"{prefix}-{int(time.time() * 1000)}{ext}"
    path.write_bytes(raw)
    return path


def _pick_image_ext(b64_or_url: str) -> str:
    m = re.search(r"data:image/(\w+);base64", b64_or_url or "")
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    return ".png"


# ===================== 生图 =====================
async def _image_job(prompt: str, size: str) -> Path:
    """实际调用 Agnes 生图并下载到本地，返回本地路径。可能耗时较长（几十秒~几分钟）。"""
    # 注意：Agnes 生图接口不接收 response_format（会 400），
    # 默认同时返回 url + b64_json，优先用 url 下载。
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": size}
    data = await asyncio.to_thread(_http_json, "POST",
                                   f"{API_ROOT}/v1/images/generations", payload, 300)
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"Agnes 生图无返回数据：{str(data)[:200]}")
    first = items[0]
    url = str(first.get("url") or "").strip()
    b64 = str(first.get("b64_json") or "").strip()
    if b64:
        raw = base64.b64decode(b64)
        ext = _pick_image_ext(b64)
        path = _gen_dir() / f"img-{int(time.time() * 1000)}{ext}"
        path.write_bytes(raw)
        return path
    if url:
        return await asyncio.to_thread(_download, url, _gen_dir(), "img")
    raise RuntimeError(f"Agnes 生图响应没有 url/b64_json：{str(first)[:200]}")


async def generate_image(prompt: str = "", size: str = "1024x768") -> str:
    """生成一张图片。prompt 建议用英文描述（主体+场景+风格+光线+构图+质量），
    如果用户说的是中文，先把描述翻译成英文再传入；size 如 1024x768 / 768x1024。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return "错误：缺少图片描述 prompt"
    if not re.fullmatch(r"\d{3,4}x\d{3,4}", size or ""):
        size = "1024x768"
    try:
        task = asyncio.create_task(_image_job(prompt, size))
        try:
            # 先同步等最多 75s：多数时候能直接随本轮回复给出图片
            path = await asyncio.wait_for(asyncio.shield(task), timeout=75)
            return (f"图片已生成，已保存到本地：{path}\n"
                    f"{_asset_mark('image', path)}")
        except asyncio.TimeoutError:
            # 没等完：转后台，完成后再推给前端
            async def _bg_image():
                try:
                    p2 = await task
                except Exception:
                    return
                if _on_asset_done:
                    try:
                        await _on_asset_done("image", str(p2))
                    except Exception:
                        pass
            asyncio.create_task(_bg_image())
            return ("图片正在后台生成中，通常需要 10~60 秒，"
                    "完成后我会直接把图片发给你～")
    except Exception as e:
        return f"错误：Agnes 生图失败 - {e}"


# ===================== 生视频 =====================
async def _poll_video(video_id: str, max_wait: float = 45.0) -> dict:
    """轮询视频任务直到完成/失败，最多等 max_wait 秒。返回最终状态 dict。"""
    deadline = time.time() + max_wait
    last = {}
    while time.time() < deadline:
        data = await asyncio.to_thread(_http_json, "GET",
                                       f"{API_ROOT}/agnesapi?video_id={video_id}",
                                       None, 60)
        last = data
        status = str(data.get("status") or "").lower()
        if status in ("completed", "succeeded", "success", "done"):
            return data
        if status in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(f"视频生成失败：{str(data)[:200]}")
        await asyncio.sleep(5)
    return {"_timeout": True, **last}


async def _finish_video(video_id: str) -> Path:
    """视频完成后下载 mp4 到 generated/，返回本地路径。"""
    data = await _poll_video(video_id, max_wait=3.0)  # 已完成的快速再查一次
    url = str(data.get("video_url") or data.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"视频结果里没有下载地址：{str(data)[:200]}")
    path = await asyncio.to_thread(_download, url, _gen_dir(), "video")
    return path


async def _background_watch(video_id: str, prompt: str) -> None:
    """后台轮询视频任务，完成后下载并回调 server 推送前端。"""
    try:
        data = await _poll_video(video_id, max_wait=1800.0)  # 最多等 30 分钟
        if data.get("_timeout"):
            return
        path = await _finish_video(video_id)
        if _on_asset_done:
            try:
                await _on_asset_done("video", str(path))
            except Exception:
                pass
    except Exception:
        pass  # 后台任务静默失败，不影响主流程


async def generate_video(prompt: str = "", width: int = 1152, height: int = 768,
                         num_frames: int = 121, frame_rate: int = 24) -> str:
    """生成一段短视频（约 5 秒）。prompt 建议用英文描述（主体+动作+场景+镜头运动+光线+风格），
    中文描述请先翻译成英文再传入；width/height 必须是 64 的倍数。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return "错误：缺少视频描述 prompt"
    try:
        width = max(256, int(width) // 64 * 64)
        height = max(256, int(height) // 64 * 64)
        num_frames = max(9, int(num_frames) // 8 * 8 + 1)
        frame_rate = max(1, min(60, int(frame_rate or 24)))
        payload = {
            "model": VIDEO_MODEL,
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }
        data = await asyncio.to_thread(_http_json, "POST", f"{API_ROOT}/v1/videos",
                                       payload, 90)
        video_id = str(data.get("video_id") or data.get("id") or "").strip()
        if not video_id:
            return f"错误：Agnes 建视频任务失败，无 video_id：{str(data)[:200]}"

        # 先同步等一小段（45s）：多数小任务这期间能完成，视频直接随本轮回复给出
        try:
            res = await _poll_video(video_id, max_wait=45.0)
            if not res.get("_timeout"):
                path = await _finish_video(video_id)
                return (f"视频已生成，已保存到本地：{path}\n"
                        f"{_asset_mark('video', path)}")
        except Exception as e:
            return f"错误：视频生成失败 - {e}"

        # 没等完：转后台轮询，完成后再推给用户
        asyncio.create_task(_background_watch(video_id, prompt))
        return (f"视频生成任务已提交（任务ID：{video_id}），正在后台生成，"
                f"预计 1~3 分钟；完成之后我会直接把视频发给你～")
    except Exception as e:
        return f"错误：Agnes 生视频失败 - {e}"
