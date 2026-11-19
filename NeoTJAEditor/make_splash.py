"""起動の札(splash.png)を作る。exe の起動直後(Python が動く前)に
PyInstaller の Splash が出す絵。

実行: アプリのフォルダで `.venv/Scripts/python.exe make_splash.py`
字は Segoe UI / Yu Gothic UI(配る絵に市販の字体は焼かない)。
"""
import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

W, H = 640, 300
NAVY = QColor("#0f1a2e")
ACCENT = QColor("#4f9cf9")


def font(px, weight):
    f = QFont("Segoe UI")
    f.setFamilies(["Segoe UI", "Yu Gothic UI"])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def make(path):
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    card = QPainterPath()
    card.addRect(QRectF(0, 0, W, H))   # 角は丸めない(Splash の窓は透過を当てにしない)
    p.fillPath(card, NAVY)
    a, b = "NeoTJA", "Launcher"
    fa, fb = font(54, QFont.Light), font(54, QFont.DemiBold)
    wa = QFontMetricsF(fa).horizontalAdvance(a)
    wb = QFontMetricsF(fb).horizontalAdvance(b)
    x = (W - wa - wb) / 2
    base = H / 2 + 8
    p.setFont(fa)
    p.setPen(QColor("#ffffff"))
    p.drawText(int(x), int(base), a)
    p.setFont(fb)
    p.setPen(ACCENT)
    p.drawText(int(x + wa), int(base), b)
    p.setFont(font(18, QFont.Normal))
    p.setPen(QColor(255, 255, 255, 150))
    p.drawText(QRectF(0, base + 18, W, 30), Qt.AlignHCenter | Qt.AlignTop, "Editor & Player")
    p.end()
    img.save(path)
    return path


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "splash.png")
    print(make(out))
