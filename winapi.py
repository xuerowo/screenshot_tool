"""
winapi.py
=========
Win32 輔助模組：
1. DPI 感知設定 (Per-Monitor V2) —— 必須在 QApplication 建立前呼叫。
2. 視窗列舉 (EnumWindows) 取得可見視窗矩形，供「貼齊視窗邊緣」使用。
3. 監視器幾何輔助 (虛擬桌面範圍)。

所有座標皆為「物理像素」（在設定 Per-Monitor V2 後與螢幕實際像素 1:1）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
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
