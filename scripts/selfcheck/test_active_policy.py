import sys
sys.path.insert(0, r"D:\document\Myprojects\学习\AGENT")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from active.policy import DoNotDisturbPolicy, _is_fullscreen_foreground

fs = _is_fullscreen_foreground()
print("当前前台是否全屏:", fs)
assert fs is False, "测试环境前台不应是全屏"
p = DoNotDisturbPolicy({"fullscreen": True})
assert p.is_quiet() is False, "当前不应静默"
p2 = DoNotDisturbPolicy({"fullscreen": False})
assert p2.is_quiet() is False, "禁用时永不静默"
print("PASS: 全屏免打扰策略（当前环境非全屏 → 不静默）")
