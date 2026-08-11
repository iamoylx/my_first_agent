# -*- coding: utf-8 -*-
"""MCP 配置加载：mcp/config.json（缺省时无 server，框架空跑不报错）。"""
import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "servers": {}
}


def load_config(config_dir=None):
    """读取 mcp/config.json；返回 {"servers": {name: {enabled, command, args, env, ...}}}。"""
    config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parent.parent / "mcp"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = config_dir / "config.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            for name, srv in (user.get("servers") or {}).items():
                cfg.setdefault("servers", {})[name] = srv
        except Exception:
            pass
    return cfg
