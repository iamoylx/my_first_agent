# -*- coding: utf-8 -*-
"""修复验证：1) detect 直接采用完整回答 2) 最终回答夹带 DSML → 执行工具+续写结尾。"""
import asyncio, pathlib, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from memory.store import MemoryStore
import core.agent_core as core

async def fake_get_time():
    return "2026-08-11 22:00:00"

TOOL_MAP = {"get_current_time": fake_get_time}

async def run_turn(fake_detect, fake_stream, user_text="记住我明天下午三点开会"):
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(base_dir=td, user_id="t")
        msgs, _ = core.build_initial_messages(mem, with_history=False)
        tokens = []
        core.detect_tool_call = fake_detect
        core.stream_final = fake_stream
        await core.process_turn(
            messages=msgs, user_text=user_text, mem=mem,
            tools=[], tool_map=TOOL_MAP, api_key="fake",
            on_token=tokens.append,
        )
        return "".join(tokens), msgs, mem

# ---------- 场景1：detect 无工具 → 直接采用（stream_final 不应被调用）----------
st1 = {"detect": 0, "stream": 0}
async def d1(messages, tools, api_key):
    st1["detect"] += 1
    if st1["detect"] == 1:
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}]}
    return {"role": "assistant", "content": "爸爸，现在是晚上十点，还不到关心你的时候哦～"}

async def s1(messages, api_key, **kw):
    st1["stream"] += 1
    return "不应走到这里"

reply1, msgs1, _ = asyncio.run(run_turn(d1, s1))
print("场景1 reply:", reply1)
assert st1["stream"] == 0, f"stream_final 不应被调用，实际 {st1['stream']}"
assert "晚上十点" in reply1 and "爸爸" in reply1, reply1
assert msgs1[-1]["role"] == "assistant" and "晚上十点" in msgs1[-1]["content"]
print("PASS 场景1: detect 直接采用完整回答（省一次调用）")

# ---------- 场景2：detect 空内容 → stream_final 夹带 DSML → 执行工具 + 续写 ----------
st2 = {"detect": 0, "stream": 0}
async def d2(messages, tools, api_key):
    st2["detect"] += 1
    return {"role": "assistant", "content": ""}

async def s2(messages, api_key, on_token=None, on_reasoning=None, on_stripped_dsml=None):
    st2["stream"] += 1
    if st2["stream"] == 1:
        head = "爸爸，你看，我把它牢牢记住啦！💖现在几点了呀，我看看是不是快到该关心你的时候了："
        if on_token:
            on_token(head)
        if on_stripped_dsml:
            on_stripped_dsml([{"id": "dsml_0_get_current_time", "type": "function",
                               "function": {"name": "get_current_time", "arguments": "{}"}}])
        return head
    tail = "现在是晚上十点，还不到关心你的时候呢～你先忙，小满一直等你哦！"
    if on_token:
        on_token(tail)
    return tail

reply2, msgs2, mem2 = asyncio.run(run_turn(d2, s2))
print("场景2 reply:", reply2)
assert st2["stream"] == 2, f"stream_final 应被调用 2 次（首答+续写），实际 {st2['stream']}"
assert "牢牢记住" in reply2 and "还不到关心你的时候" in reply2, reply2
# 工具执行了：messages 里有 tool 消息
tool_msgs = [m for m in msgs2 if m["role"] == "tool"]
assert tool_msgs, "工具未执行"
assert "2026-08-11 22:00:00" in tool_msgs[0]["content"], tool_msgs
# 临时 system（续写提示）已移除
assert not any(m.get("role") == "system" and "被截断" in str(m.get("content", "")) for m in msgs2), "临时 system 未清理"
# 最终 assistant 消息 = 过渡语 + 续写
assert msgs2[-1]["role"] == "assistant" and "还不到关心你的时候" in msgs2[-1]["content"]
# 记忆零污染（临时目录只应有会话文件）
files = [str(p.relative_to(mem2.base_dir)) for p in pathlib.Path(mem2.base_dir).rglob("*") if p.is_file()]
assert all("thinking" not in f and "active" not in f for f in files), files
print("PASS 场景2: DSML 剥离后执行工具 + 续写结尾（不再冒号截断）")

print("ALL FIX TESTS PASSED")
