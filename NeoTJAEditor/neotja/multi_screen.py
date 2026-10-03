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
  ・2〜4本。4本なら 704px で 1280x720 にちょうど収まる
  ・帯の中身はそのまま(スコア・太鼓・ネームプレート・打音表記)
  ・背景も踊り子も出さない。地は黒
  ・「良」・叩いた音符が飛ぶ演出・スコアの加算文字は出さない
    (4本ぶん同時に動くと、どの譜面を読んでいるか分からなくなる)

音は1本。同じ曲なので OFFSET も同じで、全部の帯へ同じ時刻を配るだけで
ズレようがない — 音源を複数まわして同期させる必要がそもそも無い。

録画の約束(begin_offline_render / set_render_time / end_offline_render)は
GameScreenWidget と同じにしてあるので、書き出しは1本のときと同じ手順で通る。
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QVBoxLayout, QWidget

from neotja.game_screen import PANEL_H, PANEL_Y, SCREEN_H_FULL, SCREEN_W

#: 帯の上端と高さ(素材の箱。game_screen の左パネルと同じ値)。
BAND_TOP = PANEL_Y          # 188
BAND_H = PANEL_H            # 176
#: 並べられる本数。
MIN_BANDS = 2
MAX_BANDS = 4
#: 地の色。背景を出さないので、ここが画面の地になる。
BG_COLOR = "#000000"


#: 上に空ける高さ。モード切替・コース・録画・表示・fps のボタンを置く場所
#: (利用者の指定 2026-10-03: 最上部のボタンが見えるようにする)。
TOP_PAD = 40
#: 帯の下辺(枠の縁 9px)を次の帯で隠して詰める。4本ぶんの場所を作るのに
#: 要るのはこれだけで、レーンにも左パネルにも掛からない。
BAND_TRIM = 9


def band_pitch() -> int:
    """帯どうしの間隔。下辺を重ねて詰める(4本で 677px)。"""
    return BAND_H - BAND_TRIM


def total_height(n: int) -> int:
    """n 本ぶんの高さ。いちばん下の帯だけ下辺まで出す。"""
    n = max(1, int(n))
    return band_pitch() * (n - 1) + BAND_H


class MultiBandScreen(QWidget):
    """帯を縦に積んで描く入れ物。

    中の GameScreenWidget は**画面に出さない**。親子にすると帯の外(上の
    背景や下の余白)まで塗られてしまうので、ここが自分の painter へ
    位置をずらして呼ぶ。レーンを画面へ畳んである GameScreenWidget と
    同じ手口(game_screen.paint_screen のレーン描画を参照)。
    """

    #: 画面の大きさは常に 16:9。本数が変わっても窓の形を変えない
    #: (そのまま 720p で書き出せる)。
    WIDTH = SCREEN_W
    HEIGHT = SCREEN_H_FULL

    #: 自分で時刻を取りに行く刻み(120fps 相当)。
    FRAME_MS = 8

    def __init__(self, screens=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAutoFillBackground(False)
        self._screens = []
        self._bg = QColor(BG_COLOR)
        #: キーを渡す先(本体のレーン)。
        self._key_target = None
        #: 時刻を取りに行く先と、前回描いた時刻(同じなら塗り直さない)。
        self._time_cb = None
        self._last_t = None
        self._timer = QTimer(self)
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
            self._timer.start(self.FRAME_MS)

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
            self._timer.start(self.FRAME_MS)

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
            s.show_judge_pop = False
            s.show_hit_flights = False
            s.show_score_gain = False
        self.update()

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
        return (0, TOP_PAD + i * band_pitch(), self.WIDTH,
                BAND_H if last else band_pitch())

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def paint_screen(self, p: QPainter):
        """画面一式を p へ描く(GameScreenWidget.paint_screen と同じ約束)。"""
        p.fillRect(0, 0, self.WIDTH, self.HEIGHT, self._bg)
        for i, gs in enumerate(self._screens):
            x, y, w, h = self.band_rect(i)
            p.save()
            # 中の画面は自分の座標(帯は y=BAND_TOP)で描くので、その差だけ
            # ずらしてから、帯の外へ出ないように切る。切らないと上の背景や
            # 魂ゲージまで出てしまう。
            p.translate(x, y - BAND_TOP)
            p.setClipRect(0, BAND_TOP, w, h)
            gs.paint_screen(p)
            p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self.paint_screen(p)

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
        event.accept()

    def wheelEvent(self, event):
        """ホイールで小節移動(ゲーム画面と同じ)。"""
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


def make_band_screen(preview_data, offset, se_text_enabled=True):
    """帯1本ぶん(難易度1つ)の画面を、画面に出さずに組み立てる。

    録画の make_offline_widget と同じ作り方。compact にするのは、下の背景・
    どんちゃん・踊り子をそもそも描かせないため。"""
    from neotja.chart_preview_widget import ChartPreviewWidget
    from neotja.game_screen import GameScreenWidget

    data = preview_data or {}
    cp = ChartPreviewWidget()
    cp.set_se_text_enabled(bool(se_text_enabled))
    # 叩くたびの火花は出さない(判定円の光りだけにする)。4本ぶん出ると
    # 判定円のまわりの音符が隠れる(利用者の指定 2026-10-03)。
    # **ゴーゴーの炎は別物で、こちらは残る**(paint_gogo_fire)。
    cp.set_effects_lite(True)
    cp.set_preview_data(data)
    cp.set_offset(offset)
    gs = GameScreenWidget(cp, compact=True)
    gs.set_chart(data, data.get("course_key"))
    # 魂ゲージは帯の外(上の背景)にあるので、積むと置き場所が無い。
    # 軽量と同じ扱いにして描かせない。
    gs.set_lite(True)
    return gs
