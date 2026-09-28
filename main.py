"""
main.py
=======
進入點。務必「先」設定 Per-Monitor V2 DPI 感知（讓 Qt 與 mss 都使用物理像素），
再建立 QApplication 與主視窗。
"""
from __future__ import annotations

import sys

import winapi


def main() -> int:
    # 1) 最重要的一步：在建立任何 Qt 物件「之前」設定 DPI 感知，
    #    讓座標與螢幕實際像素 1:1，確保選取區域精準對齊。
    aware = winapi.set_dpi_awareness()

    # 2) 建立 QApplication（此時 DPI 感知已生效）
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from config import Config
    from app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("RegionShot")
    app.setOrganizationName("RegionShot")

    from theme import apply_theme
    apply_theme(app)

    if not aware:
        QMessageBox.warning(
            None,
            "DPI 感知",
            "無法啟用 Per-Monitor V2 DPI 感知，\n"
            "在高縮放螢幕上區域可能無法精準對齊。",
        )

    config = Config.load()
    win = MainWindow(config)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
