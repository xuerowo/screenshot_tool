"""
app.py
======
主視窗（控制面板）：
- 定點清單：可新增多個定點區域，每次擷取會把所有定點各拍一張。
- 選中定點：縮圖預覽 + L/T/W/H 數值微調 + 1px 微調按鍵 + 重新選取 / 改名 / 刪除。
- 參數：擷取模式(間隔/FPS)、輸出目錄、格式/品質、檔名前綴、上限。
- 控制：開始 / 暫停 / 停止 / 手動加拍 / 立即單張。
- 即時預覽 + 統計。

所有座標與尺寸皆為「物理像素」。
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from typing import List, Optional

import mss
from PyQt6.QtCore import Qt, QEvent, QPoint, QTimer, QEventLoop, pyqtSignal
from PyQt6.QtGui import QAction, QGuiApplication, QImage, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import winapi
from shortcuts_help import ShortcutsPopup
from theme import CARD_MARGINS, CARD_MARGINS_UNTITLED, COLORS, repolish
from cap_engine import (
    CaptureController,
    LivePreview,
    grab_region,
    FILENAME_SEQ_MARK,
    FILENAME_TOKENS,
    check_filename_template,
    peek_folder_seq,
    region_file_path,
    render_filename,
    save_image,
)
from config import DEFAULT_FILENAME_TEMPLATE, Config, Region, default_region_name
from mini_bar import MiniBar
from region import RegionSelector

# 檔名格式預設選項：(範本, 說明)
FILENAME_PRESETS = [
    (DEFAULT_FILENAME_TEMPLATE, "預設：全部放同一個資料夾"),
    ("{名稱}/{前綴}_{日期}_{時間}_{毫秒}_{輪次}", "每個定點一個子資料夾"),
    ("{日期}/{名稱}_{時間}_{毫秒}_{輪次}", "每天一個子資料夾"),
    ("{名稱}/{序號}", "每個定點一個子資料夾，依序編號 0001、0002…"),
    ("{名稱}_{輪次}", "簡短：名稱 + 輪次"),
]

# 翻頁連拍可選的按鍵：(顯示文字, winapi.PAGE_KEYS 鍵名)
PAGE_KEY_CHOICES = [
    ("←  左方向鍵", "left"), ("→  右方向鍵", "right"),
    ("↑  上方向鍵", "up"), ("↓  下方向鍵", "down"),
    ("Page Down", "pagedown"), ("Page Up", "pageup"),
    ("空白鍵", "space"), ("Enter", "enter"),
]

PREVIEW_SIZE = (240, 110)   # 預覽框最小尺寸（邏輯像素）
LIVE_PREVIEW_MS = 200       # 左側定點預覽的即時刷新間隔（毫秒）


class MainWindow(QMainWindow):
    # 全域快捷鍵（由 keyboard 執行緒發出，跨執行緒排程到主執行緒）
    single_req = pyqtSignal()
    manual_req = pyqtSignal()
    select_req = pyqtSignal()
    add_req = pyqtSignal()
    point_req = pyqtSignal(int)   # 只拍第 N 個定點（0 起算）
    toggle_req = pyqtSignal()      # 開始 / 停止
    pause_toggle_req = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._regions: List[Region] = list(config.regions)
        self._current = 0 if self._regions else -1   # 目前選中的定點索引
        self._controller = CaptureController()
        self._paused = False
        self._live_preview = LivePreview(LIVE_PREVIEW_MS)
        self._mini = MiniBar()          # 迷你模式的浮動工具列
        self._is_mini = False
        self._has_keyboard = False
        self._keyboard_hook = None

        self.setWindowTitle("定點連續截圖工具 — 精準對齊版")

        self._build_ui()
        self._connect_signals()
        self.resize(self.sizeHint())
        winapi.set_dark_title_bar(int(self.winId()))

        # 將已儲存的定點同步到清單、數值欄位與預覽
        self._refresh_list()
        self._load_current_to_spins()
        self._update_filename_example()
        self._mini.set_regions(self._regions)

        # 左側預覽即時更新（背景執行緒抓取，見 cap_engine.LivePreview）
        self._live_preview.frame.connect(self._on_live_preview_frame)
        self._live_preview.error.connect(lambda msg: self._preview.setText(f"預覽失敗\n{msg}"))
        self.refresh_preview()
        self._live_preview.start()

        self._try_enable_string_keyboard()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI 建立
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(8)

        root.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self._build_region_group(), 5)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._build_status_group())
        right.addWidget(self._build_mode_group())
        right.addWidget(self._build_output_group())
        right.addStretch(1)
        body.addLayout(right, 6)
        root.addLayout(body, 1)

        root.addWidget(self._build_control_group())


        # 卡片內距（標題列留空間）
        for box in central.findChildren(QGroupBox):
            margins = CARD_MARGINS if box.title() else CARD_MARGINS_UNTITLED
            box.layout().setContentsMargins(*margins)

        self._setup_focus_release(central)

    def _setup_focus_release(self, central: QWidget) -> None:
        """讓輸入框的焦點容易取消。

        - 點空白處 / 卡片 / 文字：Qt 會沿父層找可接受點擊焦點的元件，找到 central → 焦點移走。
        - 按鈕、單選、勾選不接焦點：點它們同樣會把焦點交給 central，也不會殘留焦點。
        - 輸入框內按 Esc / Enter：確認並離開（見 eventFilter）。
        """
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for cls in (QPushButton, QRadioButton, QCheckBox):
            for w in central.findChildren(cls):
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for cls in (QLineEdit, QAbstractSpinBox, QComboBox):
            for w in central.findChildren(cls):
                w.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if isinstance(obj, QAbstractSpinBox):
                obj.interpretText()          # 確認輸入到一半的數值
            # 延後一拍：讓元件先處理 Enter（例如 returnPressed），再移走焦點
            QTimer.singleShot(0, self.centralWidget().setFocus)
        return super().eventFilter(obj, event)

    def _build_header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        title = QLabel("定點連續截圖")
        title.setObjectName("title")
        subtitle = QLabel("多定點 ・ 物理像素精準對齊")
        subtitle.setObjectName("subtitle")
        h.addWidget(title)
        h.addSpacing(10)
        h.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignBottom)
        h.addStretch(1)
        btn_mini = self._button(
            "⤡  迷你模式", "ghost",
            "收成小工具列，只留拍攝按鈕（Ctrl+M）\n工具列不會被拍進截圖",
        )
        btn_mini.clicked.connect(self._enter_mini)
        h.addWidget(btn_mini, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(4)
        self._btn_help = self._button("?", tip="快捷鍵一覽（F1）")
        self._btn_help.setObjectName("help")
        self._btn_help.setFixedSize(26, 26)
        self._btn_help.clicked.connect(self._show_shortcuts)
        h.addWidget(self._btn_help, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(6)
        self._pill = QLabel("● 待機")
        self._pill.setObjectName("pill")
        h.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignVCenter)
        return h

    @staticmethod
    def _button(text: str, variant: str = "", tip: str = "") -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setProperty("variant", variant)
        if tip:
            b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @staticmethod
    def _make_preview(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("preview")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setMinimumSize(*PREVIEW_SIZE)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return lbl

    def _build_region_group(self) -> QGroupBox:
        box = QGroupBox("擷取定點")
        lay = QVBoxLayout(box)
        lay.setSpacing(7)

        # 定點清單
        self._list = QListWidget()
        self._list.setMinimumHeight(84)
        self._list.setToolTip("每次擷取會依清單由上而下把所有定點各拍一張（可拖曳調整順序）\n[N] = 按 Ctrl+Alt+N 只拍該定點\n雙擊可重新命名（名稱會用在檔名）")
        self._list.currentRowChanged.connect(self._on_list_row_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._on_rename_region())
        # 拖曳調整順序（清單順序 = 拍攝順序 = Ctrl+Alt+N 編號）
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        # 依 Qt 版本，拖放可能是 rowsMoved 或「移除 + 插入」；延後到拖放結束再同步
        model = self._list.model()
        model.rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._on_rows_moved))
        model.rowsInserted.connect(lambda *_: QTimer.singleShot(0, self._on_rows_moved))
        lay.addWidget(self._list, 1)

        list_btns = QHBoxLayout()
        list_btns.setSpacing(6)
        btn_add = self._button("＋ 新增定點", tip="開啟全螢幕選取遮罩，新增一個定點（全域快捷鍵 F6）")
        btn_add.clicked.connect(self._on_add_region)
        self._btn_reselect = self._button(
            "重新選取", "ghost", "以目前定點為起點重新框選 / 微調\n快捷鍵：全域 F7 / 視窗內 Ctrl+R"
        )
        self._btn_reselect.clicked.connect(self._on_select_region)
        self._btn_rename = self._button("改名", "ghost")
        self._btn_rename.clicked.connect(self._on_rename_region)
        self._btn_delete = self._button("刪除", "ghost")
        self._btn_delete.clicked.connect(self._on_delete_region)
        list_btns.addWidget(btn_add)
        self._btn_up = self._button("↑", "ghost", "上移：提早拍攝，編號 [N] 變小（也可直接拖曳清單項目）")
        self._btn_up.setFixedWidth(36)
        self._btn_up.clicked.connect(lambda: self._move_region(-1))
        self._btn_down = self._button("↓", "ghost", "下移：延後拍攝，編號 [N] 變大（也可直接拖曳清單項目）")
        self._btn_down.setFixedWidth(36)
        self._btn_down.clicked.connect(lambda: self._move_region(1))
        list_btns.addWidget(self._btn_up)
        list_btns.addWidget(self._btn_down)
        list_btns.addStretch(1)
        for b in (self._btn_reselect, self._btn_rename, self._btn_delete):
            list_btns.addWidget(b)
        lay.addLayout(list_btns)

        self._preview = self._make_preview("尚未選取區域")
        lay.addWidget(self._preview)

        # 數值座標（目前選中的定點）
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._spin_l = self._make_spin(-100000, 100000)
        self._spin_t = self._make_spin(-100000, 100000)
        self._spin_w = self._make_spin(0, 100000)
        self._spin_h = self._make_spin(0, 100000)
        for col, (text, spin) in enumerate([
            ("X", self._spin_l), ("Y", self._spin_t),
            ("寬", self._spin_w), ("高", self._spin_h),
        ]):
            lbl = QLabel(text)
            lbl.setObjectName("muted")
            grid.addWidget(lbl, 0, col)
            grid.addWidget(spin, 1, col)
        lay.addLayout(grid)
        for s in (self._spin_l, self._spin_t, self._spin_w, self._spin_h):
            s.valueChanged.connect(self._on_region_num_changed)

        # 微調按鍵（1px / 10px）
        self._step_combo = QComboBox()
        self._step_combo.addItems(["1px", "5px", "10px"])
        self._step_combo.setCurrentIndex(0)
        nudge_row = QHBoxLayout()
        nudge_row.setSpacing(6)
        lbl = QLabel("微調")
        lbl.setObjectName("muted")
        nudge_row.addWidget(lbl)
        for text, target in [
            ("←", "left"), ("→", "right"), ("↑", "up"), ("↓", "down"),
        ]:
            b = self._button(text)
            b.setFixedWidth(40)
            b.clicked.connect(lambda _=False, t=target: self._nudge(t))
            nudge_row.addWidget(b)
        nudge_row.addStretch(1)
        lbl = QLabel("步進")
        lbl.setObjectName("muted")
        nudge_row.addWidget(lbl)
        nudge_row.addWidget(self._step_combo)
        lay.addLayout(nudge_row)

        return box

    def _make_spin(self, lo: int, hi: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(0)
        s.setMinimumWidth(80)
        return s

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        return lbl

    def _build_mode_group(self) -> QGroupBox:
        box = QGroupBox("擷取節奏")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        self._radio_interval = QRadioButton("定時間隔")
        self._radio_fps = QRadioButton("每秒幀數 (FPS)")
        self._radio_page = QRadioButton("翻頁連拍")
        self._radio_page.setToolTip(
            "先拍當前頁，接著：按翻頁鍵 → 等待 → 拍下一頁，重複到上限輪數\n"
            "（N 輪 = 拍 N 頁、翻 N-1 次）\n"
            "按鍵會送到最前面的視窗（例如閱讀器）"
        )
        self._radio_interval.setChecked(
            self._config.capture_mode == "interval"
        )
        self._radio_fps.setChecked(self._config.capture_mode == "fps")
        self._radio_page.setChecked(self._config.capture_mode == "page")
        modes = QHBoxLayout()
        modes.setSpacing(18)
        modes.addWidget(self._radio_interval)
        modes.addWidget(self._radio_fps)
        modes.addWidget(self._radio_page)
        modes.addStretch(1)
        grid.addWidget(self._field_label("模式"), 0, 0)
        grid.addLayout(modes, 0, 1, 1, 4)

        self._spin_interval = QDoubleSpinBox()
        self._spin_interval.setRange(0.05, 3600.0)
        self._spin_interval.setDecimals(2)
        self._spin_interval.setSuffix(" 秒")
        self._spin_interval.setValue(self._config.interval_seconds)
        self._spin_fps = QSpinBox()
        self._spin_fps.setRange(1, 240)
        self._spin_fps.setSuffix(" fps")
        self._spin_fps.setValue(self._config.fps)
        # 各模式專屬欄位：只顯示目前模式的（見 _on_mode_radio）
        self._lbl_interval = self._field_label("間隔")
        self._lbl_fps = self._field_label("幀數")
        grid.addWidget(self._lbl_interval, 1, 0)
        grid.addWidget(self._spin_interval, 1, 1)
        grid.addWidget(self._lbl_fps, 1, 0)       # 與「間隔」同一格，一次只顯示一個
        grid.addWidget(self._spin_fps, 1, 1)

        # 翻頁連拍
        self._combo_page_key = QComboBox()
        for text, key in PAGE_KEY_CHOICES:
            self._combo_page_key.addItem(text, key)
        idx = self._combo_page_key.findData(self._config.page_key)
        self._combo_page_key.setCurrentIndex(max(0, idx))
        self._combo_page_key.setToolTip("每一輪拍攝前，程式自動按下的翻頁鍵")
        self._spin_page_wait = QDoubleSpinBox()
        self._spin_page_wait.setRange(0.0, 60.0)
        self._spin_page_wait.setDecimals(1)
        self._spin_page_wait.setSingleStep(0.5)
        self._spin_page_wait.setSuffix(" 秒")
        self._spin_page_wait.setValue(self._config.page_wait)
        self._spin_page_wait.setToolTip("按下翻頁鍵後等多久再拍（讓畫面載入完成）")
        self._spin_page_countdown = QDoubleSpinBox()
        self._spin_page_countdown.setRange(0.0, 30.0)
        self._spin_page_countdown.setDecimals(0)
        self._spin_page_countdown.setSuffix(" 秒")
        self._spin_page_countdown.setSpecialValueText("不倒數")
        self._spin_page_countdown.setValue(self._config.page_countdown)
        self._spin_page_countdown.setToolTip(
            "按開始後先倒數，讓你點一下要翻頁的視窗\n"
            "用迷你模式開始時工具列不會搶焦點，可以設為不倒數"
        )
        self._page_widgets = [
            self._field_label("翻頁鍵"), self._combo_page_key,
            self._field_label("按鍵後等"), self._spin_page_wait,
            self._field_label("開始倒數"), self._spin_page_countdown,
        ]
        for i, w in enumerate(self._page_widgets):
            grid.addWidget(w, 2 + i // 4, i % 4)

        self._spin_max_frames = QSpinBox()
        self._spin_max_frames.setRange(0, 1000000)
        self._spin_max_frames.setSuffix(" 輪")
        self._spin_max_frames.setSpecialValueText("無限輪")
        self._spin_max_frames.setValue(self._config.max_frames)
        self._spin_max_frames.setToolTip("每一輪會把所有定點各拍一張；0 = 無限")
        self._spin_max_seconds = QDoubleSpinBox()
        self._spin_max_seconds.setRange(0, 86400)
        self._spin_max_seconds.setSuffix(" 秒")
        self._spin_max_seconds.setSpecialValueText("無限時間")
        self._spin_max_seconds.setValue(self._config.max_seconds)
        self._spin_max_seconds.setToolTip("0 = 無限")
        grid.addWidget(self._field_label("上限"), 4, 0)
        grid.addWidget(self._spin_max_frames, 4, 1)
        grid.addWidget(self._field_label("或"), 4, 2)
        grid.addWidget(self._spin_max_seconds, 4, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        for radio in (self._radio_interval, self._radio_fps, self._radio_page):
            radio.toggled.connect(self._on_mode_radio)
        self._on_mode_radio()
        return box

    def _build_output_group(self) -> QGroupBox:
        box = QGroupBox("輸出")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        self._edit_outdir = QLineEdit(self._config.output_dir)
        btn_browse = self._button("瀏覽…")
        btn_browse.clicked.connect(self._browse_outdir)
        grid.addWidget(self._field_label("目錄"), 0, 0)
        grid.addWidget(self._edit_outdir, 0, 1, 1, 3)
        grid.addWidget(btn_browse, 0, 4)

        self._combo_format = QComboBox()
        self._combo_format.addItems(["png", "jpg"])
        self._combo_format.setCurrentText(self._config.file_format)
        self._spin_quality = QSpinBox()
        self._spin_quality.setRange(1, 100)
        self._spin_quality.setPrefix("品質 ")
        self._spin_quality.setValue(self._config.jpeg_quality)
        self._edit_prefix = QLineEdit(self._config.file_prefix)
        self._edit_prefix.setPlaceholderText("檔名前綴")
        grid.addWidget(self._field_label("格式"), 1, 0)
        grid.addWidget(self._combo_format, 1, 1)
        grid.addWidget(self._spin_quality, 1, 2)
        grid.addWidget(self._field_label("前綴"), 1, 3, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._edit_prefix, 1, 4)

        # 檔名格式（可編輯下拉：預設選項 + 自訂）
        self._combo_filename = QComboBox()
        self._combo_filename.setEditable(True)
        self._combo_filename.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for template, desc in FILENAME_PRESETS:
            self._combo_filename.addItem(template)
            self._combo_filename.setItemData(
                self._combo_filename.count() - 1, desc, Qt.ItemDataRole.ToolTipRole
            )
        self._combo_filename.setCurrentText(self._config.filename_template)
        tokens_help = "\n".join(
            f"{{{key}}}　{desc}" for key, (_alias, desc) in FILENAME_TOKENS.items()
        )
        self._combo_filename.setToolTip(
            "檔名格式（不含副檔名），用 / 可建立子資料夾\n\n可用代號：\n" + tokens_help
        )
        btn_tokens = self._button("插入代號")
        menu = QMenu(btn_tokens)
        for key, (_alias, desc) in FILENAME_TOKENS.items():
            act = menu.addAction(f"{{{key}}}　{desc}")
            act.triggered.connect(lambda _=False, k=key: self._insert_filename_token(k))
        menu.addSeparator()
        menu.addAction("/　建立子資料夾").triggered.connect(
            lambda: self._insert_filename_token(None)
        )
        btn_tokens.setMenu(menu)
        btn_tokens.setProperty("hasMenu", True)
        grid.addWidget(self._field_label("檔名"), 2, 0)
        grid.addWidget(self._combo_filename, 2, 1, 1, 3)
        grid.addWidget(btn_tokens, 2, 4)

        self._lbl_filename_example = QLabel()
        self._lbl_filename_example.setObjectName("muted")
        self._lbl_filename_example.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._lbl_filename_example.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self._lbl_filename_example, 3, 1, 1, 4)
        self._combo_filename.editTextChanged.connect(self._update_filename_example)
        self._edit_prefix.textChanged.connect(self._update_filename_example)
        self._combo_format.currentTextChanged.connect(self._update_filename_example)
        self._edit_outdir.textChanged.connect(self._update_filename_example)

        self._check_snap = QCheckBox("選取時貼齊視窗邊緣")
        self._check_snap.setChecked(self._config.snap_enabled)
        grid.addWidget(self._check_snap, 4, 1, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return box

    def _build_control_group(self) -> QWidget:
        bar = QGroupBox()
        lay = QHBoxLayout(bar)
        lay.setSpacing(8)

        self._btn_start = self._button(
            "▶  開始擷取", "primary", "開始擷取（Ctrl+Alt+S；擷取中再按一次停止）"
        )
        self._btn_start.setMinimumWidth(150)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause = self._button("❚❚  暫停", tip="暫停 / 恢復（Ctrl+Alt+P）")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_pause.setEnabled(False)
        self._btn_stop = self._button("■  停止", "danger", "停止（Ctrl+Alt+S）")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        self._btn_manual = self._button(
            "＋  手動加拍", tip="在連續擷取中立即加拍一輪（全域快捷鍵 F9）"
        )
        self._btn_manual.clicked.connect(self._on_manual)
        self._btn_manual.setEnabled(False)
        self._btn_single = self._button(
            "◎  立即單張", tip="立即把所有定點各擷取一張並儲存（Ctrl+S；全域快捷鍵 F8）"
        )
        self._btn_single.clicked.connect(self._on_single)

        for b in (self._btn_start, self._btn_pause, self._btn_stop):
            lay.addWidget(b)
        lay.addStretch(1)
        lay.addWidget(self._btn_manual)
        lay.addWidget(self._btn_single)
        return bar

    def _build_status_group(self) -> QGroupBox:
        box = QGroupBox("即時預覽（選中的定點）")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)
        self._live = self._make_preview("尚未開始擷取")
        lay.addWidget(self._live)
        self._lbl_stats = QLabel("共 0 張")
        self._lbl_stats.setObjectName("muted")
        self._lbl_stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        self.add_req.connect(self._on_add_region)
        self.point_req.connect(self._on_single_point)
        self.toggle_req.connect(self._on_toggle_capture)
        self.pause_toggle_req.connect(self._on_pause)
        m = self._mini
        m.start_req.connect(self._on_start)
        m.pause_req.connect(self._on_pause)
        m.stop_req.connect(self._on_stop)
        m.manual_req.connect(self._on_manual)
        m.single_req.connect(self._on_single)
        m.point_req.connect(self._on_single_point)
        m.expand_req.connect(self._exit_mini)

    # ------------------------------------------------------------------
    # 定點清單
    # ------------------------------------------------------------------
    def _current_region(self) -> Optional[Region]:
        if 0 <= self._current < len(self._regions):
            return self._regions[self._current]
        return None

    @staticmethod
    def _region_label(index: int, r: Region) -> str:
        # 前 9 個定點標出對應的 Ctrl+Alt+數字鍵
        key = f"[{index + 1}] " if index < 9 else "　　"
        return f"{key}{r.name}　{r.width}x{r.height} @({r.left},{r.top})"

    def _refresh_list(self) -> None:
        """依 self._regions 重建清單顯示，並維持目前選取列。"""
        self._list.blockSignals(True)
        self._list.clear()
        for i, r in enumerate(self._regions):
            item = QListWidgetItem(self._region_label(i, r))
            item.setData(Qt.ItemDataRole.UserRole, i)   # 拖曳後用來還原新順序
            self._list.addItem(item)
        self._list.setCurrentRow(self._current)
        self._list.blockSignals(False)
        self._update_list_buttons()

    def _update_list_buttons(self) -> None:
        has = self._current_region() is not None
        for w in (self._btn_reselect, self._btn_rename, self._btn_delete):
            w.setEnabled(has)
        self._btn_up.setEnabled(has and self._current > 0)
        self._btn_down.setEnabled(has and self._current < len(self._regions) - 1)

    def _move_region(self, offset: int) -> None:
        """把目前定點上移 (-1) / 下移 (+1) 一格。"""
        i, j = self._current, self._current + offset
        if self._current_region() is None or not 0 <= j < len(self._regions):
            return
        self._regions[i], self._regions[j] = self._regions[j], self._regions[i]
        self._current = j
        self._refresh_list()
        self._regions_changed()

    def _on_rows_moved(self) -> None:
        """清單被拖曳重新排序後，依項目上的原索引重排 self._regions。"""
        order = [
            self._list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._list.count())
        ]
        # 清單與資料一致（或拖放尚未完成）時不處理
        identity = list(range(len(self._regions)))
        if order == identity or None in order or sorted(order) != identity:
            return
        self._regions = [self._regions[i] for i in order]
        self._current = self._list.currentRow()
        self._refresh_list()          # 重新編號 [N] 與原索引
        self._regions_changed()

    def _update_list_label(self, index: int) -> None:
        item = self._list.item(index)
        if item is not None:
            item.setText(self._region_label(index, self._regions[index]))

    def _on_list_row_changed(self, row: int) -> None:
        self._current = row
        self._update_list_buttons()
        self._load_current_to_spins()
        self.refresh_preview()
        self._update_filename_example()

    def _load_current_to_spins(self) -> None:
        region = self._current_region() or Region()
        spins = (self._spin_l, self._spin_t, self._spin_w, self._spin_h)
        for s, v in zip(spins, (region.left, region.top, region.width, region.height)):
            s.blockSignals(True)
            s.setValue(v)
            s.setEnabled(self._current_region() is not None)
            s.blockSignals(False)

    def _regions_changed(self) -> None:
        """定點清單有變動：同步設定、執行中的引擎，並存檔。"""
        self._config.regions = list(self._regions)
        self._controller.set_regions(self._regions)
        self._config.save()
        self._update_filename_example()
        self._mini.set_regions(self._regions)

    def _unique_name(self) -> str:
        used = {r.name for r in self._regions}
        i = len(self._regions)
        while default_region_name(i) in used:
            i += 1
        return default_region_name(i)

    def _run_selector(self, initial: Optional[Region], others: List[Region]) -> Optional[Region]:
        """開啟全螢幕選取遮罩並等待結果（主視窗暫時隱藏）。"""
        self._update_config_from_ui()
        self.hide()
        self._mini.hide()
        QApplication.processEvents()
        sel = RegionSelector(self._config, initial=initial, others=others)
        sel.start()
        loop = QEventLoop()
        sel.finished.connect(lambda r: loop.quit())
        sel.destroyed.connect(loop.quit)
        loop.exec()
        region = sel.result()
        sel.deleteLater()
        if self._is_mini:
            self._mini.show()
        else:
            self.show()
        if region is not None and region.is_valid():
            return region
        return None

    def _on_add_region(self) -> None:
        region = self._run_selector(None, self._regions)
        if region is None:
            return
        self._regions.append(replace(region, name=self._unique_name()))
        self._current = len(self._regions) - 1
        self._refresh_list()
        self._load_current_to_spins()
        self.refresh_preview()
        self._regions_changed()

    def _on_select_region(self) -> None:
        """重新選取目前定點（沒有任何定點時改為新增）。"""
        cur = self._current_region()
        if cur is None:
            self._on_add_region()
            return
        others = [r for i, r in enumerate(self._regions) if i != self._current]
        region = self._run_selector(cur, others)
        if region is None:
            return
        self._set_current_geometry(region.left, region.top, region.width, region.height)
        self._load_current_to_spins()

    def _on_rename_region(self) -> None:
        cur = self._current_region()
        if cur is None:
            return
        name, ok = QInputDialog.getText(
            self, "定點名稱", "名稱（會用在檔名中）：", text=cur.name
        )
        name = name.strip()
        if not ok or not name or name == cur.name:
            return
        if any(r.name == name for r in self._regions):
            QMessageBox.warning(self, "提示", f"已有名為「{name}」的定點。")
            return
        self._regions[self._current] = replace(cur, name=name)
        self._update_list_label(self._current)
        self._regions_changed()

    def _on_delete_region(self) -> None:
        if self._current_region() is None:
            return
        del self._regions[self._current]
        self._current = min(self._current, len(self._regions) - 1)
        self._refresh_list()
        self._load_current_to_spins()
        self.refresh_preview()
        self._regions_changed()

    def _set_current_geometry(self, l: int, t: int, w: int, h: int) -> None:
        """更新目前定點的座標（以新物件取代，避免與擷取執行緒共用可變物件）。"""
        cur = self._current_region()
        if cur is None:
            return
        self._regions[self._current] = replace(cur, left=l, top=t, width=w, height=h)
        self._update_list_label(self._current)
        self.refresh_preview()
        self._regions_changed()

    def _on_region_num_changed(self) -> None:
        cur = self._current_region()
        if cur is None:
            return
        self._regions[self._current] = replace(
            cur,
            left=self._spin_l.value(),
            top=self._spin_t.value(),
            width=self._spin_w.value(),
            height=self._spin_h.value(),
        )
        self._update_list_label(self._current)
        self._controller.set_regions(self._regions)
        self.refresh_preview()

    def _nudge(self, direction: str) -> None:
        cur = self._current_region()
        if cur is None:
            return
        step = int(self._step_combo.currentText().rstrip("px"))
        l, t, w, h = cur
        if direction == "left":
            l -= step
        elif direction == "right":
            l += step
        elif direction == "up":
            t -= step
        elif direction == "down":
            t += step
        self._set_current_geometry(l, t, w, h)
        self._load_current_to_spins()

    def refresh_preview(self) -> None:
        """把目前選中的定點交給背景即時預覽（約每 LIVE_PREVIEW_MS 更新一次）。"""
        region = self._current_region()
        if region is None or not region.is_valid():
            self._live_preview.set_region(None)
            self._preview.setText("尚未選取區域\n按「＋ 新增定點」開始")
            return
        self._live_preview.set_region(region)

    def _on_live_preview_frame(self, img: QImage, region: tuple) -> None:
        cur = self._current_region()
        if cur is None or tuple(cur) != region:
            return   # 已切換到別的定點，丟掉舊區域的畫面
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(self._preview.devicePixelRatioF())
        self._preview.setPixmap(pix)

    def _update_live_preview_size(self) -> None:
        """縮圖目標尺寸 = 預覽框內容大小 × DPR（物理像素，保持清晰）。"""
        if not hasattr(self, "_preview"):
            return   # UI 尚未建立
        dpr = self._preview.devicePixelRatioF()
        size = self._preview.contentsRect().size()
        self._live_preview.set_target_size(
            round((size.width() - 2) * dpr), round((size.height() - 2) * dpr)
        )

    # 視窗隱藏（框選中）或最小化時暫停即時預覽
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_live_preview_size()
        self._live_preview.set_active(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._live_preview.set_active(False)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._live_preview.set_active(self.isVisible() and not self.isMinimized())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_live_preview_size()

    @staticmethod
    def _show_image(label: QLabel, img: QImage) -> None:
        """把影像等比縮放到預覽框大小（依螢幕 DPR 以物理解析度縮放，保持清晰）。"""
        dpr = label.devicePixelRatioF()
        size = label.contentsRect().size()
        scaled = img.scaled(
            max(1, round((size.width() - 2) * dpr)),
            max(1, round((size.height() - 2) * dpr)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix = QPixmap.fromImage(scaled)
        pix.setDevicePixelRatio(dpr)
        label.setPixmap(pix)

    # ------------------------------------------------------------------
    # 擷取控制
    # ------------------------------------------------------------------
    def _update_config_from_ui(self) -> None:
        c = self._config
        c.regions = list(self._regions)
        if self._radio_page.isChecked():
            c.capture_mode = "page"
        elif self._radio_fps.isChecked():
            c.capture_mode = "fps"
        else:
            c.capture_mode = "interval"
        c.page_key = self._combo_page_key.currentData()
        c.page_wait = self._spin_page_wait.value()
        c.page_countdown = self._spin_page_countdown.value()
        c.interval_seconds = self._spin_interval.value()
        c.fps = self._spin_fps.value()
        c.max_frames = self._spin_max_frames.value()
        c.max_seconds = self._spin_max_seconds.value()
        c.output_dir = self._edit_outdir.text().strip() or c.output_dir
        c.file_format = self._combo_format.currentText()
        c.jpeg_quality = self._spin_quality.value()
        c.file_prefix = self._edit_prefix.text().strip() or "capture"
        if check_filename_template(self._filename_template()) is None:
            c.filename_template = self._filename_template()
        c.snap_enabled = self._check_snap.isChecked()

    def _filename_template(self) -> str:
        return self._combo_filename.currentText().strip()

    def _insert_filename_token(self, key: Optional[str]) -> None:
        """在檔名格式游標處插入 {代號}（key 為 None 時插入 /）。"""
        edit = self._combo_filename.lineEdit()
        edit.insert("/" if key is None else f"{{{key}}}")
        edit.setFocus()

    def _update_filename_example(self) -> None:
        """依目前設定顯示範例檔名；範本有誤時以紅字提示。"""
        template = self._filename_template()
        error = check_filename_template(template)
        if error:
            self._lbl_filename_example.setText(f"⚠ {error}")
            self._lbl_filename_example.setStyleSheet("color: #ef5b5b;")
            return
        region = self._current_region() or (self._regions[0] if self._regions else None)
        index = self._current if self._current_region() is not None else 0
        rel = render_filename(
            template,
            prefix=self._edit_prefix.text().strip() or "capture",
            name=region.name if region is not None else default_region_name(0),
            number=index + 1,
            when=datetime.now(),
            round_label="0001",
        )
        ext = "jpg" if self._combo_format.currentText() in ("jpg", "jpeg") else "png"
        if FILENAME_SEQ_MARK in rel:
            # 範例顯示該資料夾實際的下一個序號
            outdir = self._edit_outdir.text().strip() or self._config.output_dir
            seq = peek_folder_seq(os.path.join(outdir, rel), ext)
            rel = rel.replace(FILENAME_SEQ_MARK, f"{seq:04d}")
        example = f"例：{rel.replace(os.sep, '/')}.{ext}"
        self._lbl_filename_example.setText(example)
        self._lbl_filename_example.setToolTip(example)
        self._lbl_filename_example.setStyleSheet("")

    def _check_filename_template(self) -> bool:
        error = check_filename_template(self._filename_template())
        if error is None:
            return True
        QMessageBox.warning(self, "檔名格式有誤", error)
        return False

    def _has_valid_region(self) -> bool:
        if any(r.is_valid() for r in self._regions):
            return True
        QMessageBox.warning(self, "提示", "請先新增至少一個要擷取的定點。")
        return False

    def _on_start(self) -> None:
        self._update_config_from_ui()
        if not self._has_valid_region() or not self._check_filename_template():
            return
        self._config.save()
        self._controller.start(self._regions, self._config)
        self._set_running_ui(True)

    def _on_pause(self) -> None:
        if self._controller.running:
            if not self._paused:
                self._controller.pause()
                self._btn_pause.setText("▶  恢復")
            else:
                self._controller.resume()
                self._btn_pause.setText("❚❚  暫停")
            self._paused = not self._paused
            self._set_pill("paused" if self._paused else "running")

    def _on_toggle_capture(self) -> None:
        """Ctrl+Alt+S：沒在擷取就開始，擷取中（含倒數）就停止。"""
        if self._controller.running:
            self._on_stop()
        else:
            self._on_start()

    def _on_stop(self) -> None:
        self._controller.stop()

    def _on_manual(self) -> None:
        self._controller.capture_now()

    def _on_single(self) -> None:
        """立即把所有定點各擷取一張並儲存。"""
        self._update_config_from_ui()
        if not self._has_valid_region() or not self._check_filename_template():
            return
        saved = self._save_singles(range(len(self._regions)), preview_index=self._current)
        if saved is not None:
            self._lbl_stats.setText(f"已儲存單張 {saved} 張（{saved} 個定點）")
            self._mini.set_status(f"已存 {saved} 張")
            self._lbl_stats.setToolTip(f"輸出目錄：{self._config.output_dir}")

    def _on_single_point(self, index: int) -> None:
        """只擷取第 index 個定點（0 起算）一張並儲存（Ctrl+Alt+數字鍵）。"""
        if not 0 <= index < len(self._regions) or not self._regions[index].is_valid():
            self._lbl_stats.setText(f"沒有第 {index + 1} 個定點")
            return
        self._update_config_from_ui()
        if not self._check_filename_template():
            return
        if self._save_singles([index], preview_index=index) is not None:
            self._lbl_stats.setText(
                f"已儲存定點 {index + 1}「{self._regions[index].name}」單張"
            )
            self._mini.set_status(f"已存 [{index + 1}]")
            self._lbl_stats.setToolTip(f"輸出目錄：{self._config.output_dir}")

    def _save_singles(self, indices, preview_index: int) -> Optional[int]:
        """擷取指定定點各一張並儲存；回傳儲存張數，失敗回傳 None。"""
        try:
            when = datetime.now()
            saved = 0
            with mss.mss() as sct:
                for i in indices:
                    region = self._regions[i]
                    if not region.is_valid():
                        continue
                    img = grab_region(sct, region)
                    save_image(self._config, img,
                               region_file_path(self._config, region, i, when, "single"))
                    saved += 1
                    if i == preview_index:
                        self._show_image(self._live, img)
            return saved
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "錯誤", f"擷取失敗：{exc}")
            return None

    # ------------------------------------------------------------------
    # 擷取引擎訊號
    # ------------------------------------------------------------------
    def _on_frame(self, img: QImage, index: int, region_idx: int, path: str) -> None:
        # 即時預覽只顯示清單中選中的定點
        if region_idx != self._current:
            return
        self._show_image(self._live, img)

    def _on_stats(self, stats: dict) -> None:
        if "error" in stats:
            self._lbl_stats.setText(f"錯誤：{stats['error']}")
            self._lbl_stats.setToolTip(stats["error"])
            self._mini.set_status("錯誤")
            self._mini.setToolTip(stats["error"])
            return
        if "countdown" in stats:
            n = round(stats["countdown"])
            self._lbl_stats.setText(f"{n} 秒後開始翻頁連拍…請點一下要翻頁的視窗")
            self._mini.set_status(f"{n}…")
            return
        frames = stats.get("frames", 0)
        images = stats.get("images", 0)
        regions = stats.get("regions", 0)
        bytesw = stats.get("bytes", 0)
        elapsed = stats.get("elapsed", 0)
        ms = stats.get("capture_ms", 0)
        self._lbl_stats.setText(
            f"第 {frames} 輪（{regions} 個定點）・ 共 {images} 張 ・ "
            f"{self._fmt_bytes(bytesw)} ・ 耗時 {elapsed:.1f}s ・ 每輪抓取 {ms:.1f}ms"
        )
        self._mini.set_status(f"第 {frames} 輪")

    def _on_state(self, running: bool) -> None:
        self._set_running_ui(running)

    def _set_running_ui(self, running: bool) -> None:
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(running)
        self._btn_manual.setEnabled(running)
        self._btn_single.setEnabled(True)
        if not running:
            self._paused = False
            self._btn_pause.setText("❚❚  暫停")
        self._set_pill("running" if running else "idle")

    def _set_pill(self, state: str) -> None:
        """更新標頭的狀態膠囊：idle / running / paused。"""
        text = {"idle": "● 待機", "running": "● 擷取中", "paused": "● 已暫停"}[state]
        self._pill.setText(text)
        self._pill.setProperty("state", state)
        repolish(self._pill)
        self._mini.set_state(state)

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
        # 只顯示目前模式用得到的欄位，讓卡片保持精簡
        interval = self._radio_interval.isChecked()
        fps = self._radio_fps.isChecked()
        page = self._radio_page.isChecked()
        self._lbl_interval.setVisible(interval)
        self._spin_interval.setVisible(interval)
        self._lbl_fps.setVisible(fps)
        self._spin_fps.setVisible(fps)
        for w in self._page_widgets:
            w.setVisible(page)

    def _setup_shortcuts(self) -> None:
        """視窗內快捷鍵（視窗有焦點時有效）。"""
        # Ctrl+S → 立即單張
        self._sc_single = QShortcut(QKeySequence("Ctrl+S"), self)
        self._sc_single.activated.connect(self._on_single)
        # Ctrl+R → 重新選取目前定點（開遮罩）
        self._sc_select = QShortcut(QKeySequence("Ctrl+R"), self)
        self._sc_select.activated.connect(self._on_select_region)
        # F1 → 快捷鍵一覽
        self._sc_help = QShortcut(QKeySequence("F1"), self)
        self._sc_help.activated.connect(self._show_shortcuts)
        # Ctrl+M → 迷你模式
        self._sc_mini = QShortcut(QKeySequence("Ctrl+M"), self)
        self._sc_mini.activated.connect(self._enter_mini)
        # 若全域 keyboard 不可用，於視窗內補 F6 新增定點 / F9 手動加拍
        if not self._has_keyboard:
            self._sc_add = QShortcut(QKeySequence("F6"), self)
            self._sc_add.activated.connect(self._on_add_region)
            self._sc_manual = QShortcut(QKeySequence("F9"), self)
            self._sc_manual.activated.connect(self._on_manual)
            # Ctrl+Alt+S → 開始 / 停止；Ctrl+Alt+P → 暫停 / 恢復
            self._sc_toggle = QShortcut(QKeySequence("Ctrl+Alt+S"), self)
            self._sc_toggle.activated.connect(self._on_toggle_capture)
            self._sc_pause = QShortcut(QKeySequence("Ctrl+Alt+P"), self)
            self._sc_pause.activated.connect(self._on_pause)
            # Ctrl+Alt+1~9 → 只拍第 N 個定點
            self._sc_points = []
            for n in range(9):
                sc = QShortcut(QKeySequence(f"Ctrl+Alt+{n + 1}"), self)
                sc.activated.connect(lambda n=n: self._on_single_point(n))
                self._sc_points.append(sc)

    def _show_shortcuts(self) -> None:
        """在「?」按鈕下方顯示快捷鍵表（點外面或 Esc 關閉）。"""
        ShortcutsPopup(self, COLORS, self._has_keyboard).show_below(self._btn_help)

    def _try_enable_string_keyboard(self) -> None:
        """啟用全域快速鍵（若 keyboard 可用，且使用者不介意管理員權限）。

        全域動作：
          F6 → 新增定點（開全螢幕遮罩）
          F7 → 重新選取目前定點（沒有定點時新增）
          F8 → 立即單張（所有定點各一張，無論是否在連續擷取中）
          F9 → 手動加拍一輪（連續擷取運行中）
          Ctrl+Alt+1~9 → 只拍第 1~9 個定點一張
          Ctrl+Alt+S → 開始擷取 / 停止
          Ctrl+Alt+P → 暫停 / 恢復
        """
        try:
            import keyboard  # type: ignore

            self._has_keyboard = True
            keyboard.add_hotkey("f6", lambda: self.add_req.emit())
            keyboard.add_hotkey("f7", lambda: self.select_req.emit())
            keyboard.add_hotkey("f8", lambda: self.single_req.emit())
            keyboard.add_hotkey("f9", lambda: self.manual_req.emit())
            keyboard.add_hotkey("ctrl+alt+s", lambda: self.toggle_req.emit())
            keyboard.add_hotkey("ctrl+alt+p", lambda: self.pause_toggle_req.emit())
            for n in range(9):
                keyboard.add_hotkey(f"ctrl+alt+{n + 1}", lambda n=n: self.point_req.emit(n))
        except Exception:
            self._has_keyboard = False

    # ------------------------------------------------------------------
    # 迷你模式
    # ------------------------------------------------------------------
    def _enter_mini(self) -> None:
        """收起主視窗，改顯示浮動迷你工具列。"""
        if self._is_mini:
            return
        self._is_mini = True
        self._mini.adjustSize()
        self._mini.move(self._mini_position())
        self.hide()
        self._mini.show()
        self._mini.raise_()

    def _exit_mini(self) -> None:
        """從迷你工具列回到主視窗。"""
        if not self._is_mini:
            return
        self._is_mini = False
        pos = self._mini.pos()
        self._config.mini_pos = [pos.x(), pos.y()]
        self._config.save()
        self._mini.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def _mini_position(self) -> QPoint:
        """上次的位置（仍在某個螢幕內才用），否則放在主視窗所在螢幕的上方中央。"""
        saved = self._config.mini_pos
        if isinstance(saved, list) and len(saved) == 2:
            pt = QPoint(int(saved[0]), int(saved[1]))
            if QGuiApplication.screenAt(pt + QPoint(20, 10)) is not None:
                return pt
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(area.center().x() - self._mini.width() // 2, area.top() + 12)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._mini.hide()
        self._live_preview.stop()
        self._controller.stop()
        if self._has_keyboard:
            try:
                import keyboard  # type: ignore
                keyboard.unhook_all()
            except Exception:
                pass
        self._update_config_from_ui()
        self._config.save()
        super().closeEvent(event)
