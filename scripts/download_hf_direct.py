# -*- coding: utf-8 -*-
"""HuggingFace 直接下载（绕过 snapshot_download 卡顿）：小文件走 hub，大文件直连镜像 GET 带进度条。
用法：
  python scripts/download_hf_direct.py --repo Qwen/Qwen3-TTS-12Hz-0.6B-Base --dest D:\\models\\qwen3-tts-0.6b-base
"""
import argparse
import os
import sys
import time
import urllib.request

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def download(url: str, dest: str):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".incomplete"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        t0 = time.time()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                pct = done / total * 100
                spd = done / 1024 / 1024 / max(time.time() - t0, 0.01)
                print(f"\r{dest.split(chr(92))[-1]}: {done/1e6:.1f}/{total/1e6:.1f} MB ({pct:.1f}%) {spd:.1f} MB/s", end="", flush=True)
    print()
    if os.path.exists(dest):
        os.remove(dest)
    os.replace(tmp, dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dest", required=True)
    args = ap.parse_args()

    from huggingface_hub import HfApi, hf_hub_download
    os.makedirs(args.dest, exist_ok=True)
    files = HfApi().list_repo_files(args.repo)
    big = [f for f in files if f.endswith(".safetensors")]
    small = [f for f in files if f not in big]

    print("小文件下载…")
    for f in small:
        try:
            hf_hub_download(repo_id=args.repo, filename=f, local_dir=args.dest)
            print("  OK:", f)
        except Exception as e:
            print("  失败(稍后直连):", f, e)

    print("大文件直连下载…")
    for f in big:
        url = f"{os.environ['HF_ENDPOINT']}/{args.repo}/resolve/main/{f}"
        dest = os.path.join(args.dest, f.replace("/", os.sep))
        print("下载:", f)
        download(url, dest)

    print()
    print("全部完成 ✅ ->", args.dest)
    input("按回车关闭窗口…")


if __name__ == "__main__":
    main()
