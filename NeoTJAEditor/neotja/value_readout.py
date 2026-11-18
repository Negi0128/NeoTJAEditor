"""ゲーム画面の右上に出す、値だけの小さな枠(BPM・拍子・SCROLL・見た目BPM)。

Player の詳細モード用(PreviewDock.set_value_readout)。見た目は Editor の
作譜モードの命令パネル(command_panel.CommandPanel)の枠と同じ — 暗い紺の
角丸の枠に細い線、左上に見出し、値は一段暗い欄。ただし読むだけなので
上下の矢印や「追加」「変更」は無く、枠は値に合わせて小さく、項目ごとに
離して並べる(利用者の指定 2026-11-19)。値が変わった枠は縁を青く光らせる
(作譜モードで「変更」になった枠と同じ色)。

Editor は使わない(既定では作られない)。
"""

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget


def _num(v, digits):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    s = ("%." + str(digits) + "f") % v
    if s.startswith("-") and float(s) == 0.0:
        s = s[1:]
    return s


def _measure(m):
    """"4/4" → ("4", "4")。読めなければ ("-", "-")。"""
    try:
        n, d = (int(x) for x in str(m).split("/"))
    except (TypeError, ValueError):
        return ("-", "-")
    return (str(n), str(d))


class ValueReadout(QWidget):
    #: 項目(見出し, 欄の数, 欄の幅を決める見本の字)。拍子だけ欄が2つ。
    ITEMS = (("BPM", 1, "000.000"),
             ("拍子", 2, "00"),
             ("SCROLL", 1, "0.000"),
             ("見た目BPM", 1, "000.0"))
    #: 原寸の寸法(px)。命令パネルの枠(見出し 12px・欄の字 14px)に合わせる。
    PAD_X = 7           # 枠の左右の余白
    PAD_TOP = 3
    PAD_BOTTOM = 4
    TITLE_H = 16
    TITLE_GAP = 1
    FIELD_H = 26
    FIELD_PAD = 6       # 欄の中の左右の余白
    SLASH_W = 12        # 拍子の「/」
    GAP = 6             # 枠どうしの間
    TITLE_PX = 12
    VALUE_PX = 14
    BASE_H = PAD_TOP + TITLE_H + TITLE_GAP + FIELD_H + PAD_BOTTOM

    #: 変わったときに縁が光っている長さ(ms)。だんだん元の色へ戻す。
    FLASH_MS = 700
    # 色は command_panel._QSS と同じ。
    BOX_FILL = QColor(20, 24, 34, 220)
    BORDER = QColor("#33405c")
    FLASH_FILL = QColor(28, 34, 50, 230)
    FLASH = QColor("#7aa2f7")
    TITLE_COLOR = QColor("#8fa3c0")
    FIELD_FILL = QColor("#10141d")
    FIELD_BORDER = QColor("#2e3a50")
    VALUE_COLOR = QColor("#ffffff")
    SLASH_COLOR = QColor("#cdd6f4")

    EMPTY = ("-", ("-", "-"), "-", "-")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("valueReadout")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._scale = 1.0
        #: 項目ごとの出している字(拍子は (分子, 分母))。
        self.texts = self.EMPTY
        #: 項目ごとの光り始めた時刻(None = 光っていない)。
        self._flash_t0 = [None] * len(self.ITEMS)
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(30)
        self._flash_timer.timeout.connect(self._flash_step)
        #: 時計(試験で差し替える)。
        self.clock = time.monotonic
        self._layout = []
        self._relayout()

    # ------------------------------------------------------------------
    # 大きさ
    def _fonts(self, s=1.0):
        title = QFont("Yu Gothic UI")
        title.setPixelSize(max(1, round(self.TITLE_PX * s)))
        title.setWeight(QFont.Normal)
        value = QFont("Yu Gothic UI")
        value.setPixelSize(max(1, round(self.VALUE_PX * s)))
        value.setWeight(QFont.Normal)
        return title, value

    def _relayout(self):
        """原寸で枠と欄の幅を決める。見本の字より長い値が来たらそのぶん広げる。"""
        title_f, value_f = self._fonts()
        tm, vm = QFontMetricsF(title_f), QFontMetricsF(value_f)
        boxes = []
        x = 0.0
        for i, (cap, nfield, sample) in enumerate(self.ITEMS):
            t = self.texts[i]
            vals = t if nfield == 2 else (t,)
            fw = max(vm.horizontalAdvance(sample),
                     max(vm.horizontalAdvance(v) for v in vals)) + 2 * self.FIELD_PAD
            fw = float(round(fw))
            inner = fw * nfield + (self.SLASH_W if nfield == 2 else 0)
            bw = max(inner, round(tm.horizontalAdvance(cap)) + 2.0) + 2 * self.PAD_X
            boxes.append((x, bw, fw))
            x += bw + self.GAP
        self._layout = boxes
        self._base_w = x - self.GAP
        self._apply_size()

    def _apply_size(self):
        s = self._scale
        w, h = max(1, round(self._base_w * s)), max(1, round(self.BASE_H * s))
        if (self.width(), self.height()) != (w, h):
            self.resize(w, h)

    def base_size(self):
        return (self._base_w, self.BASE_H)

    def box_rects(self):
        """枠の場所(いまの倍率・このウィジェットの座標)。試験と描画で使う。"""
        s = self._scale
        return [QRectF(x * s, 0, bw * s, self.BASE_H * s) for x, bw, _ in self._layout]

    def set_ui_scale(self, s):
        s = max(0.3, float(s or 1.0))
        if abs(s - self._scale) < 1e-4:
            return
        self._scale = s
        self._apply_size()
        self.update()

    # ------------------------------------------------------------------
    def set_values(self, bpm=None, scroll=None, measure=None):
        """いまの位置の値。前と違う項目の枠を光らせる(最初の1回は光らせない)。"""
        vis = None
        if bpm is not None and scroll is not None:
            try:
                vis = float(bpm) * float(scroll)
            except (TypeError, ValueError):
                vis = None
        texts = (_num(bpm, 3) if bpm is not None else "-",
                 _measure(measure) if measure is not None else ("-", "-"),
                 _num(scroll, 3) if scroll is not None else "-",
                 _num(vis, 1) if vis is not None else "-")
        if texts == self.texts:
            return False
        first = self.texts == self.EMPTY
        old = self.texts
        self.texts = texts
        if not first:
            for i, (a, b) in enumerate(zip(old, texts)):
                if a != b:
                    self.flash(i)
        self._relayout()
        self.update()
        return True

    # ------------------------------------------------------------------
    def flash(self, index=None):
        """index の枠(None ならぜんぶ)を光らせる。"""
        now = self.clock()
        for i in (range(len(self.ITEMS)) if index is None else (index,)):
            self._flash_t0[i] = now
        self._flash_timer.start()
        self.update()

    def flash_level(self, index=None):
        """縁の光り具合 0..1(1 が光った直後)。index=None なら一番強い枠。"""
        if index is None:
            return max(self.flash_level(i) for i in range(len(self.ITEMS)))
        t0 = self._flash_t0[index]
        if t0 is None:
            return 0.0
        k = 1.0 - (self.clock() - t0) * 1000.0 / self.FLASH_MS
        return max(0.0, min(1.0, k))

    def _flash_step(self):
        for i in range(len(self.ITEMS)):
            if self._flash_t0[i] is not None and self.flash_level(i) <= 0.0:
                self._flash_t0[i] = None
        if all(t is None for t in self._flash_t0):
            self._flash_timer.stop()
        self.update()

    @staticmethod
    def _mix(a, b, k):
        return QColor(round(a.red() + (b.red() - a.red()) * k),
                      round(a.green() + (b.green() - a.green()) * k),
                      round(a.blue() + (b.blue() - a.blue()) * k),
                      round(a.alpha() + (b.alpha() - a.alpha()) * k))

    def border_color(self, index=None):
        return self._mix(self.BORDER, self.FLASH, self.flash_level(index))

    # ------------------------------------------------------------------
    def paintEvent(self, ev):
        s = self._scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        title_f, value_f = self._fonts(s)
        for i, ((cap, nfield, _), (x, bw, fw)) in enumerate(zip(self.ITEMS, self._layout)):
            k = self.flash_level(i)
            lw = max(1.0, (1.0 + k) * s)
            box = QRectF(x * s, 0, bw * s, self.BASE_H * s).adjusted(
                lw / 2, lw / 2, -lw / 2, -lw / 2)
            p.setBrush(self._mix(self.BOX_FILL, self.FLASH_FILL, k))
            p.setPen(QPen(self._mix(self.BORDER, self.FLASH, k), lw))
            p.drawRoundedRect(box, 4 * s, 4 * s)
            # 見出し(左上)
            p.setFont(title_f)
            p.setPen(self.TITLE_COLOR)
            p.drawText(QRectF((x + self.PAD_X) * s, self.PAD_TOP * s,
                              (bw - 2 * self.PAD_X) * s, self.TITLE_H * s),
                       Qt.AlignLeft | Qt.AlignVCenter, cap)
            # 欄
            fy = (self.PAD_TOP + self.TITLE_H + self.TITLE_GAP) * s
            fx = x + self.PAD_X
            vals = self.texts[i] if nfield == 2 else (self.texts[i],)
            p.setFont(value_f)
            for j, v in enumerate(vals):
                if j:
                    p.setPen(self.SLASH_COLOR)
                    p.drawText(QRectF(fx * s, fy, self.SLASH_W * s, self.FIELD_H * s),
                               Qt.AlignCenter, "/")
                    fx += self.SLASH_W
                field = QRectF(fx * s, fy, fw * s, self.FIELD_H * s).adjusted(0.5, 0.5, -0.5, -0.5)
                p.setBrush(self.FIELD_FILL)
                p.setPen(QPen(self.FIELD_BORDER, max(1.0, s)))
                p.drawRoundedRect(field, 3 * s, 3 * s)
                p.setPen(self.VALUE_COLOR)
                p.drawText(field.adjusted(self.FIELD_PAD * s, 0, -self.FIELD_PAD * s, 0),
                           Qt.AlignLeft | Qt.AlignVCenter, v)
                fx += fw
        p.end()
