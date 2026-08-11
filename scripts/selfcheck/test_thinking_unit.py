"""阶段0 单元测试：mock 模型层，验证 on_thinking 思考轨迹触发 + 记忆零污染。"""
import asyncio, json, pathlib, sys, tempfile
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")

from memory.store import MemoryStore
import core.agent_core as core

# ---- mock 模型层 ----
state = {"detect": 0}

async def fake_detect(messages, tools, api_key, **kw):
    state["detect"] += 1
    if state["detect"] == 1:
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}},
        ]}
    return {"role": "assistant", "content": ""}

async def fake_stream(messages, api_key, on_token=None, on_reasoning=None, **kw):
    if on_reasoning:
        on_reasoning("用户想知道时间，直接调用工具返回即可")
    for t in ["现", "在", "是"]:
        if on_token:
            on_token(t)
    return "现在是"

core.detect_tool_call = fake_detect
core.stream_final = fake_stream

async def main():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(base_dir=td, user_id="test")
        msgs, _ = core.build_initial_messages(mem, with_history=False)
        trace = []
        await core.process_turn(
            messages=msgs, user_text="现在几点", mem=mem,
            tools=[], tool_map={"get_current_time": lambda: "08:00"},
            api_key="fake", on_thinking=trace.append,
        )
        kinds = [e["kind"] for e in trace]
        print("trace:", json.dumps(trace, ensure_ascii=False, indent=1))
        for k in ("memory", "reason", "tool", "tool_result", "generate"):
            assert k in kinds, f"missing kind {k} in {kinds}"
        # 思考轨迹绝不允许混进 messages
        assert all(m.get("role") != "thinking" for m in msgs), "thinking leaked into messages!"
        # 记忆零污染：临时目录只有 autosave 的会话文件，绝无 thinking 相关文件
        files = sorted(str(p.relative_to(td)) for p in pathlib.Path(td).rglob("*") if p.is_file())
        print("temp files:", files)
        assert all("thinking" not in f for f in files), "thinking polluted memory!"
        # 工具确实执行了（tool 消息存在）
        assert any(m.get("role") == "tool" for m in msgs), "tool message missing"
        print("PASS: 阶段0 单元测试通过（轨迹齐全 + 记忆零污染）")

asyncio.run(main())
