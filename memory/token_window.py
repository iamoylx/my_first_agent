# memory/token_window.py
# 基于 token 计数的滑动窗口：超阈值时丢弃最古老的"普通对话"，
# 永远保护 system 提示词与所有工具相关消息。本地运算，零 LLM 延迟。
import os

# ---------- 1) token 估算：优先 tiktoken，否则用字符启发式 ----------
try:
    import tiktoken
    # cl100k_base 是 OpenAI 的编码，对中文也近似有效。
    # 注意：get_encoding 在离线/网络受限时会抛 ValueError（而非 ImportError），
    # 必须一并捕获，否则会中断整个模块导入、拖垮桌宠进程。
    _ENC = tiktoken.get_encoding("cl100k_base")

    def _count(text: str) -> int:
        return len(_ENC.encode(text or ""))
except Exception:
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
    """滑动窗口裁剪主函数（按"可丢弃单元"整组丢弃）。

    单元划分：
      - 普通 user / assistant：单条即一个单元；
      - 工具交换：assistant(tool_calls) + 紧跟的 tool* 结果 + 其后 assistant 回复，
        整组作为一个单元（保证配对不悬空，不会触发 400）。
    system 永远置顶保留；超预算时从最旧单元开始整组丢弃。

    相比旧实现：旧版"受保护消息永不删"会让天气/提醒等工具结果无限累积，
    撑爆本地模型 16K 上下文（表现为"只会查天气/context 超限"）。
    新版允许丢弃最旧的工具交换单元，只保留最近几轮的工具上下文。
    """
    sys_msgs = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]

    limit = int(max_tokens * soft_ratio)          # 软阈值 = 80% 窗口
    total = sum(msg_tokens(m) for m in body)
    if total <= limit:
        return messages                           # 没超阈值，原样返回

    # ---- 把 body 切成单元 ----
    units = []
    i = 0
    while i < len(body):
        m = body[i]
        if m.get("tool_calls"):
            unit = [m]
            j = i + 1
            while j < len(body) and body[j]["role"] == "tool":
                unit.append(body[j])
                j += 1
            # 工具执行后的 assistant 回复并入该单元（它是这一轮工具交换的收尾）
            if j < len(body) and body[j]["role"] == "assistant" and not body[j].get("tool_calls"):
                unit.append(body[j])
                j += 1
            units.append(unit)
            i = j
        else:
            units.append([m])
            i += 1

    # ---- 从最旧开始整组丢弃，直到总量 <= limit ----
    drop = 0
    for unit in units:
        if total <= limit:
            break
        total -= sum(msg_tokens(m) for m in unit)
        drop += 1

    kept = [m for u in units[drop:] for m in u]

    # ---- 安全兜底：首条非 system 必须是 user（否则 API 400）----
    if not kept or kept[0]["role"] != "user":
        for m in reversed(body):
            if m["role"] == "user":
                if m not in kept:
                    kept.insert(0, m)
                break
    return sys_msgs + kept

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
