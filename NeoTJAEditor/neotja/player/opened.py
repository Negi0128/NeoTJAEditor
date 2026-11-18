"""「TJAを開く」(ファイルを選ぶ・落とす)のあとに出す、その曲の画面。

ジャケット・曲名・副題・コースの札と、「保存済みに追加」「すぐに再生」。
「すぐに再生」は難易度選択へ(試聴つき)。「保存済みに追加」を押したら
「保存済み」に変えて押せなくし、この画面にとどまる。左上の「戻る」で選曲へ。

あとで「プレイリストに追加」を並べられるよう、ボタンは横一列の並び
(actions)に入れてある。

見た目は難易度選択と同じ平たいガラス(difficulty.paint_glass)。説明の文字は
足さない。
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QWidget

from neotja.player import song_lists
from neotja.player.difficulty import (
    BASE_H, BASE_W, BTN_RADIUS, GLOW, NAVY, GlassPill, _GlassButton, draw_outlined,
    paint_glass, thin_font, title_font,
)
from neotja.start_screen import COURSE_COLOR_KEY
from neotja.theme import COLORS


class OpenedAction(_GlassButton):
    """この画面のボタン。primary は「すぐに再生」(▶ と明るい縁)。
    done(押し終わった: 保存済み)は押せないが、薄くせず水色の縁と ✓ で示す。"""

    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_kind = icon
        self.primary = icon == "play"
        self.done = False

    def set_done(self, text):
        self.done = True
        self.setText(text)
        self.setEnabled(False)
        self.update()

    def reset(self, text):
        self.done = False
        self.setText(text)
        self.setEnabled(True)
        self.update()

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled() and not self.done:
            p.setOpacity(0.4)
        r = paint_glass(p, self.inner(), BTN_RADIUS * s, s,
                        lit=self._hover and self.isEnabled(), down=self.isDown(),
                        focus=self.hasFocus() and not self.primary,
                        primary=self.primary or self.done, base_alpha=175,
                        edge=QColor(220, 228, 238, 150))
        f = thin_font((26 if self.primary else 22) * s, QFont.Bold if self.primary else QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, (5 if self.primary else 3) * s)
        fm = QFontMetricsF(f)
        icon_w = 18 * s
        gap = 18 * s
        text_w = fm.horizontalAdvance(self.text())
        x0 = r.center().x() - (icon_w + gap + text_w) / 2
        cy = r.center().y()
        pen = QPen(QColor(255, 255, 255), 2.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        if self.primary:
            h = 10 * s
            tri = QPainterPath()
            tri.moveTo(x0, cy - h)
            tri.lineTo(x0 + icon_w, cy)
            tri.lineTo(x0, cy + h)
            tri.closeSubpath()
            p.setPen(pen)
            p.setBrush(QColor(255, 255, 255))
            p.drawPath(tri)
        elif self.done:
            p.setPen(QPen(QColor("#7fe3ff"), 2.6 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(x0 + 1 * s, cy), QPointF(x0 + 7 * s, cy + 6 * s),
                                      QPointF(x0 + icon_w, cy - 7 * s)]))
        else:
            k = 8 * s
            c = QPointF(x0 + icon_w / 2, cy)
            p.setPen(pen)
            p.drawLine(QPointF(c.x() - k, c.y()), QPointF(c.x() + k, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - k), QPointF(c.x(), c.y() + k))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x0 + icon_w + gap, r.top(), text_w + 20 * s, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


class OpenedPage(QWidget):
    """開いた TJA の画面。set_song で曲を入れる。"""

    back_requested = Signal()
    #: すぐに再生(難易度選択へ)。
    play_requested = Signal(str)
    #: 保存済みに追加。
    add_requested = Signal(str)
    sound_requested = Signal(str)

    ADD_TEXT = "保存済みに追加"
    ADDED_TEXT = "保存済み"

    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.setObjectName("openedPage")
        self.cfg = config_data
        self.path = ""
        self.title = ""
        self.subtitle = ""
        self.courses = []
        self._jacket = None
        self._bg = None
        self._bg_scaled = None
        self.scale = 1.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.btn_back = GlassPill("戻る", "back", self)
        self.btn_back.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_back.clicked.connect(self.back_requested)
        self.btn_add = OpenedAction(self.ADD_TEXT, "add", self)
        self.btn_add.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_add.clicked.connect(lambda: self.path and self.add_requested.emit(self.path))
        self.btn_play = OpenedAction("すぐに再生", "play", self)
        self.btn_play.clicked.connect(lambda: self.sound_requested.emit("don"))
        self.btn_play.clicked.connect(lambda: self.path and self.play_requested.emit(self.path))
        #: 横一列に並べるボタン(左から)。「プレイリストに追加」はここへ足す。
        self.actions = [self.btn_add, self.btn_play]

    # ------------------------------------------------------------------
    def set_song(self, path):
        self.path = path
        info = song_lists.song_info(path)
        self.title = info.get("title", "")
        self.subtitle = info.get("subtitle", "")
        self.courses = list(info.get("courses") or [])

        def load(kind):
            from neotja.player.demo_video import is_video_path
            p = song_lists.existing_art(self.cfg, path, kind)
            if not p or is_video_path(p):
                return None
            pm = QPixmap(p)
            return None if pm.isNull() else pm
        self._jacket = load("jacket") if self.cfg.get("player_show_jacket", True) else None
        self._bg = load("background") if self.cfg.get("player_show_bg", True) else None
        self._bg_scaled = None
        self.set_added(song_lists.is_saved(self.cfg, path))
        self.update()

    def set_added(self, on):
        if on:
            self.btn_add.set_done(self.ADDED_TEXT)
        else:
            self.btn_add.reset(self.ADD_TEXT)

    def is_added(self):
        return self.btn_add.done

    # ------------------------------------------------------------------
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.btn_back.click()
            ev.accept()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.focusWidget() in (self, None):
            self.btn_play.click()
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------
    def _s(self):
        return max(0.55, min(self.width() / BASE_W, self.height() / BASE_H))

    def jacket_rect(self):
        s = self._s()
        side = 360 * s
        return QRectF(140 * s, (self.height() - side) / 2 - 30 * s, side, side)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        s = self._s()
        self.scale = s
        gl = GLOW * s
        b = self.btn_back
        b.scale = s
        hint = b.sizeHint()
        b.setGeometry(int(28 * s - gl), int(24 * s - gl), hint.width(), hint.height())
        jr = self.jacket_rect()
        x = jr.right() + 56 * s
        bw, bh, gap = 330 * s, 84 * s, 22 * s
        y = jr.bottom() - bh
        g = GLOW * s
        for btn in self.actions:
            btn.scale = s
            btn.setGeometry(int(x - g), int(y - g), int(bw + 2 * g), int(bh + 2 * g))
            x += bw + gap
        self._bg_scaled = None

    def _bg_pixmap(self):
        if self._bg is None:
            return None
        size = self.size()
        if self._bg_scaled is None or self._bg_scaled.size() != size:
            sc = self._bg.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (sc.width() - size.width()) // 2
            y = (sc.height() - size.height()) // 2
            self._bg_scaled = sc.copy(x, y, size.width(), size.height())
        return self._bg_scaled

    def chip_rects(self):
        """コースの札の (名前, 文字, 矩形)。"""
        s = self._s()
        jr = self.jacket_rect()
        x = jr.right() + 56 * s
        y = jr.top() + (178 if self.subtitle else 124) * s
        f = thin_font(20 * s, QFont.Bold)
        fm = QFontMetricsF(f)
        out = []
        for name, level in self.courses:
            label = song_lists.COURSE_LABELS.get(name, name)
            text = label if level is None else "%s %d" % (label, level)
            w = fm.horizontalAdvance(text) + 30 * s
            out.append((name, text, QRectF(x, y, w, 40 * s)))
            x += w + 12 * s
        return out

    def paintEvent(self, ev):
        s = self._s()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        bg = self._bg_pixmap()
        if bg is not None:
            p.drawPixmap(0, 0, bg)
            p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, NAVY.lighter(108))
            grad.setColorAt(1, NAVY.darker(115))
            p.fillRect(self.rect(), QBrush(grad))
        jr = self.jacket_rect()
        if self._jacket is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRect(jr.translated(0, 6 * s))
            p.drawPixmap(jr, self._jacket, QRectF(self._jacket.rect()))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(jr)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.6 * s))
            p.setBrush(QColor(10, 18, 32, 150))
            p.drawRect(jr)
            p.setFont(thin_font(34 * s, QFont.DemiBold))
            p.setPen(QColor(235, 240, 248))
            p.drawText(jr, Qt.AlignCenter, "No\nImage")
        tx = jr.right() + 56 * s
        avail = w - tx - 80 * s
        size = 62 * s
        f = title_font(size)
        while QFontMetricsF(f).horizontalAdvance(self.title) > avail and size > 30 * s:
            size -= 2 * s
            f = title_font(size)
        text = QFontMetricsF(f).elidedText(self.title, Qt.ElideRight, avail)
        draw_outlined(p, text, f, tx, jr.top() + 70 * s, 9 * s)
        if self.subtitle:
            sf = title_font(28 * s)
            sub = QFontMetricsF(sf).elidedText(self.subtitle, Qt.ElideRight, avail)
            draw_outlined(p, sub, sf, tx, jr.top() + 124 * s, 5 * s)
        cf = thin_font(20 * s, QFont.Bold)
        p.setFont(cf)
        for name, label, r in self.chip_rects():
            col = QColor(COLORS.get(COURSE_COLOR_KEY.get(name, "fg_dim"), COLORS["fg_dim"]))
            fill = QColor(10, 20, 36, 190)
            p.setBrush(fill)
            p.setPen(QPen(col, 1.4 * s))
            p.drawRect(r)
            p.setPen(col.lighter(130))
            p.drawText(r, Qt.AlignCenter, label)
        p.end()
