# -*- coding: utf-8 -*-
"""下载 Qwen3-TTS-12Hz-0.6B-CustomVoice（情感控制 + 预置音色 + 声音克隆）到 D 盘。"""
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from huggingface_hub import snapshot_download

import argparse

PARSER = argparse.ArgumentParser(description="下载 Qwen3-TTS 模型（默认 CustomVoice，可传 --repo/--dest 换 Base 等）")
PARSER.add_argument("--repo", default="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
PARSER.add_argument("--dest", default=r"D:\models\qwen3-tts-0.6b-customvoice")
ARGS = PARSER.parse_args()
REPO = ARGS.repo
DEST = ARGS.dest

def main():
    print("=" * 60)
    print(f"仓库  : {REPO}")
    print(f"目标  : {DEST}")
    print(f"镜像  : {os.environ['HF_ENDPOINT']}")
    print("=" * 60)
    path = snapshot_download(repo_id=REPO, local_dir=DEST)
    print()
    print("下载完成 ✅")
    print("本地路径:", path)
    print("按回车关闭窗口…")
    input()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("下载失败:", e)
        print("按回车关闭窗口…")
        input()
