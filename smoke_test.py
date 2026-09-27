"""
smoke_test.py
=============
功能性冒煙測試（不需彈出 GUI 視窗）：
- 驗證 DPI 感知、視窗列舉、mss 抓取。
- 驗證 CaptureController 連續擷取 3 張（0.2s 間隔）並儲存。
- 驗證 RegionSelector 的貼齊/座標數學邏輯。
- 驗證 Config 儲存/載入往返。

執行：python smoke_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import winapi


def test_dpi_and_windows() -> None:
    assert winapi.set_dpi_awareness(), "DPI 感知應成功"
    bounds = winapi.get_virtual_screen_bounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    assert w > 0 and h > 0, f"虛擬桌面範圍不合理: {bounds}"
    assert len(winapi.enumerate_window_rects()) >= 0
    print(f"[OK] DPI 感知 + 虛擬桌面 {w}x{h}")


def test_mss_grab() -> None:
    import mss
    with mss.mss() as sct:
        m0 = sct.monitors[0]
        shot = sct.grab({"left": m0["left"], "top": m0["top"], "width": 20, "height": 20})
        assert shot.width == 20 and shot.height == 20
        assert len(shot.rgb) == 20 * 20 * 3
    print("[OK] mss 抓取 20x20")


def test_capture_controller() -> None:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QEventLoop, QTimer
    from cap_engine import CaptureController
    from config import Config, Region

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(capture_mode="interval", interval_seconds=0.2, max_frames=3)
    cfg.output_dir = tempfile.mkdtemp(prefix="regionshot_test_")
    region = Region(0, 0, 100, 100)

    ctrl = CaptureController()
    frames = []
    paths = []
    loop = QEventLoop()
    ctrl.signals.frame.connect(lambda img, idx, p: (frames.append(idx), paths.append(p)))
    ctrl.signals.state.connect(lambda running: loop.quit() if not running else None)
    # 安全逾時
    QTimer.singleShot(5000, loop.quit)

    assert ctrl.start(region, cfg), "應能開始擷取"
    loop.exec()
    time.sleep(0.2)  # 給 queued signal 時間
    app.processEvents()

    assert len(frames) >= 3, f"應至少擷取 3 張，實際 {len(frames)}"
    saved = [p for p in paths if p and os.path.exists(p)]
    assert len(saved) >= 3, f"應儲存至少 3 個檔案，實際 {len(saved)}"
    for p in saved:
        assert os.path.getsize(p) > 0
    print(f"[OK] 連續擷取 {len(frames)} 張，儲存 {len(saved)} 個檔案")


def test_region_math() -> None:
    from region import RegionSelector
    # 貼齊：邊 100 接近 97→吸附到 97，超過距離不吸附
    assert RegionSelector._snap_edge(100, [97], 8) == 97
    assert RegionSelector._snap_edge(100, [50], 8) == 100
    assert RegionSelector._snap_edge(100, [92, 108], 8) == 92  # 距離內最近者
    assert RegionSelector._snap_edge(100, [90, 110], 8) == 100  # 皆超出距離
    print("[OK] 貼齊邊緣邏輯")


def test_config_roundtrip() -> None:
    from config import Config, Region
    cfg = Config()
    cfg.region = Region(10, 20, 300, 200)
    cfg.interval_seconds = 1.5
    tmp = os.path.join(tempfile.mkdtemp(), "cfg.json")
    cfg.save(tmp)
    loaded = Config.load(tmp)
    assert loaded.region.left == 10 and loaded.region.top == 20
    assert loaded.region.width == 300 and loaded.region.height == 200
    assert loaded.interval_seconds == 1.5
    print("[OK] Config 儲存/載入往返")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_dpi_and_windows()
    test_mss_grab()
    test_capture_controller()
    test_region_math()
    test_config_roundtrip()
    print("\n全部冒煙測試 PASS")
