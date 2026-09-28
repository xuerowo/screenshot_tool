"""
shortcuts_help.py
=================
快捷鍵一覽彈出視窗（主視窗標頭的「?」按鈕 / F1）。

快捷鍵清單集中在 SHORTCUTS，新增或修改快捷鍵時只要改這裡。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# (分組標題, 分組說明, [(按鍵, 動作), ...])；按鍵中的「+」會拆成多個鍵帽
SHORTCUTS = [
    ("全域", "在任何程式中都有效（需 keyboard 模組）", [
        ("Ctrl+Alt+S", "開始擷取 / 停止"),
        ("Ctrl+Alt+P", "暫停 / 恢復"),
        ("F8", "立即單張（所有定點）"),
        ("Ctrl+Alt+1~9", "只拍第 N 個定點"),
        ("F9", "手動加拍一輪"),
        ("F6", "新增定點"),
        ("F7", "重新選取目前定點"),
    ]),
    ("視窗內", "主視窗在最前面時", [
        ("Ctrl+S", "立即單張"),
        ("Ctrl+R", "重新選取目前定點"),
        ("Ctrl+M", "迷你模式"),
        ("F1", "顯示這個快捷鍵表"),
    ]),
    ("選取遮罩內", "框選定點時", [
        ("Enter", "確認"),
        ("Esc", "取消"),
        ("方向鍵", "微調游標 1px（Shift：10px）"),
        ("Ctrl+方向鍵", "調整單邊 1px"),
        ("+ / -", "放大鏡倍率"),
        ("H", "隱藏 / 顯示操作面板"),
    ]),
]

_STYLE = """
QFrame#shortcuts {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 12px;
}}
QLabel#group {{
    color: {text};
    font-weight: 600;
}}
QLabel#groupNote, QLabel#note {{
    color: {muted};
}}
QLabel#kbd {{
    background: {surface3};
    color: {text};
    border: 1px solid #454b57;
    border-bottom-width: 2px;
    border-radius: 5px;
    padding: 0px 6px;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QLabel#plus {{
    color: {muted};
}}
QFrame#rule {{
    background: {border};
    max-height: 1px;
    min-height: 1px;
}}
"""


def _key_caps(keys: str) -> QWidget:
    """把 "Ctrl+Alt+S" 做成 [Ctrl] + [Alt] + [S] 的鍵帽列。"""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    parts = [keys] if keys.strip() in ("+ / -",) else keys.split("+")
    for i, part in enumerate(parts):
        if i:
            plus = QLabel("+")
            plus.setObjectName("plus")
            lay.addWidget(plus)
        cap = QLabel(part.strip())
        cap.setObjectName("kbd")
        lay.addWidget(cap)
    lay.addStretch(1)
    return box


class ShortcutsPopup(QFrame):
    """點外面或按 Esc 即關閉的快捷鍵表。"""

    def __init__(self, parent: QWidget, colors: dict, global_enabled: bool):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("shortcuts")
        card.setStyleSheet(_STYLE.format(**colors))
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(6)
        key_cells = []
        for n, (title, note, rows) in enumerate(SHORTCUTS):
            if n:
                rule = QFrame()
                rule.setObjectName("rule")
                lay.addSpacing(4)
                lay.addWidget(rule)
                lay.addSpacing(2)
            head = QHBoxLayout()
            group = QLabel(title)
            group.setObjectName("group")
            group_note = QLabel(note)
            group_note.setObjectName("groupNote")
            head.addWidget(group)
            head.addSpacing(6)
            head.addWidget(group_note)
            head.addStretch(1)
            lay.addLayout(head)

            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(5)
            for r, (keys, action) in enumerate(rows):
                caps = _key_caps(keys)
                key_cells.append(caps)
                grid.addWidget(caps, r, 0)
                grid.addWidget(QLabel(action), r, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)

        # 各分組的鍵帽欄同寬，讓「動作」欄上下對齊
        width = max(c.sizeHint().width() for c in key_cells)
        for c in key_cells:
            c.setFixedWidth(width)

        if not global_enabled:
            warn = QLabel("⚠ 未載入 keyboard 模組：「全域」快捷鍵目前只在主視窗有焦點時有效")
            warn.setObjectName("note")
            warn.setWordWrap(True)
            lay.addSpacing(4)
            lay.addWidget(warn)

    def show_below(self, anchor: QWidget) -> None:
        """顯示在 anchor 下方、右緣對齊（超出螢幕時往內收）。"""
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        screen = anchor.screen().availableGeometry()
        pos.setX(max(screen.left() + 8, min(pos.x(), screen.right() - self.width() - 8)))
        pos.setY(max(screen.top() + 8, min(pos.y(), screen.bottom() - self.height() - 8)))
        self.move(pos)
        self.show()
