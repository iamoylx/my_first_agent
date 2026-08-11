# -*- coding: utf-8 -*-
"""免打扰策略：前台有全屏程序（游戏/视频/演示）时不打扰。

实现：ctypes 调 Win32 —— GetForegroundWindow + GetWindowRect 与屏幕尺寸比较。
零第三方依赖（不装 pywin32 也能跑）。
排除：桌面外壳（Progman/WorkerW/任务栏）、小满自己的窗口（标题含"小满"）。
"""
import ctypes
import ctypes.wintypes as wt

_EXCLUDE_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"}


class DoNotDisturbPolicy:
    """免打扰判定。enabled=False 时永远不静默（调试/演示用）。"""

    def __init__(self, quiet_cfg: dict):
        self.fullscreen = bool(quiet_cfg.get("fullscreen", True))

    def is_quiet(self) -> bool:
        if not self.fullscreen:
            return False
        try:
            return _is_fullscreen_foreground()
        except Exception:
            return False


def _is_fullscreen_foreground() -> bool:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    if not user32.IsWindowVisible(hwnd):
        return False

    # 排除桌面/外壳
    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 256)
    if cls_buf.value in _EXCLUDE_CLASSES:
        return False

    # 排除小满自己的窗口（主窗口/桌宠都叫"小满"）
    title_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buf, 512)
    if "小满" in title_buf.value:
        return False

    # 窗口矩形 vs 屏幕
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    sw = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    sh = user32.GetSystemMetrics(1)   # SM_CYSCREEN
    if w <= 0 or h <= 0:
        return False
    # 全屏：覆盖 ≥ 98% 宽 / ≥ 95% 高（留任务栏/边框容差）
    return w >= sw * 0.98 and h >= sh * 0.95
