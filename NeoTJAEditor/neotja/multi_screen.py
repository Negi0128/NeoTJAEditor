"""同時再生: 難易度ごとの帯を縦に積んで、同じ曲を一度に見る画面(鑑賞用)。

**帯**は本家の素材どおりの 1280x176 — 枠の上辺 8 + レーン 130 + 打音表記帯 29
+ 下辺 9 で、左のスコア枠(333x176)と下端がぴったり揃う固まり。これを難易度の
数だけ縦に積む(利用者の案 2026-10-03)。

    ┌ 1280x720 ───────────────────┐
    │ (黒)                         │
    │ [おに  ] スコア │ ●─●──●──  │ 176
    │ [むずかしい] スコア │ ●──●───  │ 176
    │ …                            │
    └──────────────────────────┘

決め事(利用者の指定):
  ・2〜5本(5本は利用者の承認 2026-11-14)。4本までは等倍で 1280x720 に
    収まる。5本は入りきらないので、帯の束をまるごと 0.8 倍に縮めて真ん中に
    置く(左右に 128px ずつ黒が出る)。band_scale と band_rect を参照
  ・帯の中身はそのまま(スコア・太鼓・ネームプレート・打音表記)
  ・背景も踊り子も出さない。地は黒
  ・叩いた音符が飛ぶ演出・スコアの加算文字は出さない
    (何本も同時に動くと、どの譜面を読んでいるか分からなくなる)
  ・「良」は**出す**(利用者の指定 2026-10-15。はじめは出さない決まりだった)。
    文字は帯の上辺より 61px 上に出るので、帯の切り抜きの中では頭が切れる。
    帯を全部描いたあとに切らずに重ねて描く(_paint_judge_pops)。上下の帯に
    かぶってよい、というのが利用者の指定。
  ・コース記号を押すと、その帯の打音だけを切れる(利用者の案 2026-10-15)。
    切った帯は記号を薄くして斜線を引く。

音は1本。同じ曲なので OFFSET も同じで、全部の帯へ同じ時刻を配るだけで
ズレようがない — 音源を複数まわして同期させる必要がそもそも無い。

録画の約束(begin_offline_render / set_render_time / end_offline_render)は
GameScreenWidget と同じにしてあるので、書き出しは1本のときと同じ手順で通る。

**塗る面は2つある**(ゲーム画面の GameScreenWidget / GameScreenGLWidget と
同じ分け方)。描く中身は _MultiBandBase.paint_screen の1本で、違うのは面だけ。
  ・MultiBandScreen   … CPU で塗る。録画と、GPU 描画を切ってあるとき。
  ・MultiBandGLScreen … GPU(QOpenGLWidget)で塗る。画面に出すときだけ。
選ぶのは make_multi_screen。
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from neotja.game_screen import (JUDGE_BOTTOM, JUDGE_POP_DROP, JUDGE_SCALE,
                                PANEL_H, PANEL_Y, SCREEN_H_FULL, SCREEN_W,
                                QOpenGLWidget)

#: 帯の上端と高さ(素材の箱。game_screen の左パネルと同じ値)。
BAND_TOP = PANEL_Y          # 188
BAND_H = PANEL_H            # 176
#: 並べられる本数。
MIN_BANDS = 2
MAX_BANDS = 5
#: 地の色。背景を出さないので、ここが画面の地になる。
BG_COLOR = "#000000"


#: 上に空ける高さ。モード切替・コース・録画・表示・fps のボタンを置く場所
#: (利用者の指定 2026-10-03: 最上部のボタンが見えるようにする)。
TOP_PAD = 40
#: 帯の下辺(枠の縁 9px)を次の帯で隠して詰める。4本を等倍で収めるのに
#: 要るのはこれだけで、レーンにも左パネルにも掛からない。
BAND_TRIM = 9

#: 打音を切った帯の、コース記号の濃さと斜線の色。左のパネルの地が赤なので、
#: 斜線は白で引く(赤系だと地に埋もれて、ただの飾りに見えた)。
MUTE_SYM_OPACITY = 0.3
MUTE_SLASH_COLOR = "#ffffff"
MUTE_SLASH_EDGE = "#201015"


def band_pitch() -> int:
    """帯どうしの間隔。下辺を重ねて詰める(4本で 677px)。"""
    return BAND_H - BAND_TRIM


def total_height(n: int) -> int:
    """n 本ぶんの高さ。いちばん下の帯だけ下辺まで出す。"""
    n = max(1, int(n))
    return band_pitch() * (n - 1) + BAND_H


#: 縮めずに並べられる本数。これを超えたら帯をまるごと縮める(案A)。
FULL_SIZE_BANDS = 4


def band_scale(n: int) -> float:
    """n 本のときの帯の倍率。4本までは 1.0(いまと同じ)。

    5本は 1280x720 に入りきらない(40 + 844 = 884)。上のボタンの 40px は
    そのまま残し、帯の束(n 本ぶんの高さ)だけを縮めて 680px に収める。
    倍率は 0.8 に丸める(5本で 40 + 675 = 715、下に 5px 残る)。端数の
    0.8057 だと 1px 単位の端数が帯ごとにずれて、境目に細い線が出やすい。"""
    n = max(1, int(n))
    if n <= FULL_SIZE_BANDS:
        return 1.0
    fit = (SCREEN_H_FULL - TOP_PAD) / float(total_height(n))
    return min(1.0, int(fit * 20) / 20.0)


class _MultiBandBase:
    """帯を縦に積んで描く入れ物(面に依らない部分。塗る面は下の2つ)。

    中の GameScreenWidget は**画面に出さない**。親子にすると帯の外(上の
    背景や下の余白)まで塗られてしまうので、ここが自分の painter へ
    位置をずらして呼ぶ。レーンを画面へ畳んである GameScreenWidget と
    同じ手口(game_screen.paint_screen のレーン描画を参照)。
    """

    #: 画面の大きさは常に 16:9。本数が変わっても窓の形を変えない
    #: (そのまま 720p で書き出せる)。
    WIDTH = SCREEN_W
    HEIGHT = SCREEN_H_FULL

    #: 自分で時刻を取りに行く刻み(120fps 相当)。画面に出すときは置く側が
    #: レーンと同じ刻みに合わせる(set_frame_ms)。
    FRAME_MS = 8

    #: 画面へ塗った回数。fps の表示が数える(録画で描いたぶんは入らない)。
    frames_painted = 0

    def __init__(self, screens=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAutoFillBackground(False)
        # コース記号の上で指の形にするのに要る(押していなくても報せが来る)。
        self.setMouseTracking(True)
        self._screens = []
        #: 打音を切ってある帯の番号。音を止めるのは外(preview_dock)の仕事で、
        #: ここは押された所を伝えて見た目を変えるだけ。
        self._muted = set()
        self._bg = QColor(BG_COLOR)
        #: キーを渡す先(本体のレーン)。
        self._key_target = None
        #: 時刻を取りに行く先と、前回描いた時刻(同じなら塗り直さない)。
        self._time_cb = None
        self._last_t = None
        self._frame_ms = self.FRAME_MS
        self._timer = QTimer(self)
        # 既定の粗いタイマーは Windows で 15ms 刻みに丸められる(レーンと同じ)。
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        if screens:
            self.set_screens(screens)

    # ------------------------------------------------------------------
    # 時計
    #
    # 帯は**自分では進まない**。外から「この時刻を描け」と渡す(録画と同じ道。
    # begin_offline_render)。各帯の時計に任せると、帯ごとに外挿の起点が
    # ずれて、同じ曲なのに本数ぶんバラバラの位置を描く(実際にそうなった)。
    #
    # 時刻を**自分のタイマーで取りに行く**のが肝。はじめはレーンの 120fps
    # クロックに相乗りしていたが、あちらはレーンが見えていないと回らない。
    # 曲は鳴っているのに帯が止まったままになった(利用者の報告 2026-10-03)。
    # ------------------------------------------------------------------
    def set_time_source(self, cb):
        """いまの音源時刻(秒)を返すものを受け取る。None で止める。"""
        self._time_cb = cb
        if cb is None:
            self._timer.stop()
        elif self.isVisible():
            self._timer.start(self._frame_ms)

    def set_frame_ms(self, ms):
        """時刻を取りに行く刻み(ms)。レーンのタイマーと同じ値を渡す。

        8ms のままだと GPU で塗っても 125fps で頭打ちになる(利用者の承認
        2026-11-14)。上限の設定(preview_max_fps)はレーンが読んで自分の
        刻みにしているので、ここはそれに倣うだけ。"""
        self._frame_ms = max(1, int(ms))
        if self._timer.isActive():
            self._timer.start(self._frame_ms)

    def _tick(self):
        cb = self._time_cb
        if cb is None:
            return
        try:
            t = float(cb())
        except Exception:  # noqa: BLE001
            return
        # 止まっているあいだは時刻が動かない。塗り直しも要らない。
        if self._last_t is not None and abs(t - self._last_t) < 1e-6:
            return
        self._last_t = t
        self.set_render_time(t)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._time_cb is not None:
            self._timer.start(self._frame_ms)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    # ------------------------------------------------------------------
    def set_screens(self, screens):
        """並べる画面(難易度ごとの GameScreenWidget)を入れ替える。

        渡す画面は compact(1280x360)で作っておくこと。帯の外は描かないので
        高さは使わないが、compact のほうが静的な下地が小さくて済む。"""
        self._screens = [s for s in (screens or []) if s is not None][:MAX_BANDS]
        for s in self._screens:
            # 帯の中で動くものを減らす(利用者の指定 2026-10-03)。
            # 「良」はここでは描かせない。帯の切り抜きで頭が切れるので、
            # 全部の帯を描いたあとに重ねる(_paint_judge_pops)。
            s.show_judge_pop = False
            s.show_hit_flights = False
            s.show_score_gain = False
            s.course_sym_opacity = 1.0
        # 番号と難易度の対応が変わるので、切ってあった覚えは捨てる。
        # 入れ直すのは外(preview_dock が難易度の key で覚えている)。
        self._muted = set()
        self.update()

    # ------------------------------------------------------------------
    # 帯ごとの打音の入り切り
    # ------------------------------------------------------------------
    def set_muted(self, indices):
        """打音を切ってある帯を入れ替える(見た目だけ)。"""
        self._muted = {int(i) for i in (indices or [])
                       if 0 <= int(i) < len(self._screens)}
        for i, gs in enumerate(self._screens):
            gs.course_sym_opacity = (MUTE_SYM_OPACITY if i in self._muted
                                     else 1.0)
        self.update()

    def muted(self):
        return set(self._muted)

    def scale(self) -> float:
        """いまの本数での帯の倍率(band_scale)。"""
        return band_scale(len(self._screens))

    def band_at(self, pos):
        """その点がどの帯か。帯の外なら None。"""
        for i in range(len(self._screens)):
            x, y, w, h = self.band_rect(i)
            if x <= pos.x() < x + w and y <= pos.y() < y + h:
                return i
        return None

    def course_sym_at(self, pos):
        """その点がコース記号の上なら帯の番号。違えば None。"""
        i = self.band_at(pos)
        if i is None:
            return None
        x, y, _w, _h = self.band_rect(i)
        s = self.scale()
        # 帯の中は「画面の座標 - 帯のずらし量」を倍率で割って見る
        # (paint_screen と同じ変換の逆)。
        r = self._screens[i].course_sym_rect()
        if r.contains(int((pos.x() - x) / s),
                      int((pos.y() - y) / s + BAND_TOP)):
            return i
        return None

    def screens(self):
        return list(self._screens)

    def count(self) -> int:
        return len(self._screens)

    def band_rect(self, i):
        """i 本目の帯の (x, y, w, h)。

        h は**切り抜く高さ**。いちばん下以外は下辺を次の帯に譲るので、
        帯の高さ(176)ではなく間隔(167)になる。

        上はボタンのぶん(TOP_PAD)を必ず空け、余ったぶんは下に残す。本数が
        少ないときに中央へ寄せると、ボタンの下に大きな黒い帯ができて
        収まりが悪い。"""
        n = max(1, len(self._screens))
        last = (i == n - 1)
        s = band_scale(n)
        if s == 1.0:
            return (0, TOP_PAD + i * band_pitch(), self.WIDTH,
                    BAND_H if last else band_pitch())
        # 縮めたときは、帯の幅も縮むぶん左右に黒が出る。真ん中に置く。
        w = self.WIDTH * s
        return ((self.WIDTH - w) / 2.0, TOP_PAD + i * band_pitch() * s, w,
                (BAND_H if last else band_pitch()) * s)

    def _band_transform(self, p: QPainter, i: int):
        """帯 i を描くための座標へ p を変える(中の画面は y=BAND_TOP に帯)。"""
        x, y, _w, _h = self.band_rect(i)
        s = self.scale()
        p.translate(x, y)
        if s != 1.0:
            p.scale(s, s)
        p.translate(0, -BAND_TOP)

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def paint_screen(self, p: QPainter):
        """画面一式を p へ描く(GameScreenWidget.paint_screen と同じ約束)。"""
        p.fillRect(0, 0, self.WIDTH, self.HEIGHT, self._bg)
        last = len(self._screens) - 1
        for i, gs in enumerate(self._screens):
            p.save()
            # 中の画面は自分の座標(帯は y=BAND_TOP)で描くので、その差だけ
            # ずらしてから、帯の外へ出ないように切る。切らないと上の背景や
            # 魂ゲージまで出てしまう。切り抜きは縮める前の寸法で取る。
            self._band_transform(p, i)
            p.setClipRect(0, BAND_TOP, self.WIDTH,
                          BAND_H if i == last else band_pitch())
            gs.paint_screen(p)
            if i in self._muted:
                self._paint_mute_mark(p, gs)
            p.restore()
        self._paint_judge_pops(p)

    def _paint_judge_pops(self, p: QPainter):
        """判定文字「良」を、帯の切り抜きの外へ重ねて描く。

        「良」の下端は帯の上辺より下(レーンに 15px かぶる所)だが、文字の丈が
        84px あるので上へ 61px はみ出す。帯ごとの切り抜きの中で描くと頭が
        切れてしまうので、帯を全部描いたあとに切らずに描く。上の帯のレーンに
        かぶってよい、というのが利用者の指定(2026-10-15)。

        帯より後に描くので、どの帯の「良」も必ず手前に出る。

        いちばん上の帯だけは、そのまま描くと**画面の外へ 21px 出て頭が
        欠ける**(上に空けてあるのはボタンぶんの 40px しかない)。はみ出す
        ぶんだけ下げる。下げるのはその帯だけで、他は素のままの位置。"""
        s = self.scale()
        for i, gs in enumerate(self._screens):
            _x, y, _w, _h = self.band_rect(i)
            p.save()
            self._band_transform(p, i)
            try:
                gs.draw_judge_pop(p, 0, self._judge_oy(gs, y, s))
            except Exception:  # noqa: BLE001
                pass
            p.restore()

    @staticmethod
    def _judge_oy(gs, band_y, scale=1.0):
        """「良」を上へずらす量。画面から出るぶんだけ負(=下げる)にする。"""
        try:
            cp = gs.chart_preview
            spr = cp.judge_sprite(cp.current_judge())
            h = int(spr.height() * JUDGE_SCALE) if spr is not None else 26
        except Exception:  # noqa: BLE001
            h = 26
        # 出た瞬間は JUDGE_POP_DROP ぶん上にいる。そこが頭。
        # 画面の上での頭の位置。縮めているときは帯の中の寸法も縮む。
        top = band_y + (JUDGE_BOTTOM - h - JUDGE_POP_DROP - BAND_TOP) * scale
        # 戻すのは帯の中の座標(縮める前)での量。
        return min(0.0, top / scale)

    def _paint_mute_mark(self, p: QPainter, gs):
        """打音を切った帯のしるし。コース記号に斜線を引く。

        記号そのものは薄くしてある(course_sym_opacity)。薄いだけだと
        「スキンがそういう絵」に見えてしまうので、線を1本足して
        切ってあることを分かるようにする。"""
        r = gs.course_sym_rect()
        m = 6
        x0, y0 = r.left() + m, r.bottom() - m
        x1, y1 = r.right() - m, r.top() + m
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(MUTE_SLASH_EDGE), 7))
        p.drawLine(x0, y0, x1, y1)
        p.setPen(QPen(QColor(MUTE_SLASH_COLOR), 4))
        p.drawLine(x0, y0, x1, y1)

    def paint_self(self):
        """自分の面へ1コマ描く(paintEvent / paintGL の中身)。"""
        self.frames_painted += 1
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self.paint_screen(p)
        p.end()

    # ------------------------------------------------------------------
    # キー操作
    #
    # ふつうのゲーム画面はレーンへ渡している(game_screen.keyPressEvent)。
    # 同時再生のあいだはこちらが画面なので、同じようにレーンへ渡さないと
    # スペース(再生/一時停止)も小節移動も効かない(利用者の報告 2026-10-03)。
    # 渡す先は**本体のレーン**で、帯の中のレーンではない — 音源と時計を
    # 持っているのはあちらだけ。
    # ------------------------------------------------------------------
    def set_key_target(self, widget):
        self._key_target = widget
        self.setFocusPolicy(Qt.StrongFocus if widget is not None
                            else Qt.NoFocus)

    def keyPressEvent(self, event):
        t = self._key_target
        if t is not None:
            t.keyPressEvent(event)
        if not event.isAccepted():
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        t = self._key_target
        if t is not None:
            t.keyReleaseEvent(event)
        if not event.isAccepted():
            super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        # 押したらキーが効くようにする(ゲーム画面と同じ)。accept() を必ず
        # 返すこと — ScaledHost が「誰も受け取らなかった」と判断して親へ
        # 送り返すと、行き場のないイベントが往復して落ちる。
        self.setFocus(Qt.MouseFocusReason)
        if event.button() == Qt.LeftButton:
            i = self.course_sym_at(event.position())
            if i is not None:
                self.toggle_mute(i)
        event.accept()

    def mouseMoveEvent(self, event):
        """コース記号の上では指の形にして、押せることを知らせる。"""
        over = self.course_sym_at(event.position()) is not None
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        event.accept()

    def toggle_mute(self, i: int):
        """帯 i の打音を入り切りする。音を止めるのは受け取った側の仕事。"""
        if not (0 <= i < len(self._screens)):
            return
        m = set(self._muted)
        m.symmetric_difference_update({i})
        self.set_muted(m)
        self.muteToggled.emit(i)

    def wheelEvent(self, event):
        """ホイールで移動(ゲーム画面と同じ。時間で送る)。"""
        t = self._key_target
        if t is not None:
            try:
                t.wheelEvent(event)
                return
            except Exception:  # noqa: BLE001
                pass
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 時計(1本の時刻を全部の帯へ配る)
    # ------------------------------------------------------------------
    def set_playback(self, seconds, playing):
        for gs in self._screens:
            cp = getattr(gs, "chart_preview", None)
            if cp is not None:
                cp.set_playback(seconds, playing)

    # ---- 録画。GameScreenWidget と同じ約束 ----
    def begin_offline_render(self):
        for gs in self._screens:
            gs.begin_offline_render()

    def set_render_time(self, seconds):
        for gs in self._screens:
            gs.set_render_time(seconds)

    def end_offline_render(self):
        for gs in self._screens:
            gs.end_offline_render()


class MultiBandScreen(_MultiBandBase, QWidget):
    """CPU で塗る帯の束。録画と、GPU 描画を切ってあるときはこちら。

    録画は QWidget.render() で QImage へ描く(recorder.make_offline_multi_widget)。
    render() で他の描き先へ描けるのはこちらだけ。"""

    #: コース記号を押して、その帯の打音を入り切りした(帯の番号を渡す)。
    muteToggled = Signal(int)

    def paintEvent(self, event):
        self.paint_self()


class MultiBandGLScreen(_MultiBandBase, QOpenGLWidget or QWidget):
    """GPU で塗る帯の束。画面に出すときだけ使う(利用者の承認 2026-11-14)。

    CPU 版は1コマの塗りに 5ms 前後かかり、そのうえ窓ぜんたいの組み立てで
    もう 5ms ほど取られて、92fps あたりで頭打ちだった。こちらは塗りが
    GPU に乗り、組み立ても絵を貼るだけで済む。

    中身は CPU 版と同じ paint_screen。GameScreenGLWidget と同じく:
      ・paintEvent を持たせない(QOpenGLWidget の段取りが飛ぶ)。
      ・render() で他の描き先へは描けない。録画は CPU 版を作って使う
        (recorder.make_offline_multi_widget)。縮小表示は入れ物が paint_screen を
        直に呼ぶので、こちらのままで通る。
    """

    muteToggled = Signal(int)

    def paintGL(self):
        self.paint_self()


def make_multi_screen(gpu=False, parent=None):
    """画面に出す帯の束を作る。gpu=True で GPU 版(使えなければ CPU 版)。

    録画からは呼ばないこと(MultiBandScreen を直に作る)。"""
    if gpu and QOpenGLWidget is not None:
        return MultiBandGLScreen(parent=parent)
    return MultiBandScreen(parent=parent)


def make_band_screen(preview_data, offset, se_text_enabled=True):
    """帯1本ぶん(難易度1つ)の画面を、画面に出さずに組み立てる。

    録画の make_offline_widget と同じ作り方。compact にするのは、下の背景・
    どんちゃん・踊り子をそもそも描かせないため。"""
    from neotja.chart_preview_widget import ChartPreviewWidget
    from neotja.game_screen import GameScreenWidget

    data = preview_data or {}
    cp = ChartPreviewWidget()
    cp.set_se_text_enabled(bool(se_text_enabled))
    # 叩くたびの火花も出す(利用者の指定 2026-11-14「同時再生の時、判定枠の
    # 火花表示してほしい」)。はじめは判定円の光りだけにしていた(2026-10-03)。
    # 火花はレーンより大きい絵だが、帯の切り抜き(paint_screen)の中で描くので
    # 上下の帯へははみ出さない。
    cp.set_effects_lite(False)
    cp.set_preview_data(data)
    cp.set_offset(offset)
    gs = GameScreenWidget(cp, compact=True)
    gs.set_chart(data, data.get("course_key"))
    # 魂ゲージは帯の外(上の背景)にあるので、積むと置き場所が無い。
    # 軽量と同じ扱いにして描かせない。
    gs.set_lite(True)
    return gs
