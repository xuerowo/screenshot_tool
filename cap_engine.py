"""
cap_engine.py
=============
以 mss 為核心的連續擷取引擎。

- 使用獨立的背景執行緒（`threading.Thread`）跑擷取迴圈，避免阻塞主執行緒。
- 透過 Qt 訊號把「已擷取畫面」送回主執行緒更新即時預覽與統計。
- 支援：定時間隔(秒) / 每秒幀數(FPS) 兩種節奏、張數/秒數上限、暫停/恢復/停止、
  手動額外擷取、執行中更新區域、PNG/JPEG 儲存。

跨執行緒安全性：藉由 QObject 訊號（自動使用 queued connection）把資料送回主執行緒。
"""
from __future__ import annotations

import threading
import time
import os
from datetime import datetime
from typing import Optional, Tuple

import mss
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from config import Config, Region

# 擷取節點上層定義
REGION_TUPLE = Tuple[int, int, int, int]  # (left, top, width, height)


def timestamp_ms() -> str:
    """回傳 YYYYmmdd_HHMMSS_mmm（到毫秒）。"""
    now = datetime.now()
    return now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"


class CaptureSignals(QObject):
    """跨執行緒訊號容器（物件存活於主執行緒）。"""
    frame = pyqtSignal(QImage, int, str)   # (QImage, frame_index, saved_path or "")
    state = pyqtSignal(bool)               # True = 執行中
    stats = pyqtSignal(dict)               # {"frames", "bytes", "elapsed"}


class _CaptureWorker(threading.Thread):
    """實際擷取迴圈。存活於獨立執行緒，持有 mss 實例（mss 非執行緒安全，需在該執行緒建立）。"""

    def __init__(
        self,
        signals: CaptureSignals,
        region: Region,
        cfg: Config,
    ):
        super().__init__(daemon=True)
        self.signals = signals
        self._region = region
        self._cfg = cfg
        self._region_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._one_shot_event = threading.Event()

        self._frame_count = 0
        self._bytes_written = 0
        self._start_time = 0.0

    # -- 主執行緒控制介面（執行緒安全） ------------
    def request_stop(self) -> None:
        self._stop_event.set()

    def request_pause(self) -> None:
        self._pause_event.set()

    def request_resume(self) -> None:
        self._pause_event.clear()

    def request_one_shot(self) -> None:
        self._one_shot_event.set()

    def set_region(self, region: Region) -> None:
        with self._region_lock:
            self._region = region

    def get_region(self) -> Region:
        with self._region_lock:
            return self._region

    # -- 執行緒本體 --------------------------------
    def run(self) -> None:  # pragma: no cover - 依賴 GUI/螢幕
        self._start_time = time.monotonic()
        self.signals.state.emit(True)
        try:
            with mss.mss() as sct:
                while not self._stop_event.is_set():
                    # 1) 處理手動加拍（即使暫停也能捕獲）
                    if self._one_shot_event.is_set():
                        self._one_shot_event.clear()
                        self._capture_once(sct)
                        continue

                    # 2) 暫停
                    if self._pause_event.is_set():
                        self._pause_event.wait(0.2)
                        continue

                    # 3) 正常擷取一幀
                    self._capture_once(sct)

                    # 4) 節流：間隔 / FPS
                    if self._reached_limit():
                        break
                    self._sleep_period()
        except Exception as exc:  # noqa: BLE001
            self.signals.stats.emit({"error": str(exc)})
        finally:
            self.signals.state.emit(False)

    def _capture_once(self, sct: mss.mss) -> None:
        region = self.get_region()
        if not region.is_valid():
            return
        left, top, width, height = region.left, region.top, region.width, region.height
        monitor = {"left": left, "top": top, "width": width, "height": height}

        t0 = time.perf_counter()
        shot = sct.grab(monitor)
        raw = shot.rgb
        rgb_bytes = bytes(raw)
        qimg = QImage(
            rgb_bytes,
            shot.width,
            shot.height,
            shot.width * 3,
            QImage.Format.Format_RGB888,
        ).copy()

        path = self._save(region, qimg)

        self._frame_count += 1
        self._bytes_written += len(rgb_bytes)
        elapsed = time.monotonic() - self._start_time
        self.signals.frame.emit(qimg, self._frame_count, path)
        self.signals.stats.emit(
            {
                "frames": self._frame_count,
                "bytes": self._bytes_written,
                "elapsed": elapsed,
                "capture_ms": (time.perf_counter() - t0) * 1000.0,
            }
        )

    def _save(self, region: Region, qimg: QImage) -> str:
        cfg = self._cfg
        outdir = cfg.output_dir
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError:
            outdir = os.getcwd()
        ts = timestamp_ms()
        ext = cfg.file_format.lower()
        if ext not in ("png", "jpg", "jpeg"):
            ext = "png"
        if ext == "jpeg":
            ext = "jpg"
        fname = f"{cfg.file_prefix}_{ts}_{self._frame_count + 1:04d}.{ext}"
        path = os.path.join(outdir, fname)

        format_hint = "PNG" if ext == "png" else "JPEG"
        if ext == "png":
            qimg.save(path, "PNG", -1)
        else:
            qimg.save(path, "JPEG", cfg.jpeg_quality)
        return path

    def _sleep_period(self) -> None:
        cfg = self._cfg
        if cfg.capture_mode == "fps":
            fps = max(1, cfg.fps)
            period = 1.0 / fps
            # 盡量貼近 target rate
            start = time.monotonic()
            while True:
                remaining = period - (time.monotonic() - start)
                if remaining <= 0:
                    break
                if self._stop_event.is_set() or self._pause_event.is_set():
                    break
                time.sleep(min(remaining, 0.05))
        else:
            interval = max(0.0, cfg.interval_seconds)
            if interval > 0:
                # 分段睡眠以快速回應停止/暫停
                end = time.monotonic() + interval
                while time.monotonic() < end:
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        return
                    time.sleep(min(0.05, end - time.monotonic()))

    def _reached_limit(self) -> bool:
        cfg = self._cfg
        if cfg.max_frames and self._frame_count >= cfg.max_frames:
            return True
        if cfg.max_seconds and (time.monotonic() - self._start_time) >= cfg.max_seconds:
            return True
        return False


class CaptureController(QObject):
    """主執行緒側的控制器，封裝 Worker 生命週期。"""

    def __init__(self):
        super().__init__()
        self.signals = CaptureSignals()
        self._worker: Optional[_CaptureWorker] = None

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self, region: Region, cfg: Config) -> bool:
        if self.running:
            return False
        self._worker = _CaptureWorker(self.signals, region, cfg)
        self._worker.start()
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()

    def pause(self) -> None:
        if self._worker is not None:
            self._worker.request_pause()

    def resume(self) -> None:
        if self._worker is not None:
            self._worker.request_resume()

    def capture_now(self) -> None:
        if self._worker is not None:
            self._worker.request_one_shot()

    def set_region(self, region: Region) -> None:
        if self._worker is not None:
            self._worker.set_region(region)
