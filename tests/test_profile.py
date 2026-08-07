# tests/test_profile.py
# 不依赖网络的单元测试：验证档案卡的状态复写（latest-wins）与渲染/存取。
# 抽取函数 extract_facts 的网络部分用"连接必失败"的地址验证降级返回 []。
# 用法：python tests/test_profile.py
import os
import sys
import asyncio
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import profile as P


def test_latest_wins():
    """核心：新值覆盖旧值（仅当置信度更高），低置信度不得覆盖已存事实。"""
    p = {"version": 1, "facts": {}}
    # 写入全新事实
    p, c = P.merge_facts(p, [{"key": "name", "value": "小明", "confidence": 0.9}])
    assert p["facts"]["name"]["value"] == "小明"
    assert c == 1
    # 更高置信度 → 覆盖
    p, c = P.merge_facts(p, [{"key": "name", "value": "明仔", "confidence": 1.0}])
    assert p["facts"]["name"]["value"] == "明仔", "高置信度应覆盖"
    # 更低置信度 → 不覆盖（防低置信度污染），且本次 changed 计数为 0
    p, c = P.merge_facts(p, [{"key": "name", "value": "错误名", "confidence": 0.3}])
    assert p["facts"]["name"]["value"] == "明仔", "低置信度不得覆盖"
    assert c == 0, "低置信度覆盖不应计数（changed 应为 0）"
    print("[OK] latest-wins：高可信覆盖、低可信保留，均正确")


def test_save_load_roundtrip(tmp_path):
    """存取往返 + 原子写盘容错。"""
    P.PROFILE_FILE = str(tmp_path / "profile.json")
    p = {"version": 1, "facts": {"city": {"value": "上海", "confidence": 0.9, "updated_at": "x"}}}
    P.save_profile(p)
    assert os.path.exists(P.PROFILE_FILE)
    back = P.load_profile()
    assert back["facts"]["city"]["value"] == "上海"
    print("[OK] 档案卡存→取往返一致")
    # 损坏文件也能安全降级
    with open(P.PROFILE_FILE, "w", encoding="utf-8") as f:
        f.write("{坏json")
    assert P.load_profile()["facts"] == {}, "损坏文件应安全返回空"
    print("[OK] 损坏 profile.json 安全降级为空白档案")


def test_to_context_text():
    p = {"version": 1, "facts": {
        "name": {"value": "小明"}, "city": {"value": "上海"},
        "goal": {"value": "学 agent 开发"},
    }}
    txt = P.to_context_text(p)
    assert "小明" in txt and "上海" in txt and "学 agent 开发" in txt
    # 超长截断
    big = {"version": 1, "facts": {f"k{i}": {"value": "x" * 200} for i in range(20)}}
    assert len(P.to_context_text(big, max_chars=600)) <= 600 + len("\n- ...(更多略)")
    print("[OK] 档案渲染紧凑且超长截断保预算")


async def test_extract_degrades_on_network_error():
    """抽取函数遇到网络错误应降级返回 []，不抛异常、不阻塞退出。"""
    fake_msgs = [{"role": "user", "content": "我叫小明，住上海"}]
    # 127.0.0.1:9 是必失败端口 → 触发 except → 返回 []
    res = await P.extract_facts(fake_msgs, "fake-key",
                                "http://127.0.0.1:9/v1/chat/completions",
                                "deepseek-chat")
    assert res == [], "网络失败时抽取应降级为 []"
    print("[OK] extract_facts 网络失败安全降级为 []")


if __name__ == "__main__":
    test_latest_wins()
    d = tempfile.mkdtemp()
    test_save_load_roundtrip(__import__("pathlib").Path(d))
    test_to_context_text()
    asyncio.run(test_extract_degrades_on_network_error())
    print("\n✅ 档案卡单元测试全部通过。")
