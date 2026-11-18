"""ゲーム画面の右上に出す、数字だけの小さな表示(BPM・HS・見た目BPM)。

Player の詳細モード用(PreviewDock.set_value_readout)。Editor の作譜モードの
命令パネルと同じ場所に置くが、押せるものは何も無い。値が変わったら縁を
青く光らせる(作譜モードで「変更」になった枠と同じ色)。

Editor は使わない(既定では作られない)。
"""

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


def _fmt(v, digits):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    s = ("%." + str(digits) + "f") % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class ValueReadout(QWidget):
    #: 原寸の大きさ。画面の縮小に合わせて set_ui_scale で縮める。
    BASE_W = 330
    BASE_H = 54
    #: 変わったときに縁が光っている長さ(ms)。だんだん元の色へ戻す。
    FLASH_MS = 700
    BORDER = QColor(127, 227, 255, 120)
    FLASH = QColor("#7aa2f7")
    FILL = QColor(10, 20, 36, 205)
    CAPTIONS = ("BPM", "HS", "見た目BPM")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("valueReadout")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._scale = 1.0
        self.texts = ("-", "-", "-")
        self._flash_t0 = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(30)
        self._flash_timer.timeout.connect(self._flash_step)
        #: 時計(試験で差し替える)。
        self.clock = time.monotonic
        self.resize(self.BASE_W, self.BASE_H)

    def set_ui_scale(self, s):
        s = max(0.3, float(s or 1.0))
        if abs(s - self._scale) < 1e-4 and self.width() == round(self.BASE_W * s):
            return
        self._scale = s
        self.resize(max(1, round(self.BASE_W * s)), max(1, round(self.BASE_H * s)))
        self.update()

    def set_values(self, bpm=None, scroll=None):
        """いまの位置の値。前と違えば縁を光らせる(最初の1回は光らせない)。"""
        vis = None
        if bpm is not None and scroll is not None:
            try:
                vis = float(bpm) * float(scroll)
            except (TypeError, ValueError):
                vis = None
        texts = (_fmt(bpm, 2) if bpm is not None else "-",
                 _fmt(scroll, 3) if scroll is not None else "-",
                 _fmt(vis, 2) if vis is not None else "-")
        if texts == self.texts:
            return False
        first = self.texts == ("-", "-", "-")
        self.texts = texts
        if not first:
            self.flash()
        self.update()
        return True

    # ------------------------------------------------------------------
    def flash(self):
        self._flash_t0 = self.clock()
        self._flash_timer.start()
        self.update()

    def flash_level(self):
        """縁の光り具合 0..1(1 が光った直後)。"""
        if self._flash_t0 is None:
            return 0.0
        k = 1.0 - (self.clock() - self._flash_t0) * 1000.0 / self.FLASH_MS
        return max(0.0, min(1.0, k))

    def _flash_step(self):
        if self.flash_level() <= 0.0:
            self._flash_t0 = None
            self._flash_timer.stop()
        self.update()

    def border_color(self):
        k = self.flash_level()
        a, b = self.BORDER, self.FLASH
        return QColor(round(a.red() + (b.red() - a.red()) * k),
                      round(a.green() + (b.green() - a.green()) * k),
                      round(a.blue() + (b.blue() - a.blue()) * k),
                      round(a.alpha() + (b.alpha() - a.alpha()) * k))

    def paintEvent(self, ev):
        s = self._scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k = self.flash_level()
        bw = (1.0 + 1.0 * k) * s
        r = QRectF(self.rect()).adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2)
        p.setBrush(self.FILL)
        p.setPen(QPen(self.border_color(), bw))
        p.drawRoundedRect(r, 2 * s, 2 * s)
        widths = (0.30, 0.28, 0.42)
        x = r.left()
        cap = QFont("Yu Gothic UI")
        cap.setPixelSize(max(1, round(11 * s)))
        cap.setWeight(QFont.DemiBold)
        val = QFont("Yu Gothic UI")
        val.setPixelSize(max(1, round(21 * s)))
        val.setWeight(QFont.Bold)
        for i, (c, t) in enumerate(zip(self.CAPTIONS, self.texts)):
            w = r.width() * widths[i]
            cell = QRectF(x, r.top(), w, r.height())
            if i:
                p.setPen(QPen(QColor(127, 227, 255, 50), 1.0))
                p.drawLine(cell.topLeft() + QRectF(0, 8 * s, 0, 0).topLeft(),
                           cell.bottomLeft() - QRectF(0, 8 * s, 0, 0).topLeft())
            p.setFont(cap)
            p.setPen(QColor(143, 163, 192))
            p.drawText(cell.adjusted(0, 4 * s, 0, 0), Qt.AlignHCenter | Qt.AlignTop, c)
            p.setFont(val)
            p.setPen(QColor(238, 244, 255))
            p.drawText(cell.adjusted(0, 0, 0, -4 * s), Qt.AlignHCenter | Qt.AlignBottom, t)
            x += w
        p.end()
