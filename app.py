"""
app.py
======
主視窗（控制面板）：
- 區域：縮圖預覽 + L/T/W/H 數值微調 + 1px 微調按鍵 + 選取區域。
- 參數：擷取模式(間隔/FPS)、輸出目錄、格式/品質、檔名前綴、上限。
- 控制：開始 / 暫停 / 停止 / 手動加拍 / 立即單張。
- 即時預覽 + 統計。

所有座標與尺寸皆為「物理像素」。
"""
from __future__ import annotations

import os

import mss
from PyQt6.QtCore import Qt, QTimer, QEventLoop, pyqtSignal
from PyQt6.QtGui import QAction, QImage, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import winapi
from cap_engine import CaptureController
from config import Config, Region
from region import RegionSelector

PREVIEW_SIZE = (220, 150)


class MainWindow(QMainWindow):
    # 全域快捷鍵（由 keyboard 執行緒發出，跨執行緒排程到主執行緒）
    single_req = pyqtSignal()
    manual_req = pyqtSignal()
    select_req = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._region: Region = config.region
        self._controller = CaptureController()
        self._has_keyboard = False
        self._keyboard_hook = None

        self.setWindowTitle("定點連續截圖工具 — 精準對齊版")
        self.resize(760, 560)

        self._build_ui()
        self._connect_signals()

        # 將已儲存的區域同步到數值欄位與預覽
        self._apply_region(self._region)

        # 延遲刷新手動預覽（等待視窗繪製）
        QTimer.singleShot(200, self.refresh_preview)

        self._try_enable_string_keyboard()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI 建立
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        root.addWidget(self._build_region_group(), 1)
        root.addLayout(self._build_right_panel(), 1)

    def _build_region_group(self) -> QGroupBox:
        box = QGroupBox("擷取區域（物理像素 / 精準對齊）")
        lay = QVBoxLayout(box)

        self._preview = QLabel("尚未選取區域")
        self._preview.setFixedSize(*PREVIEW_SIZE)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "border:1px solid #999; background:#111; color:#888;"
        )
        self._preview.setScaledContents(False)
        lay.addWidget(self._preview)

        btn_sel = QPushButton("■　選取區域…")
        btn_sel.setToolTip("開啟全螢幕選取遮罩（放大鏡、網格、貼齊、微調）\n快捷鍵：全域 F7 / 視窗內 Ctrl+R")
        btn_sel.clicked.connect(self._on_select_region)
        lay.addWidget(btn_sel)

        # 數值座標
        grid = QGridLayout()
        self._spin_l = self._make_spin(-100000, 100000)
        self._spin_t = self._make_spin(-100000, 100000)
        self._spin_w = self._make_spin(0, 100000)
        self._spin_h = self._make_spin(0, 100000)
        grid.addWidget(QLabel("Left"), 0, 0)
        grid.addWidget(self._spin_l, 0, 1)
        grid.addWidget(QLabel("Top"), 0, 2)
        grid.addWidget(self._spin_t, 0, 3)
        grid.addWidget(QLabel("寬度"), 1, 0)
        grid.addWidget(self._spin_w, 1, 1)
        grid.addWidget(QLabel("高度"), 1, 2)
        grid.addWidget(self._spin_h, 1, 3)
        lay.addLayout(grid)
        for s in (self._spin_l, self._spin_t, self._spin_w, self._spin_h):
            s.valueChanged.connect(self._on_region_num_changed)

        # 微調按鍵（1px / 10px）
        self._step_combo = QComboBox()
        self._step_combo.addItems(["1px", "5px", "10px"])
        self._step_combo.setCurrentIndex(0)
        nudge_row = QHBoxLayout()
        for text, target in [
            ("◀", "left"), ("▶", "right"), ("▲", "up"), ("▼", "down"),
        ]:
            b = QPushButton(text)
            b.setFixedWidth(40)
            b.clicked.connect(lambda _=False, t=target: self._nudge(t))
            nudge_row.addWidget(b)
        nudge_row.addWidget(QLabel("步進"))
        nudge_row.addWidget(self._step_combo)
        nudge_row.addStretch(1)
        lay.addLayout(nudge_row)

        return box

    def _make_spin(self, lo: int, hi: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(0)
        s.setFixedWidth(80)
        return s

    def _build_right_panel(self) -> QVBoxLayout:
        right = QVBoxLayout()
        right.addWidget(self._build_mode_group())
        right.addWidget(self._build_output_group())
        right.addWidget(self._build_control_group())
        right.addWidget(self._build_status_group())
        hint = QLabel("快捷鍵　全域: F7 選取區域 ・ F8 單張 ・ F9 手動加拍　|　視窗內: Ctrl+R 選取區域 ・ Ctrl+S 單張")
        hint.setStyleSheet("color:#777; font-size:11px;")
        hint.setWordWrap(True)
        right.addWidget(hint)
        right.addStretch(1)
        return right

    def _build_mode_group(self) -> QGroupBox:
        box = QGroupBox("擷取節奏")
        lay = QVBoxLayout(box)

        self._radio_interval = QRadioButton("定時間隔（秒）")
        self._radio_fps = QRadioButton("每秒幀數 (FPS)")
        self._radio_interval.setChecked(
            self._config.capture_mode == "interval"
        )
        self._radio_fps.setChecked(self._config.capture_mode == "fps")
        lay.addWidget(self._radio_interval)
        lay.addWidget(self._radio_fps)

        h = QHBoxLayout()
        h.addWidget(QLabel("間隔"))
        self._spin_interval = QDoubleSpinBox()
        self._spin_interval.setRange(0.05, 3600.0)
        self._spin_interval.setDecimals(2)
        self._spin_interval.setValue(self._config.interval_seconds)
        h.addWidget(self._spin_interval)
        h.addWidget(QLabel("秒 / FPS"))
        self._spin_fps = QSpinBox()
        self._spin_fps.setRange(1, 240)
        self._spin_fps.setValue(self._config.fps)
        h.addWidget(self._spin_fps)
        h.addStretch(1)
        lay.addLayout(h)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("上限"))
        self._spin_max_frames = QSpinBox()
        self._spin_max_frames.setRange(0, 1000000)
        self._spin_max_frames.setValue(self._config.max_frames)
        self._spin_max_frames.setToolTip("0 = 無限")
        h2.addWidget(self._spin_max_frames)
        h2.addWidget(QLabel("張 / "))
        self._spin_max_seconds = QDoubleSpinBox()
        self._spin_max_seconds.setRange(0, 86400)
        self._spin_max_seconds.setValue(self._config.max_seconds)
        self._spin_max_seconds.setToolTip("0 = 無限")
        h2.addWidget(self._spin_max_seconds)
        h2.addWidget(QLabel("秒"))
        h2.addStretch(1)
        lay.addLayout(h2)

        self._radio_interval.toggled.connect(self._on_mode_radio)
        self._on_mode_radio()
        return box

    def _build_output_group(self) -> QGroupBox:
        box = QGroupBox("輸出")
        lay = QVBoxLayout(box)

        h = QHBoxLayout()
        h.addWidget(QLabel("目錄"))
        self._edit_outdir = QLineEdit(self._config.output_dir)
        h.addWidget(self._edit_outdir, 1)
        btn_browse = QPushButton("瀏覽…")
        btn_browse.clicked.connect(self._browse_outdir)
        h.addWidget(btn_browse)
        lay.addLayout(h)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("格式"))
        self._combo_format = QComboBox()
        self._combo_format.addItems(["png", "jpg"])
        self._combo_format.setCurrentText(self._config.file_format)
        h2.addWidget(self._combo_format)
        h2.addWidget(QLabel("品質"))
        self._spin_quality = QSpinBox()
        self._spin_quality.setRange(1, 100)
        self._spin_quality.setValue(self._config.jpeg_quality)
        h2.addWidget(self._spin_quality)
        h2.addWidget(QLabel("前綴"))
        self._edit_prefix = QLineEdit(self._config.file_prefix)
        h2.addWidget(self._edit_prefix, 1)
        lay.addLayout(h2)

        self._check_snap = QCheckBox("選取時貼齊視窗邊緣")
        self._check_snap.setChecked(self._config.snap_enabled)
        lay.addWidget(self._check_snap)
        return box

    def _build_control_group(self) -> QGroupBox:
        box = QGroupBox("控制")
        lay = QHBoxLayout(box)

        self._btn_start = QPushButton("▶ 開始")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause = QPushButton("⏸ 暫停")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_pause.setEnabled(False)
        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        self._btn_manual = QPushButton("＋ 手動加拍")
        self._btn_manual.setToolTip("在連續擷取中立即加拍一張（全域快捷鍵 F9）")
        self._btn_manual.clicked.connect(self._on_manual)
        self._btn_manual.setEnabled(False)
        self._btn_single = QPushButton("📷 立即單張")
        self._btn_single.setToolTip("立即擷取單張並儲存（Ctrl+S；全域快捷鍵 F8）")
        self._btn_single.clicked.connect(self._on_single)

        for b in (self._btn_start, self._btn_pause, self._btn_stop,
                  self._btn_manual, self._btn_single):
            lay.addWidget(b)
        return box

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("狀態 / 即時預覽")
        lay = QVBoxLayout(box)
        self._live = QLabel("尚未開始擷取")
        self._live.setFixedSize(*PREVIEW_SIZE)
        self._live.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live.setStyleSheet(
            "border:1px solid #999; background:#111; color:#888;"
        )
        lay.addWidget(self._live)
        self._lbl_stats = QLabel("共 0 張")
        lay.addWidget(self._lbl_stats)
        return box

    # ------------------------------------------------------------------
    # 訊號
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        c = self._controller.signals
        c.frame.connect(self._on_frame)
        c.stats.connect(self._on_stats)
        c.state.connect(self._on_state)
        self.single_req.connect(self._on_single)
        self.manual_req.connect(self._on_manual)
        self.select_req.connect(self._on_select_region)

    # ------------------------------------------------------------------
    # 區域操作
    # ------------------------------------------------------------------
    def _on_select_region(self) -> None:
        self._update_config_from_ui()
        self.hide()
        QApplication.processEvents()
        sel = RegionSelector(self._config)
        sel.start()
        loop = QEventLoop()
        sel.finished.connect(lambda r: loop.quit())
        sel.destroyed.connect(loop.quit)
        loop.exec()
        region = sel.result()
        sel.deleteLater()
        self.show()
        if region is not None and region.is_valid():
            self._apply_region(region)
            self._config.save()

    def _apply_region(self, region: Region) -> None:
        self._region = region
        self._spin_l.setValue(region.left)
        self._spin_t.setValue(region.top)
        self._spin_w.setValue(region.width)
        self._spin_h.setValue(region.height)
        self.refresh_preview()
        self._controller.set_region(region)

    def _on_region_num_changed(self) -> None:
        self._region = Region(
            self._spin_l.value(),
            self._spin_t.value(),
            self._spin_w.value(),
            self._spin_h.value(),
        )
        self.refresh_preview(debounced=True)

    def _nudge(self, direction: str) -> None:
        step = int(self._step_combo.currentText().rstrip("px"))
        l, t, w, h = self._region.left, self._region.top, self._region.width, self._region.height
        if direction == "left":
            l -= step
        elif direction == "right":
            l += step
        elif direction == "up":
            t -= step
        elif direction == "down":
            t += step
        self._apply_region(Region(l, t, w, h))
        self._config.save()

    def refresh_preview(self, debounced: bool = False) -> None:
        if debounced:
            QTimer.singleShot(150, self._do_refresh_preview)
        else:
            self._do_refresh_preview()

    def _do_refresh_preview(self) -> None:
        region = self._region
        if not region.is_valid():
            self._preview.setText("尚未選取區域")
            return
        try:
            with mss.mss() as sct:
                shot = sct.grab(
                    {"left": region.left, "top": region.top,
                     "width": region.width, "height": region.height}
                )
                raw = bytes(shot.rgb)
            img = QImage(raw, shot.width, shot.height, shot.width * 3,
                         QImage.Format.Format_RGB888).copy()
            pix = self._scale(img, *PREVIEW_SIZE)
            self._preview.setPixmap(pix)
        except Exception as exc:  # noqa: BLE001
            self._preview.setText(f"預覽失敗\n{exc}")

    @staticmethod
    def _scale(img: QImage, w: int, h: int) -> QPixmap:
        scaled = img.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return QPixmap.fromImage(scaled)

    # ------------------------------------------------------------------
    # 擷取控制
    # ------------------------------------------------------------------
    def _update_config_from_ui(self) -> None:
        c = self._config
        c.region = self._region
        c.capture_mode = "interval" if self._radio_interval.isChecked() else "fps"
        c.interval_seconds = self._spin_interval.value()
        c.fps = self._spin_fps.value()
        c.max_frames = self._spin_max_frames.value()
        c.max_seconds = self._spin_max_seconds.value()
        c.output_dir = self._edit_outdir.text().strip() or c.output_dir
        c.file_format = self._combo_format.currentText()
        c.jpeg_quality = self._spin_quality.value()
        c.file_prefix = self._edit_prefix.text().strip() or "capture"
        c.snap_enabled = self._check_snap.isChecked()

    def _on_start(self) -> None:
        self._update_config_from_ui()
        if not self._region.is_valid():
            QMessageBox.warning(self, "提示", "請先選取要擷取的區域。")
            return
        self._config.save()
        self._controller.start(self._region, self._config)
        self._set_running_ui(True)

    def _on_pause(self) -> None:
        if self._controller.running:
            if self._btn_pause.text().startswith("⏸"):
                self._controller.pause()
                self._btn_pause.setText("▶ 恢復")
            else:
                self._controller.resume()
                self._btn_pause.setText("⏸ 暫停")

    def _on_stop(self) -> None:
        self._controller.stop()

    def _on_manual(self) -> None:
        self._controller.capture_now()

    def _on_single(self) -> None:
        self._update_config_from_ui()
        region = self._region
        if not region.is_valid():
            QMessageBox.warning(self, "提示", "請先選取要擷取的區域。")
            return
        try:
            with mss.mss() as sct:
                shot = sct.grab(
                    {"left": region.left, "top": region.top,
                     "width": region.width, "height": region.height}
                )
                raw = bytes(shot.rgb)
            img = QImage(raw, shot.width, shot.height, shot.width * 3,
                         QImage.Format.Format_RGB888).copy()
            from cap_engine import timestamp_ms
            ext = self._config.file_format.replace("jpeg", "jpg")
            outdir = self._config.output_dir
            os.makedirs(outdir, exist_ok=True)
            path = os.path.join(
                outdir,
                f"{self._config.file_prefix}_{timestamp_ms()}_single.{ext}",
            )
            img.save(path, "PNG" if ext == "png" else "JPEG",
                     self._config.jpeg_quality if ext != "png" else -1)
            pix = self._scale(img, *PREVIEW_SIZE)
            self._live.setPixmap(pix)
            self._lbl_stats.setText(f"已儲存單張：{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "錯誤", f"擷取失敗：{exc}")

    # ------------------------------------------------------------------
    # 擷取引擎訊號
    # ------------------------------------------------------------------
    def _on_frame(self, img: QImage, index: int, path: str) -> None:
        pix = self._scale(img, *PREVIEW_SIZE)
        self._live.setPixmap(pix)
        if path:
            self._lbl_stats.setText(f"已儲存：{os.path.basename(path)}")
            # 即時統計由 _on_stats 覆蓋

    def _on_stats(self, stats: dict) -> None:
        if "error" in stats:
            self._lbl_stats.setText(f"錯誤：{stats['error']}")
            return
        frames = stats.get("frames", 0)
        bytesw = stats.get("bytes", 0)
        elapsed = stats.get("elapsed", 0)
        ms = stats.get("capture_ms", 0)
        self._lbl_stats.setText(
            f"已抓 {frames} 張 ・ {self._fmt_bytes(bytesw)} ・ "
            f"耗時 {elapsed:.1f}s ・ 單幀 {ms:.1f}ms"
        )

    def _on_state(self, running: bool) -> None:
        self._set_running_ui(running)

    def _set_running_ui(self, running: bool) -> None:
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(running)
        self._btn_manual.setEnabled(running)
        self._btn_single.setEnabled(True)
        if not running:
            self._btn_pause.setText("⏸ 暫停")

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n / (1024 * 1024):.1f}MB"

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------
    def _browse_outdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "選擇輸出目錄", self._edit_outdir.text())
        if d:
            self._edit_outdir.setText(d)

    def _on_mode_radio(self) -> None:
        if not hasattr(self, "_spin_interval"):
            return
        interval = self._radio_interval.isChecked()
        self._spin_interval.setEnabled(interval)
        self._spin_fps.setEnabled(not interval)

    def _setup_shortcuts(self) -> None:
        """視窗內快捷鍵（視窗有焦點時有效）。"""
        # Ctrl+S → 立即單張
        self._sc_single = QShortcut(QKeySequence("Ctrl+S"), self)
        self._sc_single.activated.connect(self._on_single)
        # Ctrl+R → 選取區域（開遮罩）
        self._sc_select = QShortcut(QKeySequence("Ctrl+R"), self)
        self._sc_select.activated.connect(self._on_select_region)
        # 若全域 keyboard 不可用，於視窗內補 F9 手動加拍
        if not self._has_keyboard:
            self._sc_manual = QShortcut(QKeySequence("F9"), self)
            self._sc_manual.activated.connect(self._on_manual)

    def _try_enable_string_keyboard(self) -> None:
        """啟用全域快速鍵（若 keyboard 可用，且使用者不介意管理員權限）。

        全域動作：
          F7 → 選取區域（開全螢幕遮罩）
          F8 → 立即單張（無論是否在連續擷取中）
          F9 → 手動加拍（連續擷取運行中）
        """
        try:
            import keyboard  # type: ignore

            self._has_keyboard = True
            keyboard.add_hotkey("f7", lambda: self.select_req.emit())
            keyboard.add_hotkey("f8", lambda: self.single_req.emit())
            keyboard.add_hotkey("f9", lambda: self.manual_req.emit())
        except Exception:
            self._has_keyboard = False

    def closeEvent(self, event) -> None:  # noqa: N802
        self._controller.stop()
        if self._has_keyboard:
            try:
                import keyboard  # type: ignore
                keyboard.unhook_all()
            except Exception:
                pass
        self._config.save()
        super().closeEvent(event)
