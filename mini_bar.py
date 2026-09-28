"""
mini_bar.py
===========
迷你工具列：主視窗收起後的浮動小工具列，只保留拍攝會用到的按鈕。

- 置頂、無邊框、可拖曳（按住空白處或左側 ⠿ 拖動）。
- 設為「不被截圖」：即使蓋在定點上，也不會出現在截圖中（Windows 10 2004+）。
- 點擊不搶焦點：翻頁連拍時，翻頁鍵仍會送到原本在最前面的視窗。
- 按鈕：開始 / 暫停 / 停止 / 手動加拍、立即單張（全部）、各定點單拍 [1] [2]…、展開主視窗。
"""
from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import winapi
from config import Region
from theme import COLORS, repolish

_STYLE = """
QFrame#mini {{
    background: rgba(29, 32, 39, 245);
    border: 1px solid {border};
    border-radius: 12px;
}}
QFrame#mini QPushButton {{
    padding: 4px 9px;
    min-width: 0;
}}
QFrame#sep {{
    background: {border};
    max-width: 1px;
    min-width: 1px;
    margin: 4px 2px;
}}
QLabel#grip {{
    color: {muted};
    font-size: 13pt;
    padding: 0 2px;
}}
QLabel#dot {{
    color: {muted};
    font-size: 11pt;
}}
QLabel#dot[state="running"] {{ color: {success}; }}
QLabel#dot[state="paused"] {{ color: #ffb84d; }}
QLabel#status {{
    color: {muted};
    padding: 0 4px;
}}
"""


class MiniBar(QWidget):
    start_req = pyqtSignal()
    pause_req = pyqtSignal()
    stop_req = pyqtSignal()
    manual_req = pyqtSignal()
    single_req = pyqtSignal()
    point_req = pyqtSignal(int)      # 只拍第 N 個定點（0 起算）
    expand_req = pyqtSignal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # 點工具列不搶焦點：翻頁連拍時按鍵才會送到原本在前面的視窗（閱讀器）
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("定點連續截圖 — 迷你模式")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("mini")
        frame.setStyleSheet(_STYLE.format(**COLORS))
        outer.addWidget(frame)

        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        grip = QLabel("⠿")
        grip.setObjectName("grip")
        grip.setToolTip("按住拖曳移動")
        grip.setCursor(Qt.CursorShape.SizeAllCursor)
        lay.addWidget(grip)

        self._dot = QLabel("●")
        self._dot.setObjectName("dot")
        self._dot.setToolTip("待機")
        lay.addWidget(self._dot)

        self._btn_start = self._button("▶", self.start_req, "開始擷取（Ctrl+Alt+S）", "primary")
        self._btn_pause = self._button("❚❚", self.pause_req, "暫停")
        self._btn_stop = self._button("■", self.stop_req, "停止（Ctrl+Alt+S）", "danger")
        self._btn_manual = self._button("＋", self.manual_req, "手動加拍一輪（F9）")
        for b in (self._btn_start, self._btn_pause, self._btn_stop, self._btn_manual):
            lay.addWidget(b)

        lay.addWidget(self._separator())
        lay.addWidget(self._button("◎ 全部", self.single_req, "立即單張：所有定點各拍一張（F8）"))
        self._points_lay = QHBoxLayout()
        self._points_lay.setSpacing(4)
        lay.addLayout(self._points_lay)
        self._point_btns: List[QPushButton] = []

        lay.addWidget(self._separator())
        self._status = QLabel("")
        self._status.setObjectName("status")
        lay.addWidget(self._status)
        lay.addWidget(self._button("⤢", self.expand_req, "展開主視窗（Ctrl+M）", "ghost"))

        self.set_state("idle")
        # 建立原生視窗後設為不被截圖
        self._excluded = winapi.exclude_from_capture(int(self.winId()))

    # ------------------------------------------------------------------
    def _button(self, text: str, signal, tip: str, variant: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        if variant:
            b.setProperty("variant", variant)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda _=False: signal.emit())
        return b

    @staticmethod
    def _separator() -> QFrame:
        sep = QFrame()
        sep.setObjectName("sep")
        return sep

    @property
    def excluded_from_capture(self) -> bool:
        return self._excluded

    # ------------------------------------------------------------------
    # 狀態同步（由主視窗呼叫）
    # ------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        """idle / running / paused"""
        running = state != "idle"
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(running)
        self._btn_manual.setEnabled(running)
        self._btn_pause.setText("▶" if state == "paused" else "❚❚")
        self._btn_pause.setToolTip(
            ("恢復" if state == "paused" else "暫停") + "（Ctrl+Alt+P）"
        )
        self._dot.setProperty("state", state)
        self._dot.setToolTip({"idle": "待機", "running": "擷取中", "paused": "已暫停"}[state])
        repolish(self._dot)

    def set_regions(self, regions: List[Region]) -> None:
        """依定點清單重建 [1] [2]… 單拍按鈕（最多 9 個，對應 Ctrl+Alt+1~9）。"""
        for b in self._point_btns:
            self._points_lay.removeWidget(b)
            b.deleteLater()
        self._point_btns = []
        for i, r in enumerate(regions[:9]):
            b = QPushButton(str(i + 1))
            b.setToolTip(f"只拍 [{i + 1}] {r.name}（Ctrl+Alt+{i + 1}）")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setEnabled(r.is_valid())
            b.clicked.connect(lambda _=False, n=i: self.point_req.emit(n))
            self._points_lay.addWidget(b)
            self._point_btns.append(b)
        self.adjustSize()

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        self.adjustSize()

    # ------------------------------------------------------------------
    # 拖曳 / 關閉
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle() is not None:
            self.windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.expand_req.emit()   # 雙擊空白處展開

    def closeEvent(self, event) -> None:  # noqa: N802
        # Alt+F4 等關閉動作 → 回到主視窗，避免程式變成看不見卻仍在執行
        event.ignore()
        self.expand_req.emit()
