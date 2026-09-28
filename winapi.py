"""
winapi.py
=========
Win32 輔助模組：
1. DPI 感知設定 (Per-Monitor V2) —— 必須在 QApplication 建立前呼叫。
2. 視窗列舉 (EnumWindows) 取得可見視窗矩形，供「貼齊視窗邊緣」使用。
3. 監視器幾何輔助 (虛擬桌面範圍)。
4. 深色標題列、不被截圖、模擬按鍵（翻頁連拍）。

所有座標皆為「物理像素」（在設定 Per-Monitor V2 後與螢幕實際像素 1:1）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# DPI 感知
# ---------------------------------------------------------------------------
DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_DPI_SET = False


def set_dpi_awareness() -> bool:
    """將進程設為 Per-Monitor V2 感知，讓 Qt 與 mss 都使用物理像素。

    應在建立 QApplication 之前呼叫。回傳是否成功。
    """
    global _DPI_SET
    if _DPI_SET:
        return True
    try:
        # Win 10 1703+：一次設到最高的 Per-Monitor V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            _DPI_SET = True
            return True
    except Exception:
        pass
    try:
        # 較舊：shcore.SetProcessDpiAwareness(2) = Per-Monitor
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            _DPI_SET = True
            return True
    except Exception:
        pass
    try:
        # 最舊：user32.SetProcessDPIAware()
        if ctypes.windll.user32.SetProcessDPIAware():
            _DPI_SET = True
            return True
    except Exception:
        pass
    return _DPI_SET


def is_dpi_aware() -> bool:
    try:
        # GetProcessDpiAwareness 需要 process handle；用 GetCurrentProcess()。
        h = ctypes.windll.kernel32.GetCurrentProcess()
        return bool(ctypes.windll.user32.GetProcessDpiAwareness(h) >= 2)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 視窗列舉 (用於貼齊)
# ---------------------------------------------------------------------------
# DwmGetWindowAttribute 用於取得擴展外框 (extended frame)，含陰影/邊框。
DWMWA_EXTENDED_FRAME_BOUNDS = 9

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _get_extended_window_rect(hwnd) -> Optional[Tuple[int, int, int, int]]:
    rect = RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    ) == 0:
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def _get_window_rect(hwnd) -> Optional[Tuple[int, int, int, int]]:
    rect = RECT()
    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def _is_window_visible(hwnd) -> bool:
    return bool(ctypes.windll.user32.IsWindowVisible(hwnd))


def _is_minimized(hwnd) -> bool:
    return bool(ctypes.windll.user32.IsIconic(hwnd))


def _is_tool_window(hwnd) -> bool:
    return bool(ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)


def enumerate_window_rects(extended: bool = True) -> List[Tuple[int, int, int, int]]:
    """回傳所有可見、未最小化、非工具視窗的矩形 [(left, top, right, bottom), ...]。

    extended=True 時優先使用 DwmGetWindowAttribute 的擴展外框（較貼合實際輪廓）。
    座標為物理像素、絕對螢幕座標。此為全快照，供貼齊演算法使用。
    """
    results: List[Tuple[int, int, int, int]] = []

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HWND, wt.LPARAM
    )

    def callback(hwnd, lparam):
        if not _is_window_visible(hwnd) or _is_minimized(hwnd):
            return True
        if _is_tool_window(hwnd):
            return True
        rect = _get_extended_window_rect(hwnd) if extended else _get_window_rect(hwnd)
        if rect is None:
            rect = _get_window_rect(hwnd)
        if rect is None:
            return True
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            return True
        results.append((left, top, right, bottom))
        return True

    # EnumWindows 的 callback 需保持參考，避免被 GC。
    cb_ref = enum_proc(callback)
    try:
        if user32.EnumWindows(cb_ref, 0):
            pass
    finally:
        # 釋放參考
        del cb_ref
    return results


# ---------------------------------------------------------------------------
# 監視器 / 虛擬桌面幾何
# ---------------------------------------------------------------------------
def get_virtual_screen_bounds() -> Tuple[int, int, int, int]:
    """回傳整片虛擬桌面的 (left, top, right, bottom)，物理像素。"""
    left = ctypes.c_long()
    top = ctypes.c_long()
    right = ctypes.c_long()
    bottom = ctypes.c_long()
    ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    left.value = ctypes.windll.user32.GetSystemMetrics(76)
    top.value = ctypes.windll.user32.GetSystemMetrics(77)
    right.value = left.value + ctypes.windll.user32.GetSystemMetrics(78)
    bottom.value = top.value + ctypes.windll.user32.GetSystemMetrics(79)
    return (left.value, top.value, right.value, bottom.value)


# ---------------------------------------------------------------------------
# 深色標題列（Windows 10 20H1+ / Windows 11）
# ---------------------------------------------------------------------------
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def set_dark_title_bar(hwnd: int, dark: bool = True) -> bool:
    """讓視窗標題列使用深色模式，與深色主題一致。不支援的系統上靜默失敗。"""
    try:
        value = ctypes.c_int(1 if dark else 0)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wt.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        return res == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 不被截圖（Windows 10 2004+）
# ---------------------------------------------------------------------------
WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(hwnd: int) -> bool:
    """讓視窗不出現在任何螢幕擷取中（mss / BitBlt 會直接看到視窗後方的畫面）。

    用於浮動在畫面上的迷你工具列，避免它被拍進截圖。不支援的系統上回傳 False。
    """
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            wt.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE
        ))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 模擬按鍵（翻頁連拍）
# ---------------------------------------------------------------------------
# 可用的翻頁鍵：名稱 → 虛擬鍵碼
PAGE_KEYS = {
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pageup": 0x21, "pagedown": 0x22, "space": 0x20, "enter": 0x0D,
}
# 方向鍵 / PageUp / PageDown 屬於延伸鍵，需加旗標才不會被當成數字鍵盤的鍵
_EXTENDED_KEYS = {"left", "up", "right", "down", "pageup", "pagedown"}
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def send_key(name: str) -> bool:
    """對目前最前面的視窗送出一次按鍵（按下 + 放開）。未知鍵名回傳 False。"""
    vk = PAGE_KEYS.get(name)
    if vk is None:
        return False
    try:
        user32 = ctypes.windll.user32
        scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
        flags = KEYEVENTF_EXTENDEDKEY if name in _EXTENDED_KEYS else 0
        user32.keybd_event(vk, scan, flags, 0)
        user32.keybd_event(vk, scan, flags | KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def foreground_is_own_process() -> bool:
    """最前面的視窗是否屬於本程式（是的話，送出的按鍵會被自己吃掉）。"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)   # Shift, Ctrl, Alt, 左 Win, 右 Win


def modifiers_down() -> bool:
    """目前是否有 Shift / Ctrl / Alt / Win 被按著（實體按鍵狀態）。"""
    try:
        user32 = ctypes.windll.user32
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS)
    except Exception:
        return False
