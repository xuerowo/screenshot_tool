"""
theme.py
========
現代深色主題（Qt Style Sheet）。所有顏色集中在 COLORS，改色只需改這裡。

按鈕可用動態屬性 `variant` 切換樣式：
    btn.setProperty("variant", "primary")   # 主要動作（強調色）
    btn.setProperty("variant", "danger")    # 危險動作（紅）
    btn.setProperty("variant", "ghost")     # 次要、無框
QLabel 物件名稱：
    "preview"  → 預覽框、"muted" → 次要文字、"title"/"subtitle" → 標頭、"pill" → 狀態膠囊
"""
from __future__ import annotations

import os

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QStyleFactory

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets").replace("\\", "/")

COLORS = {
    "bg": "#15171c",
    "surface": "#1d2027",
    "surface2": "#262a33",
    "surface3": "#30353f",
    "border": "#2f343e",
    "text": "#e7e9ee",
    "muted": "#8b93a1",
    "accent": "#4f8cff",
    "accent_hover": "#6b9fff",
    "accent_press": "#3f76e0",
    "danger": "#ef5b5b",
    "danger_hover": "#f47474",
    "success": "#3ecf8e",
    "preview": "#0e1014",
}

QSS = """
* {{
    color: {text};
    outline: none;
}}
QMainWindow, QDialog, QMessageBox, QInputDialog {{
    background: {bg};
}}
QWidget#central {{
    background: {bg};
}}
QToolTip {{
    background: {surface3};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 8px;
}}

QMenu {{
    background: {surface2};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {accent};
    color: #ffffff;
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 8px;
}}
QPushButton::menu-indicator {{
    image: url({assets}/chevron-down.svg);
    subcontrol-position: right center;
    subcontrol-origin: padding;
    right: 8px;
    width: 10px;
    height: 10px;
}}
QPushButton[hasMenu="true"] {{
    padding-right: 26px;
}}

/* ---------- 標頭 ---------- */
QLabel#title {{
    font-size: 14pt;
    font-weight: 600;
}}
QLabel#subtitle, QLabel#muted {{
    color: {muted};
}}
QLabel#pill {{
    background: {surface2};
    color: {muted};
    border: 1px solid {border};
    border-radius: 11px;
    padding: 2px 10px;
    font-weight: 600;
}}
QLabel#pill[state="running"] {{
    background: rgba(62, 207, 142, 0.14);
    color: {success};
    border-color: rgba(62, 207, 142, 0.45);
}}
QLabel#pill[state="paused"] {{
    background: rgba(255, 184, 77, 0.14);
    color: #ffb84d;
    border-color: rgba(255, 184, 77, 0.45);
}}

/* ---------- 卡片 ---------- */
/* 卡片內距由 layout 的 contentsMargins 控制（見 CARD_MARGINS），才會計入最小尺寸 */
QGroupBox {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 12px;
    margin-top: 0px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 12px;
    top: 9px;
    color: {muted};
    font-weight: 600;
}}
QGroupBox QLabel {{
    font-weight: normal;
}}

/* ---------- 預覽 ---------- */
QLabel#preview {{
    background: {preview};
    border: 1px solid {border};
    border-radius: 10px;
    color: {muted};
}}

/* ---------- 按鈕 ---------- */
QPushButton {{
    background: {surface2};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 4px 11px;
    font-weight: normal;
}}
QPushButton:hover {{
    background: {surface3};
}}
QPushButton:pressed {{
    background: {border};
}}
QPushButton:disabled {{
    color: #5a616d;
    background: {surface};
}}
QPushButton[variant="primary"] {{
    background: {accent};
    border: 1px solid {accent};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background: {accent_press};
}}
QPushButton[variant="primary"]:disabled {{
    background: rgba(79, 140, 255, 0.25);
    border-color: transparent;
    color: rgba(255, 255, 255, 0.45);
}}
QPushButton[variant="danger"] {{
    color: {danger};
    border-color: rgba(239, 91, 91, 0.45);
    background: rgba(239, 91, 91, 0.10);
}}
QPushButton[variant="danger"]:hover {{
    background: rgba(239, 91, 91, 0.20);
}}
QPushButton[variant="danger"]:disabled {{
    color: #5a616d;
    border-color: {border};
    background: {surface};
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    border-color: transparent;
    color: {muted};
}}
QPushButton[variant="ghost"]:hover {{
    background: {surface2};
    color: {text};
}}
QPushButton[variant="ghost"]:disabled {{
    color: #4a505a;
}}

QPushButton#help {{
    padding: 0;
    border-radius: 13px;
    background: transparent;
    color: {muted};
    font-weight: 600;
}}
QPushButton#help:hover {{
    background: {surface2};
    color: {text};
}}

/* ---------- 輸入 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {surface2};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 2px 6px;
    min-height: 20px;
    selection-background-color: {accent};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: #3d4350;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: #5a616d;
    background: {surface};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    border: none;
    background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    margin: 3px 3px 0 0;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin: 0 3px 3px 0;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {surface3};
    border-radius: 4px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({assets}/chevron-up.svg);
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({assets}/chevron-down.svg);
    width: 10px;
    height: 10px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: url({assets}/chevron-down.svg);
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background: {surface2};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {accent};
}}

/* ---------- 單選 / 核取 ---------- */
QRadioButton, QCheckBox {{
    spacing: 8px;
    font-weight: normal;
}}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    background: {surface2};
    border: 1px solid #454b57;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator {{
    border-radius: 5px;
}}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
    border-color: {accent};
}}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 #ffffff, stop:0.35 #ffffff,
                                stop:0.45 {accent}, stop:1 {accent});
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: url({assets}/check.svg);
}}

/* ---------- 清單 ---------- */
QListWidget {{
    background: {surface2};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 4px 8px;
    border-radius: 6px;
    margin: 1px 0;
}}
QListWidget::item:hover {{
    background: {surface3};
}}
QListWidget::item:selected {{
    background: rgba(79, 140, 255, 0.22);
    color: {text};
}}

/* ---------- 捲軸 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {surface3};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
"""


# 卡片（QGroupBox）內距：左、上（含標題）、右、下
CARD_MARGINS = (12, 32, 12, 12)
CARD_MARGINS_UNTITLED = (10, 8, 10, 8)


def stylesheet() -> str:
    return QSS.format(assets=ASSETS, **COLORS)


def apply_theme(app: QApplication) -> None:
    """套用 Fusion 基底 + 深色調色盤 + 樣式表 + 字型。"""
    app.setStyle(QStyleFactory.create("Fusion"))

    pal = QPalette()
    c = COLORS
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["surface2"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["surface2"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface3"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    app.setPalette(pal)

    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft JhengHei UI"])
    font.setPointSize(9)
    app.setFont(font)

    app.setStyleSheet(stylesheet())


def repolish(widget) -> None:
    """動態屬性改變後重新套用樣式（例如狀態膠囊切換顏色）。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
