"""曲名表示(v15-design/PLAYER_IMPLEMENTATION_PLAN.md「曲名表示」、確定画像は
v15-design/player-approved/song-intro-final.png)。

大きなジャケットを真ん中に、十分に空けて下へ曲名・副題、その下に
難易度のマーク・名前・★・「オート再生」を1行。背景は難易度選択と同じ
(背景なし・OFF は濃紺、ジャケットなし・OFF は No Image の枠)。

表示はちょうど 5 秒で、飛ばせない(キーもクリックも受け流す)。試聴は最初の
2 秒そのまま、残り 3 秒でまっすぐ 0 まで下げて止める。本編の準備はこのあいだに
進め、5 秒で間に合わなければ準備ができるまで出し続ける(IntroSequence)。
"""

import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QWidget

from neotja.player import song_lists
from neotja.player.difficulty import (BASE_H, BASE_W, NAVY, SLOT_NAMES,
                                      course_mark, draw_outlined, thin_font,
                                      title_font)

#: 曲名表示の長さ(秒)。飛ばせない。
INTRO_SEC = 5.0
#: 試聴をそのままの音量で流す長さ(秒)。そのあと INTRO_SEC まで下げる。
HOLD_SEC = 2.0
#: 見張りの間合い(ms)。
TICK_MS = 30


def intro_volume(t, base, hold=HOLD_SEC, total=INTRO_SEC):
    """曲名表示の t 秒目の試聴の音量。hold まで base、total で 0(まっすぐ)。"""
    if t <= hold:
        return float(base)
    if t >= total:
        return 0.0
    return float(base) * (1.0 - (t - hold) / float(total - hold))


class IntroSequence(QObject):
    """曲名表示の段取り。時計は差し替えられる(試験用)。

    set_volume(v) … 試聴の音量を当てる
    stop_demo()   … 試聴を止める(5 秒で1回)
    begin_load()  … 本編の読み込みを始める(試聴を止めた直後に1回)。False なら失敗
    is_ready()    … 本編が鳴らせる状態か
    finished      … 5 秒たち、かつ準備ができたら1回
    failed        … 本編を読めなかった
    """

    finished = Signal()
    failed = Signal()

    def __init__(self, set_volume, base_volume, stop_demo, begin_load, is_ready,
                 clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._set_volume = set_volume
        self._base = float(base_volume)
        self._stop_demo = stop_demo
        self._begin_load = begin_load
        self._is_ready = is_ready
        self._clock = clock
        self._t0 = None
        self._demo_stopped = False
        self._loading = False
        self.done = False
        self.aborted = False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

    def start(self):
        self._t0 = self._clock()
        self._set_volume(self._base)
        self._timer.start()

    def elapsed(self):
        return 0.0 if self._t0 is None else self._clock() - self._t0

    def tick(self):
        if self.done or self.aborted or self._t0 is None:
            return
        t = self.elapsed()
        if t < INTRO_SEC:
            self._set_volume(intro_volume(t, self._base))
            return
        if not self._demo_stopped:
            self._demo_stopped = True
            self._set_volume(0.0)
            self._stop_demo()
        if not self._loading:
            self._loading = True
            if not self._begin_load():
                self._finish(self.failed)
                return
        if self._is_ready():
            self._finish(self.finished)

    def _finish(self, signal):
        self.done = True
        self._timer.stop()
        signal.emit()

    def abort(self):
        """途中でやめる(Player を出た・窓を閉じた)。試聴も止める。"""
        if self.done or self.aborted:
            return
        self.aborted = True
        self._timer.stop()
        if not self._demo_stopped:
            self._demo_stopped = True
            self._stop_demo()


class IntroPage(QWidget):
    """曲名表示の画面。キーもクリックも受け流す(飛ばせない)。"""

    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self.title = ""
        self.subtitle = ""
        self.course = "Oni"
        self.level = None
        self._jacket = None
        self._bg = None
        self._video_bg = None
        self._bg_scaled = None
        self.setFocusPolicy(Qt.StrongFocus)

    def set_video_frame(self, image):
        """背景動画の1コマ(PlayerHost の DemoVideo から)。静止画より優先する。"""
        if image is None or image.isNull():
            return
        self._video_bg = QPixmap.fromImage(image)
        self._bg_scaled = None
        if self.cfg.get("player_show_bg", True):
            self.update()

    def clear_video_frame(self):
        if self._video_bg is not None:
            self._video_bg = None
            self._bg_scaled = None
            self.update()

    def set_song(self, path, course, level):
        info = song_lists.song_info(path)
        self.title = info.get("title", "")
        self.subtitle = info.get("subtitle", "")
        self.course = course
        self.level = level

        def load(kind):
            from neotja.player.demo_video import is_video_path
            p = song_lists.existing_art(self.cfg, path, kind)
            if not p or is_video_path(p):
                return None          # 動画は DemoVideo が1コマずつ渡す
            pm = QPixmap(p)
            return None if pm.isNull() else pm
        self._jacket = load("jacket")
        self._bg = load("background")
        self._bg_scaled = None
        self.update()

    # 飛ばせない: 何を押されても受け流す(親へも渡さない)。
    def keyPressEvent(self, ev):
        ev.accept()

    def keyReleaseEvent(self, ev):
        ev.accept()

    def mousePressEvent(self, ev):
        ev.accept()

    def mouseReleaseEvent(self, ev):
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        ev.accept()

    def wheelEvent(self, ev):
        ev.accept()

    # ------------------------------------------------------------------
    def _s(self):
        return max(0.5, min(self.width() / BASE_W, self.height() / BASE_H))

    def jacket_rect(self):
        s = self._s()
        side = 330 * s
        return QRectF((self.width() - side) / 2, 118 * s, side, side)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._bg_scaled = None

    def _bg_pixmap(self):
        source = self._video_bg if self._video_bg is not None else self._bg
        if source is None:
            return None
        size = self.size()
        if self._bg_scaled is None or self._bg_scaled.size() != size:
            sc = source.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (sc.width() - size.width()) // 2
            y = (sc.height() - size.height()) // 2
            self._bg_scaled = sc.copy(x, y, size.width(), size.height())
        return self._bg_scaled

    def paintEvent(self, ev):
        s = self._s()
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        bg = self._bg_pixmap() if self.cfg.get("player_show_bg", True) else None
        if bg is not None:
            p.drawPixmap(0, 0, bg)
            # 曲名が主役なので、背景は難易度選択より深く沈める。
            p.fillRect(self.rect(), QColor(10, 24, 44, 150))
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, NAVY.lighter(108))
            grad.setColorAt(1, NAVY.darker(115))
            p.fillRect(self.rect(), QBrush(grad))

        # ジャケット(OFF・無しは No Image の枠。位置と大きさは同じ)
        jr = self.jacket_rect()
        if self._jacket is not None and self.cfg.get("player_show_jacket", True):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 80))
            p.drawRect(jr.translated(0, 8 * s))
            p.drawPixmap(jr, self._jacket, QRectF(self._jacket.rect()))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.6 * s))
            p.setBrush(QColor(10, 18, 32, 150))
            p.drawRect(jr)
            p.setFont(thin_font(44 * s, QFont.DemiBold))
            p.setPen(QColor(235, 240, 248))
            p.drawText(jr, Qt.AlignCenter, "No\nImage")

        # 曲名・副題(中央そろえ)
        avail = w - 120 * s
        size = 70 * s
        f = title_font(size)
        while QFontMetricsF(f).horizontalAdvance(self.title) > avail and size > 32 * s:
            size -= 2 * s
            f = title_font(size)
        text = QFontMetricsF(f).elidedText(self.title, Qt.ElideRight, avail)
        base = jr.bottom() + 118 * s
        draw_outlined(p, text, f, (w - QFontMetricsF(f).horizontalAdvance(text)) / 2,
                      base, 10 * s)
        if self.subtitle:
            sf = title_font(32 * s)
            sub = QFontMetricsF(sf).elidedText(self.subtitle, Qt.ElideRight, avail)
            base += 54 * s
            draw_outlined(p, sub, sf, (w - QFontMetricsF(sf).horizontalAdvance(sub)) / 2,
                          base, 6 * s)

        # マーク・難易度名・★・オート再生(1行、中央そろえ)
        self._draw_course_row(p, base + 66 * s, s)
        p.end()

    def _draw_course_row(self, p, cy, s):
        mark_d = 78 * s
        name = SLOT_NAMES.get(self.course, self.course)
        nf = title_font(40 * s)
        star = "★%d" % self.level if self.level is not None else "★—"
        sfnt = thin_font(38 * s, QFont.Bold)
        bfnt = thin_font(22 * s, QFont.Bold)
        badge = "オート再生"
        gap = 26 * s
        name_w = QFontMetricsF(nf).horizontalAdvance(name)
        star_w = QFontMetricsF(sfnt).horizontalAdvance(star)
        badge_w = QFontMetricsF(bfnt).horizontalAdvance(badge) + 34 * s
        badge_h = 44 * s
        total = mark_d + gap + name_w + gap + star_w + gap + badge_w
        x = (self.width() - total) / 2

        # マーク(背景なしの絵があればそのまま。古いキャッシュではカードの絵を丸く)
        mr = QRectF(x, cy - mark_d / 2, mark_d, mark_d)
        pm = course_mark(self.course)
        if pm is not None and pm.width() == pm.height():
            p.drawPixmap(mr.adjusted(-8 * s, -8 * s, 8 * s, 8 * s), pm, QRectF(pm.rect()))
        elif pm is not None:
            src_side = min(pm.width(), pm.height()) * 0.94
            src = QRectF((pm.width() - src_side) / 2, pm.height() * 0.545 - src_side / 2,
                         src_side, src_side)
            clip = QPainterPath()
            clip.addEllipse(mr)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(mr, pm, src)
            p.restore()
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.5 * s))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(mr)
        x += mark_d + gap
        fm = QFontMetricsF(nf)
        draw_outlined(p, name, nf, x, cy + (fm.ascent() - fm.descent()) / 2, 6 * s)
        x += name_w + gap
        p.setFont(sfnt)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x, cy - 30 * s, star_w + 4, 60 * s), Qt.AlignLeft | Qt.AlignVCenter, star)
        x += star_w + gap
        br = QRectF(x, cy - badge_h / 2, badge_w, badge_h)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.2 * s))
        p.setBrush(QColor(108, 116, 128, 225))
        p.drawRoundedRect(br, 4 * s, 4 * s)
        p.setFont(bfnt)
        p.setPen(QColor(255, 255, 255))
        p.drawText(br, Qt.AlignCenter, badge)
