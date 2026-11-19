"""Player の音量(全体・曲・効果音)。

値は Editor と同じ設定のキー(master_volume / preview_volume / sfx_volume)を
そのまま使う。Editor の欄の名前は「マスター」「曲(比率)」「SE(比率)」で、
実際に出る音量は 全体 × 曲 / 全体 × 効果音。

ここにあるのは見た目と操作だけ。値を書き込む・保存する・音へ反映するのは
PlayerHost(set_audio_level)がまとめてやる。

- VolumeSlider: 平らな横棒。左右のドラッグ・ホイール・矢印キーで 0〜100。
- VolumeRows: 名前と横棒の3段(難易度設定・途中メニュー・選曲の小窓で共通)。
- SpeakerButton / VolumePopover: 選曲の上の段の右の「スピーカー」と、押すと
  下に出る小さな板(外を押す・Esc で閉じる)。
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QGridLayout,
                               QLabel, QWidget)

from neotja.player.difficulty import (BTN_RADIUS, GLOW, SELECT_COLOR, TOP_EDGE,
                                      paint_glass, thin_font, ui_family)

#: (設定キー, 表記, 既定)。並びはこの順。既定は Editor と同じ。
VOLUME_KEYS = (
    ("master_volume", "全体", 1.0),
    ("preview_volume", "曲", 0.8),
    ("sfx_volume", "効果音", 0.9),
)
DEFAULTS = {k: d for k, _t, d in VOLUME_KEYS}


def level_of(cfg, key):
    """設定の値(0.0〜1.0)。壊れていれば既定。"""
    try:
        v = float(cfg.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        v = DEFAULTS[key]
    return max(0.0, min(1.0, v))


class VolumeSlider(QWidget):
    """0〜100 の横棒。右端に数字。押す・引きずる・ホイール・←→。"""

    valueChanged = Signal(int)

    def __init__(self, parent=None, focusable=True):
        super().__init__(parent)
        self.scale = 1.0
        self._value = 0
        self._drag = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.StrongFocus if focusable else Qt.NoFocus)

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = 0
        v = max(0, min(100, v))
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def sizeHint(self):
        g = GLOW * self.scale
        return QSize(int(236 * self.scale + 2 * g), int(36 * self.scale + 2 * g))

    def inner(self):
        g = GLOW * self.scale
        return QRectF(self.rect()).adjusted(g, g, -g, -g)

    def _track(self):
        r = self.inner()
        s = self.scale
        num_w = 58 * s
        return QRectF(r.left() + 12 * s, r.center().y() - 4 * s,
                      max(10.0, r.width() - num_w - 24 * s), 8 * s)

    def _value_at(self, x):
        t = self._track()
        if t.width() <= 0:
            return self._value
        return round((x - t.left()) / t.width() * 100.0)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return super().mousePressEvent(ev)
        self._drag = True
        if self.focusPolicy() != Qt.NoFocus:
            self.setFocus(Qt.MouseFocusReason)
        self.setValue(self._value_at(ev.position().x()), emit=True)
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag:
            self.setValue(self._value_at(ev.position().x()), emit=True)
        ev.accept()

    def mouseReleaseEvent(self, ev):
        self._drag = False
        ev.accept()

    def wheelEvent(self, ev):
        # ホイールでは値を変えない(利用者の指定 2026-11-20)。親へ回す。
        ev.ignore()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key_Left, Qt.Key_Down):
            self.setValue(self._value - 5, emit=True)
        elif k in (Qt.Key_Right, Qt.Key_Up):
            self.setValue(self._value + 5, emit=True)
        else:
            return super().keyPressEvent(ev)
        ev.accept()

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.inner()
        lit = self._hover or self.hasFocus() or self._drag
        p.setPen(QPen(QColor(SELECT_COLOR) if self.hasFocus()
                      else QColor(255, 255, 255, 90 if lit else 55), 1.0 * s))
        p.setBrush(QColor(10, 20, 36, 150))
        p.drawRoundedRect(r, BTN_RADIUS * s, BTN_RADIUS * s)
        t = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRect(t)
        fill = QRectF(t.left(), t.top(), t.width() * self._value / 100.0, t.height())
        on = QColor(SELECT_COLOR)
        on.setAlpha(235 if lit else 200)
        p.setBrush(on)
        p.drawRect(fill)
        knob_w, knob_h = 6 * s, 20 * s
        kx = fill.right() - knob_w / 2
        p.setBrush(QColor(255, 255, 255))
        p.drawRect(QRectF(kx, t.center().y() - knob_h / 2, knob_w, knob_h))
        p.setFont(thin_font(21 * s, QFont.Bold))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(t.right() + 6 * s, r.top(), r.right() - t.right() - 10 * s, r.height()),
                   Qt.AlignRight | Qt.AlignVCenter, str(self._value))
        p.end()


class VolumeRows(QWidget):
    """名前と横棒の3段。changed(設定キー, 0.0〜1.0)。"""

    changed = Signal(str, float)

    def __init__(self, parent=None, focusable=True, label_px=22):
        super().__init__(parent)
        self.scale = 1.0
        self._label_px = label_px
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setColumnStretch(1, 1)
        self.labels = []
        self.sliders = {}
        for i, (key, text, _d) in enumerate(VOLUME_KEYS):
            lbl = QLabel(text)
            lbl.setObjectName("volLabel")
            sl = VolumeSlider(focusable=focusable)
            sl.valueChanged.connect(lambda v, k=key: self.changed.emit(k, v / 100.0))
            self.grid.addWidget(lbl, i, 0)
            self.grid.addWidget(sl, i, 1)
            self.labels.append(lbl)
            self.sliders[key] = sl
        self.apply_scale(1.0)

    def set_values(self, cfg):
        for key, sl in self.sliders.items():
            sl.setValue(level_of(cfg, key) * 100.0)

    def apply_scale(self, s):
        self.scale = s
        self.grid.setHorizontalSpacing(int(18 * s))
        self.grid.setVerticalSpacing(0)
        # 文字の大きさは自分の styleSheet で決める(親の QSS の QLabel の指定に
        # setFont が負けるため)。
        fam = ui_family()
        qss = ("color: #ffffff; background: transparent; font-size: %dpx; font-weight: 700; letter-spacing: 2px;%s"
               % (max(8, int(round(self._label_px * s))),
                  (' font-family: "%s";' % fam) if fam else ""))
        for lbl in self.labels:
            lbl.setFont(thin_font(self._label_px * s, QFont.Bold))
            lbl.setStyleSheet(qss)
        for sl in self.sliders.values():
            sl.scale = s
            sl.setFixedHeight(sl.sizeHint().height())
            sl.setMinimumWidth(int(160 * s))
            sl.update()


class SpeakerButton(QAbstractButton):
    """選曲の上の段の右に置く、スピーカーの絵のボタン。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.muted = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCheckable(True)
        self.setToolTip("音量")
        self.setFixedSize(52, 40)

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = paint_glass(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), BTN_RADIUS, 1.0,
                        lit=self._hover or self.isChecked(), down=self.isDown(),
                        focus=self.hasFocus() or self.isChecked(), base_alpha=150,
                        edge=TOP_EDGE)
        c = r.center()
        x0 = c.x() - 12
        pen = QPen(QColor(255, 255, 255), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255))
        body = QPainterPath()
        body.moveTo(x0, c.y() - 4)
        body.lineTo(x0 + 5, c.y() - 4)
        body.lineTo(x0 + 11, c.y() - 9)
        body.lineTo(x0 + 11, c.y() + 9)
        body.lineTo(x0 + 5, c.y() + 4)
        body.lineTo(x0, c.y() + 4)
        body.closeSubpath()
        p.drawPath(body)
        p.setBrush(Qt.NoBrush)
        if self.muted:
            k = 4.5
            mx = x0 + 19
            p.drawLine(QPointF(mx - k, c.y() - k), QPointF(mx + k, c.y() + k))
            p.drawLine(QPointF(mx - k, c.y() + k), QPointF(mx + k, c.y() - k))
        else:
            for rad in (6.0, 11.0):
                p.drawArc(QRectF(x0 + 11 - rad, c.y() - rad, rad * 2, rad * 2),
                          -45 * 16, 90 * 16)
        p.end()


class VolumePopover(QWidget):
    """スピーカーを押すと出る小さな板。窓の中の子として重ねる(別の窓に
    しない)。外を押す・Esc・もう一度スピーカーで閉じる。"""

    PAD = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("volumePopover")
        self.anchor = None
        self.rows = VolumeRows(self, focusable=True, label_px=17)
        self.rows.apply_scale(0.8)
        self.hide()

    def sizeHint(self):
        h = self.rows.sizeHint()
        return QSize(max(380, h.width() + 2 * self.PAD), h.height() + 2 * self.PAD)

    def open_under(self, anchor):
        """anchor(スピーカー)の下、右端をそろえて出す。"""
        self.anchor = anchor
        par = self.parentWidget()
        size = self.sizeHint()
        self.resize(size)
        self.rows.setGeometry(self.PAD, self.PAD, size.width() - 2 * self.PAD,
                              size.height() - 2 * self.PAD)
        if par is not None and anchor is not None:
            br = anchor.mapTo(par, anchor.rect().bottomRight())
            x = max(8, min(par.width() - size.width() - 8, br.x() - size.width() + 1))
            self.move(x, br.y() + 8)
        self.show()
        self.raise_()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        first = next(iter(self.rows.sliders.values()))
        first.setFocus(Qt.PopupFocusReason)

    def close_popover(self):
        if not self.isVisible():
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.hide()
        if self.anchor is not None:
            self.anchor.setChecked(False)
            self.anchor.update()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseButtonPress and self.isVisible():
            try:
                gp = ev.globalPosition().toPoint()
            except AttributeError:
                return False
            inside = self.rect().contains(self.mapFromGlobal(gp))
            on_anchor = (self.anchor is not None and self.anchor.isVisible()
                         and self.anchor.rect().contains(self.anchor.mapFromGlobal(gp)))
            if not inside and not on_anchor:
                self.close_popover()
        elif t == QEvent.KeyPress and self.isVisible() and ev.key() == Qt.Key_Escape:
            self.close_popover()
            return True
        return False

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), BTN_RADIUS, 1.0,
                    base_alpha=235, edge=TOP_EDGE)
        p.end()
