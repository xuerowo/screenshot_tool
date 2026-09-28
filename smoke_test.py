"""
smoke_test.py
=============
功能性冒煙測試（不需彈出 GUI 視窗）：
- 驗證 DPI 感知、視窗列舉、mss 抓取。
- 驗證 CaptureController 連續擷取 3 輪 x 2 個定點（0.2s 間隔）並儲存。
- 驗證 RegionSelector 的貼齊/座標數學邏輯。
- 驗證 Config 儲存/載入往返（含舊版單一 region 設定轉換）。

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
    regions = [Region(0, 0, 100, 100, "A"), Region(50, 50, 40, 30, "B")]

    ctrl = CaptureController()
    frames = []
    paths = []
    loop = QEventLoop()
    ctrl.signals.frame.connect(
        lambda img, idx, ri, p: (frames.append((idx, ri, img.width())), paths.append(p))
    )
    ctrl.signals.state.connect(lambda running: loop.quit() if not running else None)
    # 安全逾時
    QTimer.singleShot(5000, loop.quit)

    assert ctrl.start(regions, cfg), "應能開始擷取"
    loop.exec()
    time.sleep(0.2)  # 給 queued signal 時間
    app.processEvents()

    rounds = {idx for idx, _, _ in frames}
    assert len(rounds) == 3, f"應擷取 3 輪，實際 {sorted(rounds)}"
    assert len(frames) == 6, f"3 輪 x 2 定點應有 6 張，實際 {len(frames)}"
    assert {(ri, w) for _, ri, w in frames} == {(0, 100), (1, 40)}, "各定點尺寸應正確"
    saved = [p for p in paths if p and os.path.exists(p)]
    assert len(saved) == 6, f"應儲存 6 個檔案，實際 {len(saved)}"
    assert sum("_A_" in os.path.basename(p) for p in saved) == 3
    assert sum("_B_" in os.path.basename(p) for p in saved) == 3
    for p in saved:
        assert os.path.getsize(p) > 0
    print(f"[OK] 多定點連續擷取 {len(rounds)} 輪，儲存 {len(saved)} 個檔案")


def test_live_preview() -> None:
    from PyQt6.QtWidgets import QApplication
    from cap_engine import LivePreview
    from config import Region

    app = QApplication.instance() or QApplication(sys.argv)
    lp = LivePreview(interval_ms=50)
    got = []
    lp.frame.connect(lambda img, r: got.append((img.width(), img.height(), r)))
    lp.set_target_size(40, 40)
    lp.set_region(Region(0, 0, 200, 100))
    lp.start()
    end = time.time() + 2
    while len(got) < 3 and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    lp.stop()
    assert len(got) >= 3, f"應持續產生預覽，實際 {len(got)}"
    assert got[0] == (40, 20, (0, 0, 200, 100)), got[0]   # 等比縮到 40x40 內
    print(f"[OK] 即時預覽背景執行緒（{len(got)} 張縮圖）")


def test_filename_template() -> None:
    from datetime import datetime
    from cap_engine import check_filename_template, region_file_path, render_filename
    from config import DEFAULT_FILENAME_TEMPLATE, Config, Region

    when = datetime(2026, 9, 28, 17, 8, 53, 467000)
    kw = dict(prefix="capture", name="P1", number=1, when=when, round_label="0001")
    # 預設範本 = 舊版固定檔名
    assert render_filename(DEFAULT_FILENAME_TEMPLATE, **kw) == "capture_P1_20260928_170853_467_0001"
    # 子資料夾、英文別名、編號
    assert render_filename("{名稱}/{日期}_{no}_{round}", **kw) == os.path.join("P1", "20260928_1_0001")
    # 不合法字元與 .. 會被清掉，不會跳出輸出目錄
    assert render_filename("../a:b/{名稱}?", **dict(kw, name="x*y")) == os.path.join("ab", "xy")
    # 錯誤檢查
    assert check_filename_template("{名稱}_{abc}") and "abc" in check_filename_template("{名稱}_{abc}")
    assert check_filename_template("") is not None
    assert check_filename_template("{名稱") is not None
    assert check_filename_template(DEFAULT_FILENAME_TEMPLATE) is None

    # 實際路徑：建立子資料夾、重複時自動加 _1
    cfg = Config(filename_template="{名稱}/{前綴}")
    cfg.output_dir = tempfile.mkdtemp(prefix="regionshot_name_")
    region = Region(0, 0, 10, 10, "左螢幕")
    p1 = region_file_path(cfg, region, 0, when, "0001")
    assert p1 == os.path.join(cfg.output_dir, "左螢幕", "capture.png"), p1
    open(p1, "wb").close()
    p2 = region_file_path(cfg, region, 0, when, "0002")
    assert p2 == os.path.join(cfg.output_dir, "左螢幕", "capture_1.png"), p2
    # 範本壞掉時退回預設，不會存檔失敗
    cfg.filename_template = "{不存在}"
    p3 = region_file_path(cfg, region, 0, when, "0003")
    assert os.path.basename(p3) == "capture_左螢幕_20260928_170853_467_0003.png", p3
    print("[OK] 檔名範本（預設 / 子資料夾 / 清理 / 防覆蓋）")


def test_folder_seq() -> None:
    from datetime import datetime
    from PyQt6.QtGui import QImage
    from cap_engine import (
        check_filename_template, region_file_path, reset_folder_seq, save_image,
    )
    from config import Config, Region

    reset_folder_seq()
    when = datetime.now()
    cfg = Config(filename_template="{名稱}/{序號}")
    cfg.output_dir = tempfile.mkdtemp(prefix="regionshot_seq_")
    a, b = Region(0, 0, 10, 10, "A"), Region(0, 0, 10, 10, "B")

    # 資料夾已有 12 張照片（+ 一個非圖片檔）→ 接著從 0013 開始
    folder_a = os.path.join(cfg.output_dir, "A")
    os.makedirs(folder_a)
    for i in range(12):
        open(os.path.join(folder_a, f"old_{i}.jpg"), "wb").close()
    open(os.path.join(folder_a, "notes.txt"), "wb").close()

    img = QImage(2, 2, QImage.Format.Format_RGB32)
    img.fill(0)

    def save(region):
        path = region_file_path(cfg, region, 0, when, "0001")
        save_image(cfg, img, path)          # 走真正的存檔流程（會解除序號保留）
        return os.path.relpath(path, cfg.output_dir)

    assert save(a) == os.path.join("A", "0013.png")
    assert save(a) == os.path.join("A", "0014.png")
    # 每個資料夾各自編號
    assert save(b) == os.path.join("B", "0001.png")
    assert save(b) == os.path.join("B", "0002.png")

    # 中間刪過檔：照片數 = 2，但 0003 已存在 → 跳到 0004
    cfg.filename_template = "{序號}"
    cfg.output_dir = tempfile.mkdtemp(prefix="regionshot_seq_gap_")
    for n in (1, 2, 3):
        open(os.path.join(cfg.output_dir, f"{n:04d}.png"), "wb").close()
    os.remove(os.path.join(cfg.output_dir, "0002.png"))
    reset_folder_seq()
    assert save(a) == "0004.png"

    # 執行中刪掉最後幾張（不重設）→ 依資料夾現有照片數接續
    cfg.filename_template = "{序號}"
    cfg.output_dir = tempfile.mkdtemp(prefix="regionshot_seq_del_")
    names = [save(a) for _ in range(5)]
    assert names == ["0001.png", "0002.png", "0003.png", "0004.png", "0005.png"], names
    for n in ("0004.png", "0005.png"):
        os.remove(os.path.join(cfg.output_dir, n))
    assert save(a) == "0004.png"
    assert save(a) == "0005.png"
    # 還沒存檔的號碼不會被重複分配（兩邊同時要號碼）
    p1 = region_file_path(cfg, a, 0, when, "0001")
    p2 = region_file_path(cfg, a, 0, when, "0001")
    assert os.path.basename(p1) == "0006.png" and os.path.basename(p2) == "0007.png", (p1, p2)

    # {序號} 不能放在資料夾名稱
    assert check_filename_template("{序號}/{名稱}") is not None
    assert check_filename_template("{seq}/x") is not None
    assert check_filename_template("{名稱}/{日期}_{序號}") is None
    print("[OK] {序號}：接續資料夾照片數 / 刪檔後接續 / 各資料夾獨立 / 跳過已存在")


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
    import json
    cfg = Config()
    cfg.regions = [Region(10, 20, 300, 200, "左"), Region(400, 0, 50, 60, "右")]
    cfg.interval_seconds = 1.5
    cfg.capture_mode, cfg.page_key, cfg.page_wait = "page", "pagedown", 0.8
    tmp = os.path.join(tempfile.mkdtemp(), "cfg.json")
    cfg.save(tmp)
    loaded = Config.load(tmp)
    assert loaded.regions == cfg.regions, loaded.regions
    assert loaded.interval_seconds == 1.5
    assert (loaded.capture_mode, loaded.page_key, loaded.page_wait) == ("page", "pagedown", 0.8)
    assert winapi.send_key("no-such-key") is False and "left" in winapi.PAGE_KEYS

    # 舊版設定：單一 "region" → 轉成一個名為 P1 的定點
    legacy = os.path.join(tempfile.mkdtemp(), "old.json")
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({"region": {"left": 1, "top": 2, "width": 3, "height": 4}}, f)
    old = Config.load(legacy)
    assert old.regions == [Region(1, 2, 3, 4, "P1")], old.regions
    print("[OK] Config 儲存/載入往返 + 舊版轉換")


if __name__ == "__main__":
    import shutil
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    # 所有測試檔都寫進同一個暫存根目錄（tempfile.mkdtemp 會用它），結束後整個刪掉；
    # 測試只使用自己建立的 Config，不會寫進使用者設定的輸出資料夾或 config.json。
    test_root = tempfile.mkdtemp(prefix="regionshot_smoke_")
    tempfile.tempdir = test_root
    try:
        test_dpi_and_windows()
        test_mss_grab()
        test_capture_controller()
        test_live_preview()
        test_filename_template()
        test_folder_seq()
        test_region_math()
        test_config_roundtrip()
        print("\n全部冒煙測試 PASS")
    finally:
        tempfile.tempdir = None
        shutil.rmtree(test_root, ignore_errors=True)
