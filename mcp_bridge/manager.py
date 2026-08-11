# -*- coding: utf-8 -*-
"""MCPManager：连接 stdio MCP server → 注册工具 → 调用。

使用官方 mcp Python SDK（ClientSession / stdio_client）。
支持多个 server 并存；每个 server 一把锁串行调用。
"""
import asyncio
import contextlib
import json

from .config import load_config


class MCPManager:
    def __init__(self, config_dir=None):
        self.config = load_config(config_dir)
        self._sessions = {}      # tool_name -> (server_name, ClientSession)
        self._locks = {}         # server_name -> asyncio.Lock
        self._tool_meta = {}     # tool_name -> (server_name, tool schema)
        self._stacks = {}        # server_name -> AsyncExitStack（管理连接生命周期）
        self._started = False

    # ---------------- 生命周期 ----------------
    async def start(self):
        """连接所有 enabled 的 server，收集工具。失败单点隔离。"""
        if self._started:
            return
        servers = self.config.get("servers", {}) or {}
        for name, srv in servers.items():
            if not srv.get("enabled", False):
                continue
            try:
                await self._connect_server(name, srv)
            except Exception as e:
                print(f"[MCP] server '{name}' 启动失败，已跳过: {e}")
        self._started = True

    async def stop(self):
        for name, stack in list(self._stacks.items()):
            try:
                await stack.aclose()
            except Exception:
                pass
        self._stacks.clear()
        self._sessions.clear()
        self._tool_meta.clear()
        self._started = False

    async def _connect_server(self, name, srv):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=srv.get("command", ""),
            args=list(srv.get("args", []) or []),
            env=dict(srv.get("env", {}) or {}) or None,
        )
        stack = contextlib.AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await session.list_tools()
        except Exception:
            await stack.aclose()
            raise
        self._stacks[name] = stack
        self._locks[name] = asyncio.Lock()
        for tool in tools.tools:
            tname = f"mcp_{name}_{tool.name}"
            self._sessions[tname] = (name, session)
            self._tool_meta[tname] = (name, tool)
        print(f"[MCP] server '{name}' 已连接，注册 {len(tools.tools)} 个工具")

    # ---------------- 工具注册 ----------------
    def openai_tools(self):
        """返回 OpenAI function schema 列表（合并进小满 tools）。"""
        out = []
        for tname, (server_name, tool) in self._tool_meta.items():
            # mcp 2.0 用 input_schema（下划线）；旧版兼容 inputSchema
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)                 or {"type": "object", "properties": {}}
            out.append({
                "type": "function",
                "function": {
                    "name": tname,
                    "description": (tool.description or f"{server_name} 提供的工具"),
                    "parameters": schema,
                },
            })
        return out

    def tool_map(self):
        """返回 {tool_name: async wrapper}，供小满 tool_map 使用。"""
        return {tname: self._make_wrapper(tname) for tname in self._tool_meta}

    def _make_wrapper(self, tname):
        async def _wrapper(**kwargs):
            return await self.call(tname, kwargs)
        _wrapper.__name__ = tname
        _wrapper.__doc__ = self._tool_meta[tname][1].description
        return _wrapper

    # ---------------- 调用 ----------------
    async def call(self, tname, arguments: dict) -> str:
        if tname not in self._sessions:
            return f"错误：未知 MCP 工具 {tname}"
        server_name, session = self._sessions[tname]
        lock = self._locks.get(server_name)
        try:
            if lock is not None:
                async with lock:
                    result = await session.call_tool(tname.split("_", 2)[2] if tname.startswith("mcp_") else tname,
                                                     arguments=arguments or {})
            else:
                result = await session.call_tool(tname, arguments=arguments or {})
            return self._result_to_text(result)
        except Exception as e:
            return f"错误：MCP 工具 {tname} 调用失败 - {e}"

    @staticmethod
    def _result_to_text(result) -> str:
        parts = []
        for c in getattr(result, "content", []) or []:
            ct = getattr(c, "type", "")
            if ct == "text":
                parts.append(getattr(c, "text", ""))
            elif ct == "image":
                parts.append(f"[图片 {getattr(c, 'mimeType', '')} {len(getattr(c, 'data', '') or '')}B]")
            else:
                parts.append(str(getattr(c, "text", c)))
        if not parts:
            parts.append("(无内容)")
        return "\n".join(parts)

    # ---------------- 数据源接口（供 TriggerSource 使用）----------------
    def available_tools(self) -> list:
        return sorted(self._tool_meta.keys())

    async def get_tool(self, tname, arguments: dict = None) -> str:
        """供主动触发源读取数据（健康/日历等），失败返回 None 不抛异常。"""
        try:
            return await self.call(tname, arguments or {})
        except Exception:
            return None
