# memory/token_window.py
# 基于 token 计数的滑动窗口：超阈值时丢弃最古老的"普通对话"，
# 永远保护 system 提示词与所有工具相关消息。本地运算，零 LLM 延迟。
import os

# ---------- 1) token 估算：优先 tiktoken，否则用字符启发式 ----------
try:
    import tiktoken
    # cl100k_base 是 OpenAI 的编码，对中文也近似有效，且离线可用
    _ENC = tiktoken.get_encoding("cl100k_base")

    def _count(text: str) -> int:
        return len(_ENC.encode(text or ""))
except ImportError:
    # 无依赖回退：CJK 汉字≈1 token/字，英文按字母数/3 粗估
    def _count(text: str) -> int:
        if not text:
            return 0
        n, word = 0, 0
        for ch in text:
            if ord(ch) > 0x4E00:          # 汉字
                n += 1
            elif ch.isalnum():            # 英文单词内累计字母
                word += 1
            elif ch.isspace():            # 遇空格结算一次
                n += word // 3
                word = 0
        return n + word // 3


def msg_tokens(m: dict) -> int:
    """估算单条消息占用的 token（role 名等少量开销忽略，只算内容）。"""
    n = _count(m.get("content", "") or "")
    # tool_call 的函数参数也占 token，需计入（否则会低估触发阈值）
    for tc in (m.get("tool_calls") or []):
        n += _count(tc["function"].get("arguments", ""))
    return n


def is_protected(m: dict) -> bool:
    """判断消息是否受保护（不可丢弃）。
    保护对象 = 三类：
      - system：系统提示词，动了就改了 agent 人格/规则
      - 含 tool_calls 的 assistant：工具调用指令，丢了会让后续 tool 悬空 → 400
      - role=="tool"：工具返回结果，必须跟随其 tool_call 成对存在
    其余普通 user/assistant 才是可丢弃对象。"""
    if m["role"] == "system":
        return True
    if m.get("tool_calls"):
        return True
    if m["role"] == "tool":
        return True
    return False


def prune(messages: list, max_tokens: int = 12000, soft_ratio: float = 0.8) -> list:
    """滑动窗口裁剪主函数。
    原理：
      1) 把 system 单独拎出来永远置顶；其余消息算 body。
      2) 统计 body 总 token；未超 soft 阈值(80%)直接原样返回，零开销。
      3) 超阈值则从最旧(body 头部)向新扫描：受保护的必留；
         未保护的若删掉后仍超阈值，就丢弃（即"丢最古老的普通对话"）。
      4) 安全兜底：若裁剪后首条非 system 消息是 assistant（会触发 400），
         则把被丢的最近一条普通 user 补回开头，保证 system 后首条是 user。
    """
    sys_msgs = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]

    limit = int(max_tokens * soft_ratio)          # 软阈值 = 80% 窗口
    total = sum(msg_tokens(m) for m in body)
    if total <= limit:
        return messages                           # 没超阈值，原样返回

    kept = []
    for m in body:                                # 从最旧 → 最新遍历
        t = msg_tokens(m)
        if is_protected(m):
            kept.append(m)                         # 受保护：无条件保留
            continue
        # 未保护：删掉这条仍超阈值才丢，否则保留（保住尽量多的近期上下文）
        if total - t > limit:
            total -= t                             # 丢弃这条最古老的普通消息
            continue
        kept.append(m)

    # ---- 安全兜底：保证裁剪后"首条非 system"是 user，避免 400 ----
    if kept and kept[0]["role"] != "user":
        for m in body:                            # 找回被丢的最近普通 user
            if m["role"] == "user" and m not in kept:
                kept.insert(0, m)
                break

    return sys_msgs + kept
