"""
cap_engine.py
=============
以 mss 為核心的連續擷取引擎。

- 使用獨立的背景執行緒（`threading.Thread`）跑擷取迴圈，避免阻塞主執行緒。
- 透過 Qt 訊號把「已擷取畫面」送回主執行緒更新即時預覽與統計。
- 支援：多個定點區域（每一輪依序抓取全部區域）、定時間隔(秒) / 每秒幀數(FPS) /
  翻頁連拍（先拍當前頁，再按翻頁鍵、等待後拍下一頁）三種節奏、輪數/秒數上限、暫停/恢復/停止、手動額外擷取、執行中更新區域、PNG/JPEG 儲存。

跨執行緒安全性：藉由 QObject 訊號（自動使用 queued connection）把資料送回主執行緒。
"""
from __future__ import annotations

import re
import threading
import time
import os
from datetime import datetime
from typing import List, Optional, Tuple

import mss
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QImage

import winapi
from config import DEFAULT_FILENAME_TEMPLATE, Config, Region, default_region_name

# 擷取節點上層定義
REGION_TUPLE = Tuple[int, int, int, int]  # (left, top, width, height)


def grab_region(sct: mss.mss, region: Region) -> QImage:
    """以 mss 抓取單一區域並轉成（獨立記憶體的）QImage。"""
    shot = sct.grab(
        {"left": region.left, "top": region.top,
         "width": region.width, "height": region.height}
    )
    return QImage(
        bytes(shot.rgb),
        shot.width,
        shot.height,
        shot.width * 3,
        QImage.Format.Format_RGB888,
    ).copy()


# 檔名範本代號：代號 → (英文別名, 說明)
FILENAME_TOKENS = {
    "前綴": ("prefix", "檔名前綴（輸出設定中的「前綴」）"),
    "名稱": ("name", "定點名稱，例如 P1"),
    "編號": ("no", "定點在清單中的編號 1、2、3…"),
    "日期": ("date", "日期 YYYYMMDD"),
    "時間": ("time", "時間 HHMMSS"),
    "毫秒": ("ms", "毫秒 000–999"),
    "輪次": ("round", "連續擷取的輪次 0001…；立即單張為 single"),
    "序號": ("seq", "該資料夾中的第幾張照片 0001…（每次依資料夾內現有照片數接續）"),
}
_TOKEN_ALIASES = {alias: key for key, (alias, _) in FILENAME_TOKENS.items()}
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")

# {序號} 先以佔位字元代換，等確定資料夾後再換成實際號碼
FILENAME_SEQ_MARK = "\ue000"
_IMAGE_EXTS = (".png", ".jpg", ".jpeg")
_seq_lock = threading.Lock()
# 已分配但可能還沒存檔的路徑：避免擷取執行緒與「立即單張」同時拿到同一個號碼
_seq_reserved: set = set()


def _count_images(folder: str) -> int:
    try:
        with os.scandir(folder) as it:
            return sum(
                1 for e in it
                if e.is_file() and os.path.splitext(e.name)[1].lower() in _IMAGE_EXTS
            )
    except OSError:
        return 0


def _seq_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _seq_path(pattern: str, n: int, ext: str) -> str:
    return pattern.replace(FILENAME_SEQ_MARK, f"{n:04d}") + f".{ext}"


def _first_free_seq(pattern: str, ext: str) -> int:
    """從「資料夾目前的照片數 + 1」開始，跳過已存在或已分配的號碼。

    每次都重新數資料夾，所以刪掉照片後號碼會跟著接續。呼叫端需持有 _seq_lock。
    """
    n = _count_images(os.path.dirname(pattern)) + 1
    while (os.path.exists(_seq_path(pattern, n, ext))
           or _seq_key(_seq_path(pattern, n, ext)) in _seq_reserved):
        n += 1
    return n


def reserve_seq_path(pattern: str, ext: str) -> str:
    """把 pattern（含 FILENAME_SEQ_MARK、不含副檔名的完整路徑）換成下一個可用序號並保留。"""
    with _seq_lock:
        path = _seq_path(pattern, _first_free_seq(pattern, ext), ext)
        _seq_reserved.add(_seq_key(path))
        return path


def release_seq_path(path: str) -> None:
    """存檔完成（或失敗）後解除保留；之後若使用者刪掉這張，號碼就能再被使用。"""
    with _seq_lock:
        _seq_reserved.discard(_seq_key(path))


def peek_folder_seq(pattern: str, ext: str) -> int:
    """預覽下一個序號（不保留，供介面顯示範例）。"""
    with _seq_lock:
        return _first_free_seq(pattern, ext)


def reset_folder_seq() -> None:
    with _seq_lock:
        _seq_reserved.clear()


def check_filename_template(template: str) -> Optional[str]:
    """檢查檔名範本；合法回傳 None，否則回傳錯誤說明。"""
    if not template.strip():
        return "檔名格式不可為空"
    unknown = [
        t for t in _TOKEN_RE.findall(template)
        if t not in FILENAME_TOKENS and t not in _TOKEN_ALIASES
    ]
    if unknown:
        return "不認得的代號：" + "、".join("{" + t + "}" for t in unknown)
    if "{" in _TOKEN_RE.sub("", template) or "}" in _TOKEN_RE.sub("", template):
        return "大括號沒有成對"
    folders = re.split(r"[\\/]", template)[:-1]
    if any(t in ("序號", "seq") for part in folders for t in _TOKEN_RE.findall(part)):
        return "{序號} 只能用在檔名，不能用在資料夾名稱"
    return None


def render_filename(template: str, *, prefix: str, name: str, number: int,
                    when: datetime, round_label: str, seq: str = FILENAME_SEQ_MARK) -> str:
    """把範本代換成相對路徑（不含副檔名）；「/」或「\\」分隔子資料夾。

    範本不合法時丟出 ValueError。每一層名稱都會移除 Windows 不允許的字元。
    {序號} 預設代換成 FILENAME_SEQ_MARK，由 region_file_path 依資料夾決定實際號碼。
    """
    error = check_filename_template(template)
    if error:
        raise ValueError(error)
    values = {
        "前綴": prefix,
        "名稱": name,
        "編號": str(number),
        "日期": when.strftime("%Y%m%d"),
        "時間": when.strftime("%H%M%S"),
        "毫秒": f"{when.microsecond // 1000:03d}",
        "輪次": round_label,
        "序號": seq,
    }
    text = _TOKEN_RE.sub(lambda m: values[_TOKEN_ALIASES.get(m.group(1), m.group(1))], template)
    parts = [_safe_name(part).rstrip(". ") for part in re.split(r"[\\/]", text)]
    parts = [part for part in parts if part and part not in (".", "..")]
    if not parts:
        raise ValueError("檔名格式代換後是空的")
    return os.path.join(*parts)


def file_extension(cfg: Config) -> str:
    ext = cfg.file_format.lower()
    if ext == "jpeg":
        ext = "jpg"
    return ext if ext in ("png", "jpg") else "png"


def region_file_path(cfg: Config, region: Region, index: int, when: datetime,
                     round_label: str) -> str:
    """依檔名範本組出輸出檔路徑（會建立子資料夾；檔名重複時自動加 _1、_2…）。"""
    fields = dict(
        prefix=cfg.file_prefix,
        name=_safe_name(region.name) or default_region_name(index),
        number=index + 1,
        when=when,
        round_label=round_label,
    )
    try:
        rel = render_filename(cfg.filename_template, **fields)
    except ValueError:
        rel = render_filename(DEFAULT_FILENAME_TEMPLATE, **fields)
    ext = file_extension(cfg)
    path = os.path.join(cfg.output_dir, rel)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        path = os.path.join(os.getcwd(), os.path.basename(rel))
    if FILENAME_SEQ_MARK in path:
        # {序號}：該資料夾目前的照片數 + 1；號碼被佔用（例如中間刪過檔）就往後找
        return reserve_seq_path(path, ext)
    candidate, n = f"{path}.{ext}", 1
    while os.path.exists(candidate):   # 範本沒有時間 / 輪次等代號時避免覆蓋
        candidate = f"{path}_{n}.{ext}"
        n += 1
    return candidate


def save_image(cfg: Config, qimg: QImage, path: str) -> None:
    try:
        if path.lower().endswith(".png"):
            qimg.save(path, "PNG", -1)
        else:
            qimg.save(path, "JPEG", cfg.jpeg_quality)
    finally:
        release_seq_path(path)


def _safe_name(name: str) -> str:
    """去除 Windows 檔名不允許的字元。"""
    return "".join(ch for ch in name.strip() if ch not in '\\/:*?"<>|')


class CaptureSignals(QObject):
    """跨執行緒訊號容器（物件存活於主執行緒）。"""
    frame = pyqtSignal(QImage, int, int, str)  # (QImage, 輪次, 區域索引, saved_path or "")
    state = pyqtSignal(bool)                   # True = 執行中
    stats = pyqtSignal(dict)                   # {"frames", "images", "bytes", "elapsed"}


class _CaptureWorker(threading.Thread):
    """實際擷取迴圈。存活於獨立執行緒，持有 mss 實例（mss 非執行緒安全，需在該執行緒建立）。"""

    _FOCUS_ERROR = (
        "翻頁鍵沒有送出：最前面的是本程式。請先點一下要翻頁的視窗"
        "（或用 Ctrl+Alt+S / 迷你模式開始，不會搶焦點）"
    )

    def __init__(
        self,
        signals: CaptureSignals,
        regions: List[Region],
        cfg: Config,
    ):
        super().__init__(daemon=True)
        self.signals = signals
        self._regions = list(regions)
        self._cfg = cfg
        self._region_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._one_shot_event = threading.Event()

        self._frame_count = 0      # 輪次（每輪抓取所有區域）
        self._image_count = 0      # 實際輸出的圖片數
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

    def set_regions(self, regions: List[Region]) -> None:
        with self._region_lock:
            self._regions = list(regions)

    def get_regions(self) -> List[Region]:
        with self._region_lock:
            return list(self._regions)

    # -- 執行緒本體 --------------------------------
    def run(self) -> None:  # pragma: no cover - 依賴 GUI/螢幕
        self._start_time = time.monotonic()
        self.signals.state.emit(True)
        try:
            page_mode = self._cfg.capture_mode == "page"
            if page_mode and not self._countdown():
                return
            if page_mode and self._cfg.max_frames != 1 and winapi.foreground_is_own_process():
                # 之後的翻頁鍵會送不出去：先停下，不留下只拍一頁的半套結果
                self.signals.stats.emit({"error": self._FOCUS_ERROR})
                return
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

                    # 3) 擷取一輪（翻頁模式的第一輪就是開始時的當前頁）
                    self._capture_once(sct)

                    # 4) 到上限就停（最後一輪拍完不再翻頁）
                    if self._reached_limit():
                        break

                    # 5) 翻頁模式：按翻頁鍵、等畫面換好；其他模式：間隔 / FPS 節流
                    if page_mode:
                        if not self._turn_page():
                            break
                    else:
                        self._sleep_period()
        except Exception as exc:  # noqa: BLE001
            self.signals.stats.emit({"error": str(exc)})
        finally:
            self.signals.state.emit(False)

    def _capture_once(self, sct: mss.mss) -> None:
        """抓取一輪：所有有效區域各一張，共用同一個時間戳與輪次編號。"""
        regions = [(i, r) for i, r in enumerate(self.get_regions()) if r.is_valid()]
        if not regions:
            return

        t0 = time.perf_counter()
        # 先全部抓完再存檔，讓各定點的畫面時間盡量一致
        shots = [(i, r, grab_region(sct, r)) for i, r in regions]
        capture_ms = (time.perf_counter() - t0) * 1000.0

        self._frame_count += 1
        when = datetime.now()
        for i, region, qimg in shots:
            path = region_file_path(self._cfg, region, i, when, f"{self._frame_count:04d}")
            save_image(self._cfg, qimg, path)
            self._image_count += 1
            self._bytes_written += qimg.sizeInBytes()
            self.signals.frame.emit(qimg, self._frame_count, i, path)

        self.signals.stats.emit(
            {
                "frames": self._frame_count,
                "images": self._image_count,
                "regions": len(shots),
                "bytes": self._bytes_written,
                "elapsed": time.monotonic() - self._start_time,
                "capture_ms": capture_ms,
            }
        )

    def _countdown(self) -> bool:
        """翻頁模式開始前倒數，讓使用者點回要翻頁的視窗；中途按停止回傳 False。

        開始時最前面已經不是本程式（用快捷鍵或迷你列開始）就不必倒數。
        """
        if not winapi.foreground_is_own_process():
            return True
        remaining = max(0.0, self._cfg.page_countdown)
        while remaining > 0:
            self.signals.stats.emit({"countdown": remaining})
            step = min(1.0, remaining)
            if self._stop_event.wait(step):
                return False
            remaining -= step
        return True

    def _turn_page(self) -> bool:
        """按一次翻頁鍵並等待設定的秒數；無法送鍵或被停止時回傳 False。"""
        if winapi.foreground_is_own_process():
            self.signals.stats.emit({"error": self._FOCUS_ERROR})
            return False
        # 用 Ctrl+Alt+S 開始時手還按著修飾鍵：先等放開，否則閱讀器會收到 Ctrl+Alt+← 之類的組合鍵
        deadline = time.monotonic() + 5.0
        while winapi.modifiers_down():
            if self._stop_event.wait(0.05):
                return False
            if time.monotonic() > deadline:
                self.signals.stats.emit({
                    "error": "Ctrl / Alt / Shift / Win 一直被按著，翻頁鍵沒有送出"
                })
                return False
        if not winapi.send_key(self._cfg.page_key):
            self.signals.stats.emit({"error": f"無法送出翻頁鍵：{self._cfg.page_key}"})
            return False
        return self._wait(self._cfg.page_wait)

    def _wait(self, seconds: float) -> bool:
        """等待指定秒數；暫停期間不計時，按停止回傳 False。"""
        remaining = max(0.0, seconds)
        last = time.monotonic()
        while remaining > 0:
            if self._stop_event.is_set():
                return False
            time.sleep(min(0.05, remaining))
            now = time.monotonic()
            if not self._pause_event.is_set():
                remaining -= now - last
            last = now
        return not self._stop_event.is_set()

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


class LivePreview(QObject):
    """背景執行緒持續抓取單一區域並縮成預覽圖（主視窗左側即時預覽用）。

    大區域的 mss 抓取每次可能要數十毫秒，放在背景執行緒才不會讓介面卡頓。
    縮圖在背景完成，主執行緒只負責顯示。
    """
    frame = pyqtSignal(QImage, tuple)   # (縮圖, 對應區域 (left, top, width, height))
    error = pyqtSignal(str)

    def __init__(self, interval_ms: int = 200):
        super().__init__()
        self._interval = interval_ms / 1000.0
        self._lock = threading.Lock()
        self._region: Optional[REGION_TUPLE] = None
        self._size: Tuple[int, int] = (300, 180)   # 縮圖目標尺寸（物理像素）
        self._active = True
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def set_region(self, region: Optional[Region]) -> None:
        with self._lock:
            self._region = tuple(region) if region is not None and region.is_valid() else None
        self._wake.set()

    def set_target_size(self, width: int, height: int) -> None:
        with self._lock:
            self._size = (max(1, width), max(1, height))
        self._wake.set()

    def set_active(self, active: bool) -> None:
        """視窗隱藏 / 最小化時暫停抓取。"""
        with self._lock:
            self._active = active
        self._wake.set()

    def _run(self) -> None:  # pragma: no cover - 依賴螢幕
        with mss.mss() as sct:
            while not self._stop.is_set():
                with self._lock:
                    region, size, active = self._region, self._size, self._active
                timeout: Optional[float] = None       # 沒事做就等到被喚醒
                if active and region is not None:
                    t0 = time.monotonic()
                    try:
                        left, top, width, height = region
                        shot = sct.grab({"left": left, "top": top,
                                         "width": width, "height": height})
                        # BGRA 直接對應 Format_RGB32，免逐像素轉換
                        img = QImage(shot.raw, shot.width, shot.height, shot.width * 4,
                                     QImage.Format.Format_RGB32)
                        thumb = img.scaled(
                            size[0], size[1],
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        if thumb.size() == img.size():
                            thumb = img.copy()   # 尺寸相同時 scaled() 可能共用 shot 的緩衝區
                        self.frame.emit(thumb, region)
                    except RuntimeError:
                        return   # 程式結束時 Qt 物件已被刪除：直接結束執行緒
                    except Exception as exc:  # noqa: BLE001
                        try:
                            self.error.emit(str(exc))
                        except RuntimeError:
                            return
                    timeout = max(0.0, self._interval - (time.monotonic() - t0))
                self._wake.wait(timeout)
                self._wake.clear()


class CaptureController(QObject):
    """主執行緒側的控制器，封裝 Worker 生命週期。"""

    def __init__(self):
        super().__init__()
        self.signals = CaptureSignals()
        self._worker: Optional[_CaptureWorker] = None

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self, regions: List[Region], cfg: Config) -> bool:
        if self.running:
            return False
        self._worker = _CaptureWorker(self.signals, regions, cfg)
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

    def set_regions(self, regions: List[Region]) -> None:
        if self._worker is not None:
            self._worker.set_regions(regions)
