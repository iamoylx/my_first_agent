# -*- coding: utf-8 -*-
"""通用 MCP 桥（plan.md B1）——让小满作为 MCP 客户端接入外部服务。

设计原则：
  - 配置式：mcp/config.json 声明启用哪些 MCP server（stdio transport）。
  - 自动注册：启动时连接各 server → 列出工具 → 转成 OpenAI function schema，
    动态合并进小满的工具列表（与 skills 并列，模型可直接调用）。
  - 数据源预留：MCP 工具返回的数据可通过 get_tool 供主动触发源（TriggerSource）
    消费（健康/日历等），实现"MCP 数据 → 小满主动关心"。
  - 隔离：任一 server 启动失败不影响其它；工具名冲突时 MCP 工具以 mcp_<server>_ 前缀注册。
"""
from .manager import MCPManager

__all__ = ["MCPManager"]
