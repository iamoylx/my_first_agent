# -*- coding: utf-8 -*-
'''本地生图工具（WebUI Forge / Automatic1111 API）

- Forge 默认监听 http://127.0.0.1:7860，开启 --api 参数后暴露 /sdapi/v1/txt2img
- 通过 Forge API 生图，不经过任何云端，图片只存本地
- 亲密模式下由 agent 自动调用此工具生成隐晦内容；本地 Forge 未启动时会自动拉起
- 温馨模式/非亲密模式：仍走 Agnes API（由 agnes_gen skill 负责）

启用条件：
  1. AGENT_FORGE_BASE 环境变量（默认 http://127.0.0.1:7860）
  2. AGENT_FORGE_DIR 环境变量（默认 D:/DevTools/stable-diffusion-webui-forge）
  3. 首次调用会自动后台启动 Forge（start-api.bat），等待就绪后再生图

API 文档：https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API
Forge API 兼容同一套接口。
'''
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import aiohttp

GEN_DIR = Path(os.getenv('AGENT_GEN_DIR') or r'D:\document\Myprojects\学习\AGENT\generated')
GEN_DIR.mkdir(parents=True, exist_ok=True)

FORGE_BASE = os.getenv('AGENT_FORGE_BASE', 'http://127.0.0.1:7860').rstrip('/')
FORGE_DIR = os.getenv('AGENT_FORGE_DIR', r'D:/DevTools/stable-diffusion-webui-forge')
FORGE_START_BAT = os.path.join(FORGE_DIR, 'start-api.bat')
_ASSET_TAG = '@@ASSET@@'


def _asset_mark(kind: str, path) -> str:
    return f'{_ASSET_TAG}{json.dumps({"kind": kind, "path": str(path)}, ensure_ascii=False)}{_ASSET_TAG}'


def _intimacy_intimate() -> bool:
    '''当前档案亲密程度是否为亲密（intimate）。温馨=warm/未设置=非亲密。'''
    try:
        from memory.store import MemoryStore
        mem = MemoryStore(base_dir=os.getenv('AGENT_MEMORY_DIR') or None,
                          user_id=os.getenv('AGENT_USER_ID', 'default'))
        prof = mem.load_profile() or {}
        facts = prof.get('facts', {}) or {}
        v = facts.get('rule_intimacy_level')
        if isinstance(v, dict):
            if v.get('active') is False:
                return False
            return str(v.get('value') or '') == 'intimate'
        return str(v or '') == 'intimate'
    except Exception:
        return False


async def _forge_alive() -> bool:
    '''Forge API 是否可达。'''
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{FORGE_BASE}/sdapi/v1/sd-models',
                                   timeout=aiohttp.ClientTimeout(total=3)) as resp:
                return resp.status == 200
    except Exception:
        return False


def _start_forge() -> bool:
    '''后台静默启动 Forge（start-api.bat 自带防 GitHub 卡死环境变量 + --api）。'''
    try:
        if not os.path.exists(FORGE_START_BAT):
            return False
        log = os.path.join(FORGE_DIR, 'tmp', 'forge-api.log')
        err = os.path.join(FORGE_DIR, 'tmp', 'forge-api.err.log')
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, 'ab') as fo, open(err, 'ab') as fe:
            subprocess.Popen(
                ['cmd.exe', '/c', FORGE_START_BAT],
                cwd=FORGE_DIR,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                stdout=fo, stderr=fe,
            )
        return True
    except Exception:
        return False


async def _wait_forge(timeout: float = 150.0) -> bool:
    '''等待 Forge 就绪（冷启动约 40~90 秒）。'''
    waited = 0.0
    while waited < timeout:
        if await _forge_alive():
            return True
        await asyncio.sleep(1.0)
        waited += 1.0
    return False


async def generate_image(prompt: str, negative_prompt: str = '',
                          width: int = 768, height: int = 768,
                          steps: int = 20, sampler: str = 'Euler a',
                          cfg_scale: float = 7.0, seed: int = -1,
                          num_images: int = 1) -> str:
    '''通过本地 Forge API 生成图片。prompt 支持中英混合。

    参数说明：
      prompt          生图提示词（正负向描述均可写入，推荐包含风格描述）
      negative_prompt 否定提示词（减少不想要的内容）
      width/height    图片尺寸（默认 768x768，Forge 支持最大 1024x1024）
      steps           采样步数（1-50，越高越精细但越慢）
      sampler         采样器名称（Euler a / DPM++ 2M Karras / DDIM 等）
      cfg_scale       提示词相关性（1-20，默认 7）
      seed            随机种子（-1 = 随机，填数字可复现）
      num_images      一次生成几张（1-4）
    '''
    if not prompt or not prompt.strip():
        return '错误：缺少 prompt 描述'

    intimate = _intimacy_intimate()

    # 探测 Forge；亲密模式下未启动则自动后台拉起并等待就绪（隐私内容只走本地，绝不上云）
    forge_ok = await _forge_alive()
    if not forge_ok and intimate:
        _start_forge()
        forge_ok = await _wait_forge(timeout=150.0)
    if not forge_ok:
        if intimate:
            return (f'错误：本地 Forge 未能启动（隐私内容只在本地生成，不会发送到云端）。'
                    f'请手动双击 {FORGE_START_BAT} 启动后再试。')
        # 温馨/日常：本地没起就回退 Agnes 云端生图（对 agent 透明，保证总能出图）
        try:
            from skills.agnes_gen import generate_image as _agnes_gen
            return await _agnes_gen(prompt=prompt.strip(), size=f'{width}x{height}')
        except Exception as e:
            return f'错误：本地 Forge 未启动，且自动回退 Agnes 生图也失败 - {e}'

    payload = {
        'prompt': prompt.strip(),
        'negative_prompt': negative_prompt.strip(),
        'width': min(1024, max(64, int(width))),
        'height': min(1024, max(64, int(height))),
        'steps': min(50, max(1, int(steps))),
        'sampler_name': sampler,
        'cfg_scale': float(cfg_scale),
        'seed': int(seed),
        'batch_size': min(4, max(1, int(num_images))),
        'n_iter': 1,
    }

    def _sync_gen():
        import urllib.request
        req = urllib.request.Request(
            f'{FORGE_BASE}/sdapi/v1/txt2img',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read().decode('utf-8'))

    try:
        data = await asyncio.to_thread(_sync_gen)
    except Exception as e:
        return f'错误：生图请求失败 - {e}'

    images = data.get('images') or []
    if not images:
        return '错误：Forge 返回空结果'

    paths = []
    for i, b64_img in enumerate(images):
        if not b64_img or not b64_img.startswith('data:'):
            raw = b64_img.encode('utf-8')
        else:
            import base64
            raw = base64.b64decode(b64_img.split(',', 1)[1] if ',' in b64_img else b64_img)
        idx_str = '' if len(images) == 1 else f'-{i+1}'
        out_path = GEN_DIR / f'local-img-{int(time.time()*1000)}{idx_str}.png'
        out_path.write_bytes(raw)
        paths.append(str(out_path))

    if len(paths) == 1:
        return (f'已在本地生成图片并保存至：{paths[0]}\n'
                f'{_asset_mark("image", paths[0])}')
    else:
        return (f'已生成 {len(paths)} 张图片，保存至：\n'
                + '\n'.join(f'  - {p}' for p in paths)
                + f'\n{_asset_mark("image", paths[-1])}')


# ===================== Tool 定义（供 agent_core 注册）=====================
TOOLS = [{
    'type': 'function',
    'function': {
        'name': 'local_generate_image',
        'description': '生成图片：优先走本地 Stable Diffusion（WebUI Forge，隐私不出本机，未启动会自动后台拉起）；温馨模式本地没起时改用 Agnes 云端生成。注意：步数建议 20-25（30 步会非常慢），尺寸建议 768 边长以内。',
        'parameters': {
            'type': 'object',
            'properties': {
                'prompt': {'type': 'string', 'description': '图片描述（支持中英文，描述画面内容、风格、光线等）'},
                'negative_prompt': {'type': 'string', 'description': '不希望出现的元素（如：blurry, low quality, deformed）'},
                'width': {'type': 'integer', 'description': '宽度，默认768，最大1024', 'default': 768},
                'height': {'type': 'integer', 'description': '高度，默认768，最大1024', 'default': 768},
                'steps': {'type': 'integer', 'description': '采样步数，1-50，默认20', 'default': 20},
                'sampler': {'type': 'string', 'description': '采样器名称，默认 Euler a', 'default': 'Euler a'},
                'cfg_scale': {'type': 'number', 'description': '提示词相关度，1-20，默认7', 'default': 7.0},
                'seed': {'type': 'integer', 'description': '随机种子，-1表示随机', 'default': -1},
                'num_images': {'type': 'integer', 'description': '生成数量，1-4，默认1', 'default': 1},
            },
            'required': ['prompt'],
        },
    },
}]

TOOL_MAP = {'local_generate_image': generate_image}
