# -*- coding: utf-8 -*-
"""本地 TTS 服务（B 计划：Qwen3-TTS-12Hz-0.6B-CustomVoice）。

设计：
  - 独立进程，由 agent-server.py 在「首次点朗读且选择本地引擎」时拉起
  - 模型懒加载：启动进程不加载，首个请求才 load（约 5~15s），之后常驻
  - 情感控制：instruct 用自然语言指令（如「用温柔开心的语气」）
  - 空闲自动退出：QWEN3_TTS_IDLE 秒无请求后退出，释放显存（默认 600s）
  - 协议与 agent-server /tts 一致：POST JSON -> {"ok", "audio_b64", "mime"}
启动：python desktop-client/tts_server.py   （端口 QWEN3_TTS_PORT，默认 18900）
"""
import asyncio
import base64
import io
import os
import sys
import time
from pathlib import Path

from aiohttp import web

MODEL_DIR = os.getenv("QWEN3_TTS_MODEL", r"D:\models\qwen3-tts-0.6b-customvoice")
PORT = int(os.getenv("QWEN3_TTS_PORT", "18900"))
IDLE_EXIT = int(os.getenv("QWEN3_TTS_IDLE", "600"))   # 10 分钟空闲自动退出
SYNTH_TIMEOUT = int(os.getenv("QWEN3_TTS_TIMEOUT", "180"))
TTS_DEFAULT_SPEAKER = os.getenv("QWEN3_TTS_SPEAKER", "vivian")  # 预置甜美女声

_model = None
_speakers = None
_last_active = time.time()


def _log(*args):
    print("[TTS]", *args, flush=True)


def _load_model():
    global _model, _speakers
    if _model is not None:
        return _model
    _log("加载模型中…", MODEL_DIR)
    import torch
    from qwen_tts import Qwen3TTSModel
    _model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    try:
        _speakers = _model.model.get_supported_speakers()
    except Exception:
        _speakers = None
    _log("模型就绪；可用音色:", _speakers)
    return _model


def _synthesize(text: str, instruct: str, speaker: str = "") -> bytes:
    global _last_active
    model = _load_model()
    speakers = list(_speakers or ["vivian"])
    if speaker:
        low = speaker.strip().lower()
        hit = [s for s in speakers if s.lower() == low]
        if hit:
            speaker = hit[0]
        else:
            speaker = TTS_DEFAULT_SPEAKER if TTS_DEFAULT_SPEAKER in speakers else speakers[0]
    else:
        speaker = TTS_DEFAULT_SPEAKER if TTS_DEFAULT_SPEAKER in speakers else speakers[0]
    _log("合成中… speaker=%s instruct=%r len=%d", speaker, instruct, len(text))
    wavs, sr = model.generate_custom_voice(
        text=text,
        speaker=speaker,
        language="Chinese",
        instruct=instruct or "",
        do_sample=True,
        top_k=50,
        top_p=0.9,
        temperature=0.8,
        repetition_penalty=1.05,
        non_streaming_mode=True,
    )
    wav = wavs[0]
    buf = io.BytesIO()
    import soundfile as sf
    sf.write(buf, wav, sr, format="WAV")
    data = buf.getvalue()
    _last_active = time.time()
    _log("完成，%d 字节，sr=%d", len(data), sr)
    return data


async def tts_handler(request):
    global _last_active
    _last_active = time.time()
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "text 不能为空"}, status=400)
    instruct = (body.get("instruct") or "").strip()
    speaker = (body.get("speaker") or TTS_DEFAULT_SPEAKER).strip()
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(_synthesize, text, instruct, speaker), timeout=SYNTH_TIMEOUT
        )
    except asyncio.TimeoutError:
        return web.json_response({"error": "TTS 合成超时"}, status=504)
    except Exception as e:
        _log("合成失败:", repr(e))
        return web.json_response({"error": f"TTS 合成失败：{e}"}, status=500)
    if not data:
        return web.json_response({"error": "TTS 未生成音频"}, status=500)
    return web.json_response({
        "ok": True,
        "audio_b64": base64.b64encode(data).decode("ascii"),
        "mime": "audio/wav",
    })


async def idle_watcher(app):
    """空闲超时自动退出（释放显存）。"""
    while True:
        await asyncio.sleep(5)
        if _model is not None and time.time() - _last_active > IDLE_EXIT:
            _log("空闲 %d 秒，退出释放显存", IDLE_EXIT)
            loop = asyncio.get_running_loop()
            loop.stop()
            return


def main():
    app = web.Application()
    app.router.add_post("/tts", tts_handler)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok", "model": "qwen3-tts-0.6b-customvoice"}))

    async def _startup(app):
        # fire-and-forget：不能被 on_startup await（否则 run_app 永远卡在启动阶段）
        asyncio.create_task(idle_watcher(app))

    app.on_startup.append(_startup)
    _log("监听端口", PORT, "模型", MODEL_DIR)
    web.run_app(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
