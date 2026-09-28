"""
region.py
=========
全螢幕區域選取遮罩。核心：讓使用者「精準對齊」目標像素邊界。

DPI 說明（本工具一切以「物理像素」為準）：
- mss 回傳物理像素；在縮放螢幕上 Qt 的座標是「邏輯」像素 (devicePixelRatio>1)。
- 本模組維護 origin/vsize（物理）與 self._dpr，並在 邏輯(畫面繪製)↔物理(選取/擷取) 之間一致轉換，
  確保疊在螢幕上的導引與實際擷取區域落在相同物理像素。

功能：
- 凍結桌布：進入選取時先拍一張全螢幕母片，對凍結像素對齊。
- 十字準線 + 即時座標（物理像素）。
- 放大鏡：游標附近像素放大 2x/4x/8x（以物理解析度渲染、疊像素網格與十字）。
- 貼齊視窗邊緣：拖曳邊界接近視窗邊界時自動吸附。
- 鍵盤微調：方向鍵移動 1px / Shift 10px / Ctrl+方向鍵移動單邊 1px。
- 選取視窗：一鍵抓取游標下的目標視窗。
- 多定點：其他已設定的定點以虛線框顯示作為參考；重新選取時以原區域為起點微調。
- 確認 Enter / 取消 Esc。
- 控制列不擋畫面：拖曳時自動隱藏、自動避開選取框（下方 ↔ 上方）、放大鏡避開控制列、H 鍵手動隱藏。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import mss
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import winapi
from config import Config, Region

LOUPE_LOGICAL = 200  # 放大鏡顯示邊長（邏輯像素）
BAR_W, BAR_H, BAR_MARGIN = 680, 80, 18   # 控制列尺寸與離畫面邊緣距離（邏輯像素）
DEFAULT_ZOOM = 4

# 選取框的 8 個控制點（4 角 + 4 邊中點）
GRIPS = ("tl", "tr", "bl", "br", "left", "right", "top", "bottom")
# 每個控制點會改動的邊
GRIP_EDGES = {
    "tl": ("left", "top"),
    "tr": ("right", "top"),
    "bl": ("left", "bottom"),
    "br": ("right", "bottom"),
    "left": ("left",),
    "right": ("right",),
    "top": ("top",),
    "bottom": ("bottom",),
}
# 各控制點對應的縮放游標
GRIP_CURSOR = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "left": Qt.CursorShape.SizeHorCursor,
    "right": Qt.CursorShape.SizeHorCursor,
    "top": Qt.CursorShape.SizeVerCursor,
    "bottom": Qt.CursorShape.SizeVerCursor,
}


class RegionSelector(QWidget):
    """全螢幕選取遮罩。使用方式：建構後呼叫 start()，等待 finished/result。"""

    # 完成時發出：成功帶 Region，取消帶 None
    finished = pyqtSignal(object)

    def __init__(
        self,
        config: Config,
        initial: Optional[Region] = None,
        others: Optional[List[Region]] = None,
    ):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window,
        )
        self._config = config
        self._bg: Optional[QImage] = None
        self._origin = (0, 0)        # 虛擬桌面左上角「絕對物理」座標
        self._vsize = (0, 0)         # 虛擬桌面「物理」大小
        self._dpr = 1.0              # 本窗格的 devicePixelRatio (物理/邏輯)
        self._window_rects: List[Tuple[int, int, int, int]] = []
        self._selection: Optional[Tuple[int, int, int, int]] = None  # 絕對物理
        if initial is not None and initial.is_valid():
            self._selection = tuple(initial)
        # 其他定點（僅供參考顯示，不可編輯）
        self._others: List[Region] = [r for r in (others or []) if r.is_valid()]
        self._drag_start: Optional[Tuple[int, int]] = None
        self._dragging = False
        self._cursor_abs = (0, 0)    # 絕對物理
        self.zoom = DEFAULT_ZOOM
        self._result: Optional[Region] = None
        self._loupe_geom = (0, 0)    # 邏輯：放大鏡左上角（供繪製）

        # 游標微調/縮放狀態
        self._action: Optional[str] = None        # "draw" / "move" / "resize"
        self._grip: Optional[str] = None          # 目前縮放的控制點
        self._move_start_abs = (0, 0)             # 移動起點（物理）
        self._move_start_sel: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._resize_start_sel: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._hover_grip: Optional[str] = None    # 游標目前所在的控制點（無按鍵）
        self._bar_enabled = True                  # H 鍵切換控制列顯示

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)  # 預設十字游標
        self._build_ui()

    # ------------------------------------------------------------------
    # 建立控制列
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        bar = QWidget(self)
        bar.setObjectName("bar")
        bar.setStyleSheet(
            "#bar { background: rgba(21,23,28,245); border: 1px solid rgba(255,255,255,20);"
            " border-radius: 12px; }"
            "QLabel { color: #8b93a1; background: transparent; }"
            "QPushButton { background: #262a33; color: #e7e9ee; border: 1px solid #2f343e;"
            " padding: 5px 12px; border-radius: 8px; }"
            "QPushButton:hover { background: #30353f; }"
            "QPushButton#ok { background: #4f8cff; border-color: #4f8cff; color: #fff;"
            " font-weight: 600; }"
            "QPushButton#ok:hover { background: #6b9fff; }"
            "QPushButton#cancel { background: rgba(239,91,91,0.12); color: #ef5b5b;"
            " border-color: rgba(239,91,91,0.45); }"
            "QPushButton#cancel:hover { background: rgba(239,91,91,0.22); }"
        )
        # 提示列（鍵盤/滑鼠操作）＋ 按鈕列
        vlay = QVBoxLayout(bar)
        vlay.setContentsMargins(12, 8, 12, 10)
        vlay.setSpacing(6)

        hint = QLabel(
            "滑鼠：拖曳框選 ・ 框內拖曳移動 ・ 拖控制點縮放　　"
            "鍵盤：方向鍵微調 ・ Shift 10px ・ Ctrl 調整單邊\n"
            "＋/－ 放大鏡倍率 ・ H 隱藏面板 ・ Enter 確認 ・ Esc 取消"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = hint.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        hint.setFont(f)
        vlay.addWidget(hint)

        hlay = QHBoxLayout()
        hlay.setSpacing(6)

        btn_zoom_out = QPushButton("－", bar)
        btn_zoom_out.setToolTip("縮小放大倍率")
        btn_zoom_in = QPushButton("＋", bar)
        btn_zoom_in.setToolTip("放大倍率")
        btn_win = QPushButton("選取游標下視窗", bar)
        btn_win.setToolTip("抓取游標所在的頂層視窗")
        btn_ok = QPushButton("✓  確認 (Enter)", bar)
        btn_ok.setObjectName("ok")
        btn_cancel = QPushButton("取消 (Esc)", bar)
        btn_cancel.setObjectName("cancel")
        for b in (btn_zoom_out, btn_zoom_in, btn_win, btn_ok, btn_cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            # 按鈕不搶鍵盤焦點，點過之後方向鍵 / ＋－ / Enter 仍由遮罩處理
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        btn_zoom_out.clicked.connect(lambda: self._set_zoom(self.zoom // 2))
        btn_zoom_in.clicked.connect(lambda: self._set_zoom(self.zoom * 2))
        btn_win.clicked.connect(self._select_window_under_cursor)
        btn_ok.clicked.connect(self._accept)
        btn_cancel.clicked.connect(self._cancel)

        hlay.addWidget(btn_zoom_out)
        hlay.addWidget(btn_zoom_in)
        hlay.addWidget(btn_win)
        hlay.addStretch(1)
        hlay.addWidget(btn_ok)
        hlay.addWidget(btn_cancel)
        vlay.addLayout(hlay)
        self._bar = bar

    def _set_zoom(self, z: int) -> None:
        self.zoom = max(2, min(16, z))
        self.update()

    # ------------------------------------------------------------------
    # 對外流程
    # ------------------------------------------------------------------
    def start(self) -> None:
        """顯示遮罩前：取凍結桌布、視窗矩形、DPI。"""
        self._capture_frozen()
        self._window_rects = winapi.enumerate_window_rects()
        self._dpr = self._detect_dpr()
        # 讓母片以其物理/邏輯比例繪製，避免縮放螢幕上的縮放失真
        if self._bg is not None:
            self._bg.setDevicePixelRatio(self._dpr)

        # 以「邏輯」幾何覆蓋整片虛擬桌面（物理/dpr）
        gx = round(self._origin[0] / self._dpr)
        gy = round(self._origin[1] / self._dpr)
        gw = round(self._vsize[0] / self._dpr)
        gh = round(self._vsize[1] / self._dpr)
        self.setGeometry(gx, gy, gw, gh)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _detect_dpr(self) -> float:
        scr = self.screen() if self.screen() else QGuiApplication.primaryScreen()
        if scr is not None:
            return float(scr.devicePixelRatio())
        return 1.0

    def closeEvent(self, event) -> None:  # noqa: N802
        super().closeEvent(event)

    def bg_image(self) -> Optional[QImage]:
        return self._bg

    # ------------------------------------------------------------------
    # 凍結桌布
    # ------------------------------------------------------------------
    def _capture_frozen(self) -> None:
        with mss.mss() as sct:
            m0 = sct.monitors[0]
            shot = sct.grab(m0)
            raw = bytes(shot.rgb)
            img = QImage(raw, m0["width"], m0["height"], m0["width"] * 3,
                         QImage.Format.Format_RGB888).copy()
            self._bg = img
            self._origin = (m0["left"], m0["top"])
            self._vsize = (m0["width"], m0["height"])

    # ------------------------------------------------------------------
    # 座標轉換：physical(絕對) ↔ logical(畫面)
    # ------------------------------------------------------------------
    def _to_abs(self, pos: QPoint) -> Tuple[int, int]:
        """邏輯視窗座標 → 絕對物理。"""
        ax = self._origin[0] + round(pos.x() * self._dpr)
        ay = self._origin[1] + round(pos.y() * self._dpr)
        return (ax, ay)

    def _lx(self, phys: int) -> float:
        """絕對物理 → 視窗邏輯 x。"""
        return (phys - self._origin[0]) / self._dpr

    def _ly(self, phys: int) -> float:
        return (phys - self._origin[1]) / self._dpr

    # ------------------------------------------------------------------
    # 滑鼠
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()            # QPointF，避免 .toPoint() 截斷小數
        abs_pt = self._to_abs(pos)
        self._cursor_abs = abs_pt
        grip = self._hit_test(pos)

        if grip in GRIPS:
            # 控制點：開始縮放
            self._action = "resize"
            self._grip = grip
            self._resize_start_sel = tuple(self._selection)  # type: ignore[arg-type]
            self._cursor_abs = abs_pt
        elif grip == "move":
            # 區域內：開始移動
            self._action = "move"
            self._move_start_abs = abs_pt
            self._move_start_sel = tuple(self._selection)  # type: ignore[arg-type]
        else:
            # 區域外：開始新繪製
            self._action = "draw"
            self._drag_start = abs_pt
            self._selection = (abs_pt[0], abs_pt[1], 0, 0)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()            # QPointF，保留小數才不會在縮放螢幕上座標漂移
        abs_pt = self._to_abs(pos)
        self._cursor_abs = abs_pt
        self._sync_selection()
        self._update_cursor(pos)
        self._position_loupe_logical()
        self.update()

    def _sync_selection(self) -> None:
        """依目前動作與 self._cursor_abs 同步選取框（供滑鼠與鍵盤共用）。"""
        if self._action == "draw" and self._drag_start is not None:
            sx, sy = self._drag_start
            cx, cy = self._cursor_abs
            left, top = min(sx, cx), min(sy, cy)
            w, h = abs(cx - sx), abs(cy - sy)
            self._selection = self._apply_snap((left, top, w, h))
        elif self._action == "move":
            dx = self._cursor_abs[0] - self._move_start_abs[0]
            dy = self._cursor_abs[1] - self._move_start_abs[1]
            l, t, w, h = self._move_start_sel
            self._selection = self._apply_snap((l + dx, t + dy, w, h))
        elif self._action == "resize":
            self._selection = self._apply_resize(self._grip, self._cursor_abs, self._resize_start_sel)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        if self._action == "draw" and self._selection is not None and (
            self._selection[2] <= 1 or self._selection[3] <= 1
        ):
            # 退化為點擊：改為選取游標下視窗
            self._select_window_under_cursor()
        self._action = None
        self._grip = None
        self._update_cursor(pos)
        self.update()

    # ------------------------------------------------------------------
    # 命中測試與縮放/移動
    # ------------------------------------------------------------------
    def _sel_rect_logical(self) -> Optional[Tuple[float, float, float, float]]:
        """回傳選取框的邏輯幾何 (x, y, w, h)，無選取回 None。"""
        if self._selection is None:
            return None
        l, t, w, h = self._selection
        return (self._lx(l), self._ly(t), w / self._dpr, h / self._dpr)

    def _hit_test(self, pos: QPoint) -> Optional[str]:
        """回傳該邏輯點落在的控制點 / "move" / None。"""
        r = self._sel_rect_logical()
        if r is None:
            return None
        x, y = pos.x(), pos.y()
        rx, ry, rw, rh = r
        tol = 8
        if rw <= 1 or rh <= 1:
            return "move" if (rx <= x <= rx + rw and ry <= y <= ry + rh) else None

        def near(px: float, py: float) -> bool:
            return abs(x - px) <= tol and abs(y - py) <= tol

        # 角點優先
        for grip, (px, py) in [
            ("tl", (rx, ry)), ("tr", (rx + rw, ry)),
            ("bl", (rx, ry + rh)), ("br", (rx + rw, ry + rh)),
        ]:
            if near(px, py):
                return grip
        # 邊中點
        for grip, (px, py) in [
            ("left", (rx, ry + rh / 2)), ("right", (rx + rw, ry + rh / 2)),
            ("top", (rx + rw / 2, ry)), ("bottom", (rx + rw / 2, ry + rh)),
        ]:
            if near(px, py):
                return grip
        # 區域內
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return "move"
        return None

    def _apply_resize(
        self,
        grip: str,
        abs_pt: Tuple[int, int],
        start: Tuple[int, int, int, int],
    ) -> Tuple[int, int, int, int]:
        """依控制點 + 游標(物理) 縮放選取框。"""
        cx, cy = abs_pt
        l, t, w, h = start
        right, bottom = l + w, t + h
        moves = GRIP_EDGES[grip]
        if "left" in moves:
            l = cx
        if "right" in moves:
            right = cx
        if "top" in moves:
            t = cy
        if "bottom" in moves:
            bottom = cy
        w = max(1, right - l)
        h = max(1, bottom - t)
        return self._apply_snap((l, t, w, h))

    def _update_cursor(self, pos: QPoint) -> None:
        """依目前動作 / 命中測試切換游標形狀。"""
        if self._action == "draw":
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        grip = self._hit_test(pos)
        self._hover_grip = grip
        shape = Qt.CursorShape.CrossCursor
        if self._action == "move" or grip == "move":
            shape = Qt.CursorShape.SizeAllCursor
        elif self._action == "resize" or grip in GRIPS:
            shape = GRIP_CURSOR.get(grip or self._grip, Qt.CursorShape.CrossCursor)
        self.setCursor(shape)


    def _position_loupe_logical(self) -> None:
        """計算放大鏡在邏輯畫面上的位置（放在游標右下方）。"""
        cx = self._lx(self._cursor_abs[0])
        cy = self._ly(self._cursor_abs[1])
        bar = self._bar_geometry()
        L = LOUPE_LOGICAL
        # 依序嘗試：右下 → 右上 → 左下 → 左上，取第一個不被控制列蓋住的位置
        candidates = [
            (cx + 24, cy + 24), (cx + 24, cy - 24 - L),
            (cx - 24 - L, cy + 24), (cx - 24 - L, cy - 24 - L),
        ]
        clamped = [
            (max(0.0, min(ox, self.width() - L)), max(0.0, min(oy, self.height() - L)))
            for ox, oy in candidates
        ]
        ox, oy = clamped[0]
        if bar is not None:
            for x, y in clamped:
                if not QRectF(x, y, L, L).intersects(QRectF(bar)):
                    ox, oy = x, y
                    break
        self._loupe_geom = (round(ox), round(oy))

    # ------------------------------------------------------------------
    # 貼齊
    # ------------------------------------------------------------------
    def _apply_snap(self, raw: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if not self._config.snap_enabled:
            return raw
        d = self._config.snap_distance
        xs = [x for r in self._window_rects for x in (r[0], r[2])]
        ys = [y for r in self._window_rects for y in (r[1], r[3])]
        left, top, width, height = raw
        right, bottom = left + width, top + height
        nl = self._snap_edge(left, xs, d)
        nr = self._snap_edge(right, xs, d)
        nt = self._snap_edge(top, ys, d)
        nb = self._snap_edge(bottom, ys, d)
        w = nr - nl
        h = nb - nt
        return (nl, nt, w, h)

    @staticmethod
    def _snap_edge(value: int, candidates: List[int], dist: int) -> int:
        best = None
        best_delta = dist + 1
        for c in candidates:
            delta = abs(value - c)
            if delta <= dist and delta < best_delta:
                best_delta = delta
                best = c
        return best if best is not None else value

    # ------------------------------------------------------------------
    # 選取游標下視窗
    # ------------------------------------------------------------------
    def _select_window_under_cursor(self) -> None:
        cx, cy = self._cursor_abs
        overlay_rect = (self._origin[0], self._origin[1],
                        self._origin[0] + self._vsize[0], self._origin[1] + self._vsize[1])
        for r in self._window_rects:
            if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
                if r != overlay_rect:
                    self._selection = (r[0], r[1], r[2] - r[0], r[3] - r[1])
                    self.update()
                    return

    # ------------------------------------------------------------------
    # 鍵盤微調
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept()
            return
        # 放大鏡倍率：主鍵盤 = / + / - / _ 與數字鍵盤 + / - 皆可（不必按 Shift）
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._set_zoom(self.zoom * 2)
            return
        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self._set_zoom(self.zoom // 2)
            return
        if key == Qt.Key.Key_H:
            self._bar_enabled = not self._bar_enabled
            self._position_loupe_logical()
            self.update()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            step = 10 if shift else 1
            if ctrl and self._selection is not None:
                # 調整「單邊」（錨定對邊，不移動游標）
                l, t, w, h = self._selection
                if key == Qt.Key.Key_Left:
                    l -= step
                    w += step          # 錨定右邊
                elif key == Qt.Key.Key_Right:
                    w += step          # 錨定左邊（右邊右移）
                elif key == Qt.Key.Key_Up:
                    t -= step
                    h += step          # 錨定底邊
                elif key == Qt.Key.Key_Down:
                    h += step          # 錨定頂邊（底邊下移）
                raw = self._apply_snap((l, t, max(1, w), max(1, h)))
                self._selection = (raw[0], raw[1], max(1, raw[2]), max(1, raw[3]))
            else:
                # 移動「滑鼠游標」位置
                dx = dy = 0
                if key == Qt.Key.Key_Left:
                    dx = -step
                elif key == Qt.Key.Key_Right:
                    dx = step
                elif key == Qt.Key.Key_Up:
                    dy = -step
                elif key == Qt.Key.Key_Down:
                    dy = step
                self._move_cursor_by(dx, dy)
            self.update()
            event.accept()

    def _move_cursor_by(self, dx: int, dy: int) -> None:
        """以方向鍵微調遊標（物理像素）。正在繪製/移動/縮放時會同步更新選取框。"""
        try:
            import ctypes
            nx = self._cursor_abs[0] + dx
            ny = self._cursor_abs[1] + dy
            l, t = self._origin
            rw, rh = self._vsize
            nx = max(l, min(nx, l + rw - 1))
            ny = max(t, min(ny, t + rh - 1))
            ctypes.windll.user32.SetCursorPos(nx, ny)
            self._cursor_abs = (nx, ny)
            self._sync_selection()          # 若在拖曳中，同步移動/縮放選取框
            self._position_loupe_logical()
            self.update()
        except Exception as exc:  # noqa: BLE001
            print("move cursor failed:", exc)

    # ------------------------------------------------------------------
    # 確認 / 取消
    # ------------------------------------------------------------------
    def _accept(self) -> None:
        if self._selection is None:
            return
        l, t, w, h = self._selection
        if w <= 0 or h <= 0:
            return
        self._result = Region(l, t, w, h)
        self.finished.emit(self._result)
        self.close()

    def _cancel(self) -> None:
        self._result = None
        self.finished.emit(None)
        self.close()

    def result(self) -> Optional[Region]:
        return self._result

    # ------------------------------------------------------------------
    # 繪製
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        if self._bg is not None and not self._bg.isNull():
            # 背景：物理母片以邏輯尺寸畫出，Qt 依 DPR 映射回物理 1:1
            painter.drawImage(self.rect(), self._bg)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 70))
        else:
            painter.fillRect(self.rect(), QColor(20, 20, 20))

        self._paint_crosshair(painter)
        self._paint_others(painter)
        self._paint_selection(painter)
        self._paint_loupe(painter)

        # 控制列（子 widget，位於邏輯座標）
        self._position_bar()
        self._bar.raise_()
        painter.end()

    def _paint_crosshair(self, painter: QPainter) -> None:
        if not self._cursor_abs:
            return
        cx = self._lx(self._cursor_abs[0])
        cy = self._ly(self._cursor_abs[1])
        pen = QPen(QColor(255, 255, 255, 90))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(round(cx), 0, round(cx), self.height())
        painter.drawLine(0, round(cy), self.width(), round(cy))

    def _paint_others(self, painter: QPainter) -> None:
        """以橘色虛線框標出其他已設定的定點，方便對齊與避免重疊。"""
        pen = QPen(QColor(255, 170, 0, 200))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        for r in self._others:
            rect = QRectF(self._lx(r.left), self._ly(r.top),
                          r.width / self._dpr, r.height / self._dpr)
            painter.setPen(pen)
            painter.drawRect(rect)
            if r.name:
                tag = QRectF(rect.x(), rect.y(), 8 + 7 * len(r.name), 16)
                painter.fillRect(tag, QColor(255, 170, 0, 200))
                painter.setPen(QColor(0, 0, 0))
                painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, r.name)

    def _paint_selection(self, painter: QPainter) -> None:
        if self._selection is None:
            return
        l, t, w, h = self._selection
        x = self._lx(l)
        y = self._ly(t)
        rw = w / self._dpr
        rh = h / self._dpr
        rect = QRectF(x, y, rw, rh)

        # 挖空：區域內保持母片原亮度（在暗化層上重繪母片）
        if self._bg is not None:
            src = QRect(l - self._origin[0], t - self._origin[1], w, h)
            painter.drawImage(rect, self._bg, QRectF(src))

        pen = QPen(QColor(0, 200, 255))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(rect)

        # 三分網格
        grid_pen = QPen(QColor(0, 200, 255, 120))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        from PyQt6.QtCore import QLineF
        for i in range(1, 3):
            gx = rect.x() + rect.width() * i / 3.0
            gy = rect.y() + rect.height() * i / 3.0
            painter.drawLine(QLineF(gx, rect.y(), gx, rect.y() + rect.height()))
            painter.drawLine(QLineF(rect.x(), gy, rect.x() + rect.width(), gy))

        # 尺寸標籤
        label = f"{w} x {h}   @({l},{t})"
        painter.setPen(QColor(255, 255, 255))
        label_rect = QRectF(round(rect.x()), max(0, round(rect.y() - 22)), 230, 20)
        painter.fillRect(label_rect, QColor(0, 0, 0, 160))
        painter.drawText(round(rect.x()) + 4, round(max(12, rect.y() - 6)), label)

        # 8 個控制點（邊/角），可拖曳縮放
        hs = 4
        pts = {
            "tl": (rect.x(), rect.y()),
            "tr": (rect.x() + rect.width(), rect.y()),
            "bl": (rect.x(), rect.y() + rect.height()),
            "br": (rect.x() + rect.width(), rect.y() + rect.height()),
            "left": (rect.x(), rect.y() + rect.height() / 2),
            "right": (rect.x() + rect.width(), rect.y() + rect.height() / 2),
            "top": (rect.x() + rect.width() / 2, rect.y()),
            "bottom": (rect.x() + rect.width() / 2, rect.y() + rect.height()),
        }
        active = self._grip if self._grip else self._hover_grip
        for g, (px, py) in pts.items():
            hp = QRectF(px - hs, py - hs, hs * 2, hs * 2)
            if g == active:
                painter.fillRect(hp, QColor(255, 120, 0))
            else:
                painter.fillRect(hp, QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 80, 180)))
            painter.drawRect(hp)

    def _paint_loupe(self, painter: QPainter) -> None:
        bg = self._bg
        if bg is None or bg.isNull():
            return
        zoom = self.zoom
        disp_phys = round(LOUPE_LOGICAL * self._dpr)   # 放大鏡物理邊長
        src_w = max(1, disp_phys // zoom)
        src_h = max(1, disp_phys // zoom)

        cx, cy = self._cursor_abs
        crop_left = cx - src_w // 2
        crop_top = cy - src_h // 2
        crop_left = max(0, min(crop_left, bg.width() - src_w))
        crop_top = max(0, min(crop_top, bg.height() - src_h))

        # 在高解析度 QImage 上以物理像素繪製（保持尖銳）
        lw = disp_phys
        loupe_img = QImage(lw, lw, QImage.Format.Format_ARGB32)
        loupe_img.setDevicePixelRatio(self._dpr)
        lp = QPainter(loupe_img)
        lp.fillRect(0, 0, lw, lw, QColor(30, 30, 32))
        lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        src = QRect(crop_left, crop_top, src_w, src_h)
        lp.drawImage(QRect(0, 0, lw, lw), bg, src)

        # 像素網格（每 zoom 物理像素一條）
        pen = QPen(QColor(255, 255, 255, 80))
        pen.setWidth(1)
        lp.setPen(pen)
        for gx in range(0, lw + 1, zoom):
            lp.drawLine(gx, 0, gx, lw)
        for gy in range(0, lw + 1, zoom):
            lp.drawLine(0, gy, lw, gy)

        # 十字準線（對應游標物理落點）
        loupe_cx = (cx - crop_left) * zoom
        loupe_cy = (cy - crop_top) * zoom
        cross = QPen(QColor(255, 60, 60))
        cross.setWidth(1)
        lp.setPen(cross)
        lp.drawLine(loupe_cx, 0, loupe_cx, lw)
        lp.drawLine(0, loupe_cy, lw, loupe_cy)

        # 邊框 + 座標
        lp.setPen(QColor(255, 255, 255, 200))
        lp.drawRect(0, 0, lw - 1, lw - 1)
        f = QFont()
        f.setPointSize(8)
        lp.setFont(f)
        lp.setPen(QColor(255, 255, 255))
        lp.fillRect(0, lw - 18, 150, 18, QColor(0, 0, 0, 170))
        lp.drawText(6, lw - 6, f"({cx},{cy}) x{zoom}")
        lp.end()

        painter.drawImage(self._loupe_geom[0], self._loupe_geom[1], loupe_img)

    def _bar_geometry(self) -> Optional[QRect]:
        """控制列應在的位置；拖曳中或使用者按 H 隱藏時回傳 None。

        預設放在下方中央；若會蓋住選取框（或其他定點）就改放上方中央，
        兩邊都會蓋住時選擇遮擋面積較小的一邊（選取框權重較高）。
        """
        if not self._bar_enabled or self._action is not None:
            return None
        x = (self.width() - BAR_W) // 2
        candidates = [
            QRect(x, self.height() - BAR_H - BAR_MARGIN, BAR_W, BAR_H),   # 下方
            QRect(x, BAR_MARGIN, BAR_W, BAR_H),                           # 上方
        ]
        obstacles = [
            (QRectF(self._lx(r.left), self._ly(r.top), r.width / self._dpr, r.height / self._dpr), 1.0)
            for r in self._others
        ]
        sel = self._sel_rect_logical()
        if sel is not None:
            obstacles.append((QRectF(*sel), 100.0))

        def covered(rect: QRect) -> float:
            total = 0.0
            for ob, weight in obstacles:
                inter = QRectF(rect).intersected(ob)
                total += inter.width() * inter.height() * weight
            return total

        return min(candidates, key=covered)   # 同分時保留下方

    def _position_bar(self) -> None:
        bar = self._bar
        geom = self._bar_geometry()
        if geom is not None and bar.geometry() != geom:
            bar.setGeometry(geom)
        if bar.isHidden() == (geom is not None):
            bar.setVisible(geom is not None)
