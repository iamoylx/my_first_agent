import asyncio
import base64
import os
import sys
from pathlib import Path
sys.path.insert(0, ".")
from skills.vision import describe_image

async def main():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    for f in ["素材/三视图/1000148096.jpg", "素材/三视图/1000148182.png"]:
        p = Path(f)
        if not p.exists():
            print(f"[missing] {f}")
            continue
        suffix = p.suffix.lower()
        mime = "image/jpeg" if suffix == ".jpg" else "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode()
        data_url = f"data:{mime};base64,{b64}"
        print(f"=== {p.name} ({len(b64)//1024} KB) ===")
        desc = await describe_image(data_url, api_key, base_url="http://127.0.0.1:11434/v1", model="qwen3-vl:4b")
        print(desc if desc else "(识别失败)")
        print()

asyncio.run(main())
