"""曲名表示(v15-design/PLAYER_IMPLEMENTATION_PLAN.md「曲名表示」、確定画像は
v15-design/player-approved/song-intro-final.png)。

大きなジャケットを真ん中に、十分に空けて下へ曲名・副題、その下に
難易度のマーク・名前・★・「オート演奏」を1行。背景は難易度選択と同じ
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
from neotja.player.difficulty import (ui_css_family, BASE_H, BASE_W, NAVY, SLOT_NAMES,
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
                 clock=time.monotonic, parent=None, hold=HOLD_SEC, total=INTRO_SEC):
        super().__init__(parent)
        #: そのままの音量の長さと、全体の長さ(秒)。プレイリストの「再生する」
        #: のあとは短く(すぐ下げ始める)。
        self._hold = float(hold)
        self._total = float(total)
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
        if t < self._total:
            self._set_volume(intro_volume(t, self._base, self._hold, self._total))
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


#: プレイリストの連続再生の右の板(倍率 1 のときの寸法)。
PL_PANEL_W = 560
#: コースのカードの倍率の上限(難易度選択のカードに対して)。
PL_CARD_SCALE = 0.75
PL_MARGIN = 80
PL_PAD = 32
PL_COUNTER_H = 64
PL_PLAY_H = 96
PL_QUIT_H = 58
PL_GAP = 26
#: 平らなボタン(暗い半透明・細い薄シアンの縁・ほぼ四角・太い Yu Gothic UI)。
PL_BUTTON_QSS = """
QPushButton {
    background-color: rgba(16, 28, 44, 190); color: #ffffff;
    border: %(bw)dpx solid rgba(139, 217, 238, 170); border-radius: %(r)dpx;
    font-family: "%(fam)s"; font-weight: bold; font-size: %(px)dpx;
    letter-spacing: %(ls)dpx;
}
QPushButton:hover { background-color: rgba(35, 61, 84, 215); border-color: #8ad9ee; }
QPushButton:pressed { background-color: rgba(10, 20, 34, 230); }
QPushButton:focus { border-color: #7fe3ff; }
"""


def _make_countdown_button(text, parent):
    """「再生する」。自動のときは右の端に、残りの秒と減っていく輪を描く。"""
    from PySide6.QtWidgets import QPushButton

    class _Btn(QPushButton):
        def __init__(self):
            super().__init__(text, parent)
            #: 残りの割合(1→0)と秒。None なら描かない。
            self.ratio = None
            self.secs = 0

        def set_countdown(self, ratio, secs):
            self.ratio = ratio
            self.secs = secs
            self.update()

        def paintEvent(self, ev):
            super().paintEvent(ev)
            if self.ratio is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            h = self.height()
            d = h * 0.74
            r = QRectF(self.width() - h * 0.14 - d, (h - d) / 2, d, d)
            pen_w = max(2.0, d * 0.08)
            p.setPen(QPen(QColor(255, 255, 255, 50), pen_w))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
            p.setPen(QPen(QColor(127, 227, 255), pen_w, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, int(360 * 16 * max(0.0, min(1.0, self.ratio))))
            p.setFont(thin_font(d * 0.46, QFont.Bold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignCenter, str(int(self.secs)))
            p.end()
    return _Btn()


class IntroPage(QWidget):
    """曲名表示の画面。キーもクリックも受け流す(飛ばせない)。

    プレイリストの連続再生(set_playlist)では、今の表示を左へ寄せて、右に
    「N曲目 / M曲」・コースの小さなカード・「再生する」・「やめる」の板を出す。
    このときは待つ(5 秒で進まない)。キーは Enter=再生する・Esc=やめる・
    ←→=コース。"""

    #: プレイリストの「再生する」/「やめる」/ コースを替えた / 操作音。
    play_requested = Signal()
    quit_requested = Signal()
    course_changed = Signal(str)
    sound_requested = Signal(str)

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
        #: プレイリストの連続再生のときの (何曲目, 全部で何曲)。None なら普段の曲名表示。
        self.playlist = None
        #: 選べるコース [(名前, レベル)](難しい順でなく かんたん→裏)。
        self.pl_courses = []
        self._pl_cards = {}
        self._pl_panel = QRectF()
        self.btn_play = None
        self.btn_quit = None
        self.pl_auto = False
        #: 自動(完全オート)の待ち。(始めた時刻, 秒) または None。
        self._countdown = None
        self._cd_timer = QTimer(self)
        self._cd_timer.setInterval(50)
        self._cd_timer.timeout.connect(self._tick_countdown)
        #: 時計(試験で差し替える)。
        self.clock = time.monotonic

    # ------------------------------------------------------------------
    # プレイリスト
    def set_playlist(self, index, total, courses, auto=False):
        """連続再生の曲名表示にする。index は 1 から。courses は song_info の並び。
        auto(完全オート)ではコースのカードを出さない(変えない)。"""
        from PySide6.QtWidgets import QPushButton
        from neotja.player.difficulty import CourseCard
        self.stop_countdown()
        self.playlist = (int(index), int(total))
        self.pl_auto = bool(auto)
        order = ("Easy", "Normal", "Hard", "Oni", "Edit")
        levels = dict(courses or [])
        self.pl_courses = [] if auto else [(k, levels[k]) for k in order if k in levels]
        if self.btn_play is None:
            self.btn_play = _make_countdown_button("再生する", self)
            self.btn_play.setCursor(Qt.PointingHandCursor)
            self.btn_play.setFocusPolicy(Qt.NoFocus)
            self.btn_play.clicked.connect(self.request_play)
            self.btn_quit = QPushButton("やめる", self)
            self.btn_quit.setCursor(Qt.PointingHandCursor)
            self.btn_quit.setFocusPolicy(Qt.NoFocus)
            self.btn_quit.clicked.connect(self.request_quit)
        for c in self._pl_cards.values():
            c.setParent(None)
            c.deleteLater()
        self._pl_cards = {}
        for k, lv in self.pl_courses:
            c = CourseCard(k, self)
            c.LEVEL_PX = 30          # 小さく並べるので★を大きめに(1920x1009 で約24px)
            if k == "Edit":
                c.name_override = "おに(裏)"   # 表のおにと見分ける
            c.set_state(k, True, lv, k == self.course)
            c.setFocusPolicy(Qt.NoFocus)
            c.clicked.connect(self.pick_course)
            c.show()
            self._pl_cards[k] = c
        self.btn_play.show()
        self.btn_quit.show()
        self._layout_playlist()
        self.update()

    def clear_playlist(self):
        """普段の曲名表示へ戻す(板を隠す)。"""
        self.stop_countdown()
        self.playlist = None
        for c in self._pl_cards.values():
            c.setParent(None)
            c.deleteLater()
        self._pl_cards = {}
        self.pl_courses = []
        if self.btn_play is not None:
            self.btn_play.hide()
            self.btn_quit.hide()
        self.update()

    def set_course(self, course, level):
        self.course = course
        self.level = level
        for k, c in self._pl_cards.items():
            c.selected = k == course
            c.update()
        self.update()

    def pick_course(self, key):
        levels = dict(self.pl_courses)
        if key not in levels or key == self.course:
            return
        self.sound_requested.emit("pi")
        self.set_course(key, levels[key])
        self.course_changed.emit(key)

    def _step_course(self, d):
        keys = [k for k, _l in self.pl_courses]
        if not keys:
            return
        i = keys.index(self.course) if self.course in keys else 0
        j = max(0, min(len(keys) - 1, i + d))
        if j != i:
            self.pick_course(keys[j])

    def request_play(self):
        if self.playlist is None:
            return
        self.stop_countdown()
        self.sound_requested.emit("don")
        self.play_requested.emit()

    def request_quit(self):
        if self.playlist is None:
            return
        self.stop_countdown()
        self.sound_requested.emit("pi")
        self.quit_requested.emit()

    # --- 自動の待ち ---------------------------------------------------------
    def start_countdown(self, secs):
        """secs 秒たったら自分で「再生する」。「再生する」ならすぐ、やめるで取り消し。"""
        if self.playlist is None or self.btn_play is None:
            return
        self._countdown = (self.clock(), max(0.0, float(secs)))
        self._tick_countdown()
        if self._countdown is not None:
            self._cd_timer.start()

    def stop_countdown(self):
        self._countdown = None
        self._cd_timer.stop()
        if self.btn_play is not None:
            self.btn_play.set_countdown(None, 0)

    def countdown_left(self):
        """残りの秒。待っていなければ None。"""
        if self._countdown is None:
            return None
        t0, total = self._countdown
        return max(0.0, total - (self.clock() - t0))

    def _tick_countdown(self):
        import math
        left = self.countdown_left()
        if left is None:
            self._cd_timer.stop()
            return
        total = self._countdown[1]
        if left <= 0.0:
            self.request_play()
            return
        self.btn_play.set_countdown(left / total if total > 0 else 0.0, math.ceil(left))

    def _left_width(self):
        """曲名などを並べる幅(プレイリストのときは右の板を除いた左側)。"""
        if self.playlist is None:
            return float(self.width())
        s = self._s()
        return max(200.0, self.width() - (PL_PANEL_W + PL_MARGIN) * s)

    def _cx(self):
        return self._left_width() / 2.0

    def _layout_playlist(self):
        if self.playlist is None or self.btn_play is None:
            return
        s = self._s()
        pw = PL_PANEL_W * s
        inner = pw - 2 * PL_PAD * s
        n = len(self._pl_cards)
        # 4枚以上は2段に(1段に並べると★が読めないほど小さくなる)。下の段は
        # 上の段の真ん中にそろえる(各段をパネルの中央へ)。
        per_row = n if n <= 3 else (n + 1) // 2
        rows = 0 if n == 0 else (1 if n <= 3 else 2)
        cs = min(PL_CARD_SCALE * s, inner / (max(1, per_row) * 236.0))
        cw, ch = 236 * cs, 232 * cs
        cards_h = rows * ch + (PL_GAP * s if rows else 0)
        ph = (PL_PAD * 2 + PL_COUNTER_H + PL_GAP * 2 + PL_PLAY_H + PL_QUIT_H) * s + cards_h
        x = self.width() - PL_MARGIN * s * 0.6 - pw
        y = (self.height() - ph) / 2.0
        self._pl_panel = QRectF(x, y, pw, ph)
        cy = y + (PL_PAD + PL_COUNTER_H + PL_GAP) * s
        keys = [k for k, _lv in self.pl_courses]
        for r in range(rows):
            row = keys[r * per_row:(r + 1) * per_row]
            cx = x + (pw - cw * len(row)) / 2.0
            for k in row:
                c = self._pl_cards[k]
                c.scale = cs
                c.setGeometry(int(cx), int(cy), int(cw), int(ch))
                cx += cw
            cy += ch
        by = cy + (PL_GAP * s if rows else 0)
        bx, bw = x + PL_PAD * s, inner
        self.btn_play.setGeometry(int(bx), int(by), int(bw), int(PL_PLAY_H * s))
        self.btn_play.setStyleSheet(PL_BUTTON_QSS % {
            "bw": max(1, round(1.5 * s)), "r": max(2, round(3 * s)),
            "px": max(12, round(34 * s)), "ls": max(1, round(6 * s)), "fam": ui_css_family()})
        by += (PL_PLAY_H + PL_GAP) * s
        self.btn_quit.setGeometry(int(bx), int(by), int(bw), int(PL_QUIT_H * s))
        self.btn_quit.setStyleSheet(PL_BUTTON_QSS % {
            "bw": 1, "r": max(2, round(3 * s)),
            "px": max(11, round(22 * s)), "ls": max(1, round(3 * s)), "fam": ui_css_family()})

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
    # プレイリストの曲名表示だけ、Enter / Esc / ←→ を使う。
    def keyPressEvent(self, ev):
        if self.playlist is not None and not ev.isAutoRepeat():
            k = ev.key()
            if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.request_play()
            elif k == Qt.Key_Escape:
                self.request_quit()
            elif k in (Qt.Key_Left, Qt.Key_Right):
                self._step_course(-1 if k == Qt.Key_Left else 1)
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
        return QRectF(self._cx() - side / 2, 118 * s, side, side)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._bg_scaled = None
        self._layout_playlist()

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
        # 「ジャケット」OFF は再生画面の下のジャケットだけ(利用者の指定 2026-11-20)。
        if self._jacket is not None:
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

        # 曲名・副題(中央そろえ。プレイリストのときは左側の真ん中)
        lw = self._left_width()
        avail = lw - 120 * s
        size = 70 * s
        f = title_font(size)
        while QFontMetricsF(f).horizontalAdvance(self.title) > avail and size > 32 * s:
            size -= 2 * s
            f = title_font(size)
        text = QFontMetricsF(f).elidedText(self.title, Qt.ElideRight, avail)
        base = jr.bottom() + 118 * s
        draw_outlined(p, text, f, (lw - QFontMetricsF(f).horizontalAdvance(text)) / 2,
                      base, 10 * s)
        if self.subtitle:
            sf = title_font(32 * s)
            sub = QFontMetricsF(sf).elidedText(self.subtitle, Qt.ElideRight, avail)
            base += 54 * s
            draw_outlined(p, sub, sf, (lw - QFontMetricsF(sf).horizontalAdvance(sub)) / 2,
                          base, 6 * s)

        # マーク・難易度名・★・オート演奏(1行、中央そろえ)
        self._draw_course_row(p, base + 66 * s, s)
        if self.playlist is not None:
            self._draw_playlist_panel(p, s)
        p.end()

    def _draw_playlist_panel(self, p, s):
        """右の板(難易度選択・途中メニューと同じ平らなガラス)と「N曲目 / M曲」。"""
        from neotja.player.difficulty import BTN_RADIUS, TOP_EDGE, paint_glass
        r = self._pl_panel
        if r.isEmpty():
            return
        paint_glass(p, r, BTN_RADIUS * s, s, base_alpha=190, edge=TOP_EDGE)
        n, m = self.playlist
        head = QRectF(r.left() + PL_PAD * s, r.top() + PL_PAD * s,
                      r.width() - 2 * PL_PAD * s, PL_COUNTER_H * s)
        big = thin_font(46 * s, QFont.Bold)
        small = thin_font(26 * s, QFont.DemiBold)
        a, b = "%d曲目" % n, " / %d曲" % m
        aw = QFontMetricsF(big).horizontalAdvance(a)
        bw = QFontMetricsF(small).horizontalAdvance(b)
        x = head.center().x() - (aw + bw) / 2
        base = head.top() + head.height() * 0.5 + QFontMetricsF(big).ascent() * 0.36
        p.setFont(big)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QPointF(x, base), a)
        p.setFont(small)
        p.setPen(QColor(200, 222, 236))
        p.drawText(QPointF(x + aw, base), b)
        # 見出しの下の細い線。
        ly = head.bottom() + PL_GAP * s / 2
        p.setPen(QPen(QColor(150, 215, 235, 90), 1.0 * max(1.0, s)))
        p.drawLine(int(head.left()), int(ly), int(head.right()), int(ly))

    def _draw_course_row(self, p, cy, s):
        mark_d = 78 * s
        name = SLOT_NAMES.get(self.course, self.course)
        nf = title_font(40 * s)
        star = "★%d" % self.level if self.level is not None else "★—"
        sfnt = thin_font(38 * s, QFont.Bold)
        bfnt = thin_font(19 * s, QFont.Bold)
        # 自分で叩くとき(難易度設定のオート演奏 OFF。プレイリストはいつもオート)は札を出さない。
        auto = self.playlist is not None or bool(self.cfg.get("player_auto", True))
        badge = "オート演奏" if auto else ""
        gap = 26 * s
        name_w = QFontMetricsF(nf).horizontalAdvance(name)
        star_w = QFontMetricsF(sfnt).horizontalAdvance(star)
        badge_w = (QFontMetricsF(bfnt).horizontalAdvance(badge) + 30 * s) if badge else -gap
        badge_h = 38 * s
        total = mark_d + gap + name_w + gap + star_w + gap + badge_w
        x = self._cx() - total / 2

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
        if not badge:
            return
        br = QRectF(x, cy - badge_h / 2, badge_w, badge_h)
        # 難易度選択・結果と同じ細枠の札(#93a3bf)。
        p.setPen(QPen(QColor(147, 163, 191, 130), max(1.0, 1.0 * s)))
        p.setBrush(QColor(12, 24, 44, 200))
        p.drawRoundedRect(br, 3 * s, 3 * s)
        p.setFont(bfnt)
        p.setPen(QColor("#93a3bf"))
        p.drawText(br, Qt.AlignCenter, badge)
