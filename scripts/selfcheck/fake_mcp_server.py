# -*- coding: utf-8 -*-
"""假 MCP server：框架测试用（echo/add 两个工具）。"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-test")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入的文本。"""
    return f"echo: {text}"


@mcp.tool()
def add(a: float, b: float) -> float:
    """两个数字相加。"""
    return a + b


if __name__ == "__main__":
    mcp.run()
