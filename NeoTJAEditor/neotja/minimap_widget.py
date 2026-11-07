# -*- coding: utf-8 -*-
"""曲まるごとを1本の帯にした、譜面つきのシークバー。

利用者の要望 2026-11-07:
  「peepoの下側みたいなシークバーがあったら操作しやすいかもなあと」
  「シークバーって、peepoみたいに譜面を極度に抽象化できない？」
  「1本の線で、音符の配色だけ表示した図でいいよ　音符ないところは暗い
    灰色で」

曲の頭から終わりまでを帯の幅いっぱいに割り当て、音符のある所だけその色
(ドン=赤 / カッ=青 / 連打・風船=黄)を塗る。無い所は暗い灰色。大小は
区別しない(配色だけ、という指定)。

帯の上の赤い線が再生位置、白い枠がいま作譜ペインに見えている範囲。
押した所・引いた所へシークする。
"""

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class ChartMinimap(QWidget):
    """曲ぜんたいの譜面を1本の帯で出す。押すとそこへシークする。"""

    #: シークしてほしい(秒。**助走こみの音源の時刻**)。
    seekRequested = Signal(float)

    #: 帯の高さ(px)。
    BAR_H = 16
    #: 上下の余白。
    PAD_V = 5
    #: 音符1つの太さ(px)。
    TICK_W = 2

    #: 音符が無い所の色。
    EMPTY_COLOR = "#32363f"
    #: 枠の色。
    EDGE_COLOR = "#14161b"
    #: 再生位置の線。
    PLAYHEAD_COLOR = "#ff3b30"
    #: いま見えている範囲の枠。
    VIEW_EDGE = (255, 255, 255, 150)
    VIEW_FILL = (255, 255, 255, 30)

    #: 音符の文字 → 色の鍵(theme のパレット)。
    _KEY_OF = {"1": "don", "3": "don", "2": "ka", "4": "ka"}

    #: 色をどれだけ落とすか。帯は曲ぜんたいぶんが一度に目に入るので、
    #: レーンと同じ濃さだと**うるさい**(利用者の指定 2026-11-07
    #: 「色もっと暗くしてほしいな　情報量が少し多い」)。
    DIM = 0.58

    @classmethod
    def _dim(cls, name):
        c = QColor(name)
        if not c.isValid():
            return name
        return QColor(int(c.red() * cls.DIM), int(c.green() * cls.DIM),
                      int(c.blue() * cls.DIM)).name()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.BAR_H + self.PAD_V * 2)
        self.setMinimumWidth(80)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._notes = []          # [(音源時刻, 色の鍵)]
        self._duration = 0.0
        self._position = 0.0
        self._view = None         # (始まり, 終わり) 音源時刻
        self._pal = {k: self._dim(v) for k, v in
                     (("don", "#ff6b6b"), ("ka", "#5bc8fc"),
                      ("roll", "#f5c518"))}
        self._strip = None        # 焼いた帯
        self._strip_key = None

    # ------------------------------------------------------------------
    # 外から入れるもの
    # ------------------------------------------------------------------
    def set_palette_colors(self, pal):
        """テーマの色を入れる(don / ka / roll)。帯では DIM ぶん落として使う。"""
        for k in ("don", "ka", "roll"):
            if pal.get(k):
                self._pal[k] = self._dim(pal[k])
        self._strip_key = None
        self.update()

    def set_chart(self, notes, spans=None, offset=0.0, duration=0.0):
        """譜面を入れる。

        notes は build_preview_timeline の "notes"([(譜面時刻, 文字, ...)])。
        spans は (連打, 風船, くす玉) の区間。offset は譜面時刻→音源時刻の
        ずらし(音源時刻 = 譜面時刻 - offset。助走ぶんもここに入っている)。
        """
        out = []
        for n in notes or []:
            try:
                t = float(n[0]) - float(offset)
                c = str(n[1])
            except (TypeError, ValueError, IndexError):
                continue
            key = self._KEY_OF.get(c)
            if key is None:
                continue          # 0 や 8 は音符として出さない
            out.append((t, key))
        for group in (spans or ()):
            for sp in group or ():
                try:
                    a = float(sp[0]) - float(offset)
                    b = float(sp[1]) - float(offset)
                except (TypeError, ValueError, IndexError):
                    continue
                out.append((a, "roll"))
                out.append((b, "roll"))
                # 区間の中も塗る(両端だけだと連打が点に見える)
                out.append(((a + b) * 0.5, "roll"))
                out.append((a, "rollspan", b))
        self._notes = out
        self._duration = max(0.0, float(duration or 0.0))
        self._strip_key = None
        self.update()

    def set_duration(self, seconds):
        seconds = max(0.0, float(seconds or 0.0))
        if abs(seconds - self._duration) < 1e-6:
            return
        self._duration = seconds
        self._strip_key = None
        self.update()

    # 絵が変わらない知らせでは塗り直さない。曲ぜんたいを1本に詰めている
    # ので、1px ぶん動くのに何百ミリ秒もかかる。知らせは 60Hz で来るのに
    # 毎回塗り直していて、**GL 画面の隣で毎回ウィンドウ合成が走り**、
    # 作譜モードの fps が 1/3 になっていた(利用者の報告 2026-11-07
    # 「LYRICとシークバーを追加してから平均fpsが100くらいに落ちてしまって
    #  重い」。実測 59 → 173 fps)。
    #
    # 変わるときも**変わった所だけ**塗る(赤い線の筋と、枠の動いたぶん)。
    def _px(self, sec):
        return int(round(self._x_of(sec)))

    def _mark_column(self, x):
        """赤い線1本ぶんの塗り直し(太さに少し余裕を持たせる)。"""
        self.update(x - 2, 0, 5, self.height())

    def set_position(self, seconds):
        """赤い線(音源時刻)。"""
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if abs(seconds - self._position) < 1e-4:
            return
        was = self._px(self._position)
        self._position = seconds
        now = self._px(seconds)
        if now == was:
            return                      # 同じ画素。絵は変わらない
        self._mark_column(was)
        self._mark_column(now)

    def set_view(self, t0, t1):
        """いま作譜ペインに見えている範囲(音源時刻)。None で消す。"""
        v = None if t0 is None or t1 is None else (float(t0), float(t1))
        if v == self._view:
            return
        old = self._view
        self._view = v
        if (old is None) != (v is None):
            self.update()
            return
        if v is None:
            return
        a0, b0 = (self._px(min(old)), self._px(max(old)))
        a1, b1 = (self._px(min(v)), self._px(max(v)))
        if (a0, b0) == (a1, b1):
            return                      # 同じ画素。絵は変わらない
        if abs(a1 - a0) > 40 or abs(b1 - b0) > 40:
            self.update()               # 大きく飛んだ。まとめて塗る
            return
        for x in (a0, a1, b0, b1):      # 枠の縦線が動いたぶんだけ
            self._mark_column(x)

    # ------------------------------------------------------------------
    # 位置の計算
    # ------------------------------------------------------------------
    def _x_of(self, sec):
        if self._duration <= 0:
            return 0.0
        w = max(1, self.width())
        return w * max(0.0, min(float(sec), self._duration)) / self._duration

    def _sec_of(self, x):
        if self._duration <= 0:
            return 0.0
        w = max(1, self.width())
        return max(0.0, min(1.0, float(x) / w)) * self._duration

    # ------------------------------------------------------------------
    # 描く
    # ------------------------------------------------------------------
    def _build_strip(self):
        """音符の帯を1枚に焼く。幅と中身が変わらないかぎり使い回す。"""
        dpr = self.devicePixelRatioF()
        w, h = max(1, self.width()), self.BAR_H
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(self.EMPTY_COLOR))
        if self._duration > 0 and self._notes:
            q = QPainter(pm)
            try:
                # 連打の区間を先に(音符が上に乗るように)
                for item in self._notes:
                    if len(item) < 3:
                        continue
                    a, _k, b = item
                    x0, x1 = self._x_of(a), self._x_of(b)
                    q.fillRect(QRectF(x0, 0, max(1.0, x1 - x0), h),
                               QColor(self._pal["roll"]))
                tw = float(self.TICK_W)
                for item in self._notes:
                    if len(item) >= 3:
                        continue
                    t, key = item
                    x = self._x_of(t)
                    q.fillRect(QRectF(x - tw * 0.5, 0, tw, h),
                               QColor(self._pal.get(key, "#cccccc")))
            finally:
                q.end()
        return pm

    def paintEvent(self, _e):
        p = QPainter(self)
        y = self.PAD_V
        h = self.BAR_H
        key = (self.width(), self._duration, len(self._notes),
               tuple(sorted(self._pal.items())), self.devicePixelRatioF())
        if self._strip is None or key != self._strip_key:
            self._strip = self._build_strip()
            self._strip_key = key
        p.drawPixmap(0, y, self._strip)
        # 枠
        p.setPen(QColor(self.EDGE_COLOR))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0.5, y + 0.5, max(1, self.width() - 1), h - 1))
        # いま見えている範囲
        if self._view is not None and self._duration > 0:
            a, b = sorted(self._view)
            x0, x1 = self._x_of(a), self._x_of(b)
            p.setBrush(QColor(*self.VIEW_FILL))
            p.setPen(QColor(*self.VIEW_EDGE))
            p.drawRect(QRectF(x0, y + 0.5, max(2.0, x1 - x0), h - 1))
            p.setBrush(Qt.NoBrush)
        # 再生位置
        if self._duration > 0:
            x = self._x_of(self._position)
            p.setPen(QColor(self.PLAYHEAD_COLOR))
            p.drawLine(int(x), y - 2, int(x), y + h + 2)
        p.end()

    # ------------------------------------------------------------------
    # 押す / 引く
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self._duration <= 0:
            return
        self._emit_seek(e.position().x())
        e.accept()

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.LeftButton) or self._duration <= 0:
            return
        self._emit_seek(e.position().x())
        e.accept()

    def _emit_seek(self, x):
        sec = self._sec_of(x)
        self.set_position(sec)
        self.seekRequested.emit(sec)
