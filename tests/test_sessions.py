# tests/test_sessions.py
# 不依赖网络的单元测试：专门验证"会话持久化"的存/取往返是否正确。
# 用法：python tests/test_sessions.py
import os
import sys
import json
import tempfile

# 让脚本能 import 项目根目录的 memory 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import sessions as S


def make_fake_convo():
    """构造一段像真的对话的消息列表（含 system / user / assistant / tool 链）。"""
    return [
        {"role": "system", "content": "你是助手。"},
        {"role": "user", "content": "我叫小明，在学 agent。"},
        {"role": "assistant", "content": "好的小明，有什么我可以帮你的？"},
        {"role": "user", "content": "现在几点？"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_current_time", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "2026-08-08 12:00:00"},
        {"role": "assistant", "content": "现在是 2026-08-08 12:00:00。"},
    ]


def test_roundtrip(tmp_path):
    """核心测试：save 之后 load 回来，内容应当一致（system 也保留）。"""
    # 用一个临时目录替换模块里的路径，避免污染真实项目
    S.SESSIONS_DIR = str(tmp_path)
    S.CURRENT_FILE = os.path.join(str(tmp_path), "current.json")

    convo = make_fake_convo()
    S.save_session(convo)

    # 1) 文件确实落盘了
    assert os.path.exists(S.CURRENT_FILE), "❌ current.json 未生成"
    print("[OK] 会话文件已落盘:", S.CURRENT_FILE)

    # 2) load 回来内容和保存时一致
    loaded = S.load_last_session()
    assert loaded == convo, "❌ load 回来的内容和保存不一致"
    print("[OK] 存→取往返一致，消息数 =", len(loaded))

    # 3) 模拟 main() 的"去掉旧 system 后拼接"逻辑，user/assistant 应当保留
    body = [m for m in loaded if m["role"] != "system"]
    assert any(m["role"] == "user" and "小明" in m["content"] for m in body), \
        "❌ 重启后丢失了用户说过的话"
    print("[OK] 重启拼接后仍能看到用户原话: 小明 / 学 agent")


def test_load_when_empty(tmp_path):
    """没有历史文件时，load 应安全返回 []，不报错。"""
    S.SESSIONS_DIR = str(tmp_path)
    S.CURRENT_FILE = os.path.join(str(tmp_path), "current.json")
    assert S.load_last_session() == [], "❌ 首次运行应返回空列表"
    print("[OK] 首次运行 load 安全返回空列表")


def test_sanitize_drops_dangling_tool_call():
    """落盘前若末尾有'调了工具但没结果'的 assistant，应被剔除，否则重载会 400。"""
    bad = [
        {"role": "user", "content": "几点？"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "get_current_time", "arguments": "{}"}}
        ]},  # 这条没有对应的 tool 返回 → 悬空，必须删
    ]
    clean = S.sanitize(bad)
    assert len(clean) == 1 and clean[0]["role"] == "user", "❌ 悬空 tool_calls 未被清理"
    print("[OK] sanitize 正确剔除末尾悬空 tool_calls")


if __name__ == "__main__":
    # 用系统临时目录做隔离测试，绝不碰你项目的 memory/sessions
    d = tempfile.mkdtemp(prefix="sestest_")
    test_roundtrip(__import__("pathlib").Path(d))
    test_load_when_empty(__import__("pathlib").Path(d + "_2"))
    test_sanitize_drops_dangling_tool_call()
    print("\n✅ 全部测试通过：持久化层本身工作正常。")
