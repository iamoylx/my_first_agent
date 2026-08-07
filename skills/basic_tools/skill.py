# skills/basic_tools/skill.py
from datetime import datetime

async def get_current_time() -> str:
    """获取当前系统时间"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

async def calculator(a: float, b: float, op: str) -> str:
    """简易计算器，支持 + - * /"""
    if op == "add":
        res = a + b
    elif op == "sub":
        res = a - b
    elif op == "mul":
        res = a * b
    elif op == "div":
        if b == 0:
            return "错误：除数不能为0"
        res = a / b
    else:
        return "不支持的运算"
    return f"计算结果 = {res}"
