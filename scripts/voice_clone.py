# -*- coding: utf-8 -*-
"""小满专属音色克隆工具（Qwen3-TTS-12Hz-0.6B-Base，免训练，ICL 声音克隆）。

用法：
  python scripts/voice_clone.py --ref <参考音频路径> --ref-text "<参考文本>" [--text "<要合成的测试句>"] [--out <输出wav>]

说明：
  - ref：10~60 秒干净人声（wav/mp3 均可，无背景音乐/杂音，越清晰越好）
  - ref-text：与参考音频【逐字对应】的文字（越准，克隆音色越像）
  - 首次运行会加载 Base 模型（约 30~60s），之后合成一句约 20~30s
  - 参考音频用你想让小满拥有的声音（自己录 / 找一段干净的素材）
"""
import argparse
import base64
import io
import os
import sys
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODEL_DIR = os.getenv("QWEN3_TTS_BASE_MODEL", r"D:\models\qwen3-tts-0.6b-base")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="参考音频路径（10~60s 干净人声）")
    ap.add_argument("--ref-text", required=True, help="参考音频逐字对应的文字")
    ap.add_argument("--text", default="爸爸，我是小满呀，这是用你的声音克隆出来的我，喜欢吗？",
                    help="要合成的测试句")
    ap.add_argument("--out", default=r"tmp\voice_clone_test.wav", help="输出 wav 路径")
    args = ap.parse_args()

    import torch
    from qwen_tts import Qwen3TTSModel

    print("加载 Base 模型（首次约 30~60s）…", flush=True)
    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    print(f"模型就绪（{time.time()-t0:.0f}s）", flush=True)

    print("构建克隆 prompt（参考音频 + 参考文本）…", flush=True)
    prompt = model.create_voice_clone_prompt(
        ref_audio=args.ref,
        ref_text=args.ref_text,
        x_vector_only_mode=False,   # ICL 模式：参考文本 + 语音都参与，相似度最高
    )

    t1 = time.time()
    wavs, sr = model.generate_voice_clone(
        text=args.text,
        language="Chinese",
        voice_clone_prompt=prompt,
        do_sample=True,
        top_k=50,
        top_p=0.9,
        temperature=0.8,
        repetition_penalty=1.05,
        non_streaming_mode=True,
    )
    print(f"合成完成（{time.time()-t1:.0f}s）", flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    import soundfile as sf
    sf.write(args.out, wavs[0], sr, format="WAV")
    print("已保存:", os.path.abspath(args.out))
    print()
    print("如果音色满意 → 告诉我，我把它接进日常朗读（所有「🔊 朗读」都用这个专属音色）。")


if __name__ == "__main__":
    main()
