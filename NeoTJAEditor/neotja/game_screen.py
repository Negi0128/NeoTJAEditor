"""本家(太鼓の達人 / TNDE)の画面構成を再現する合成ウィジェット。

譜面レーンそのものは既存の ChartPreviewWidget が描く。ここはその周りを組み
立てる係で、背景・左パネル・フッターを敷き、レーンを実測位置へ置くだけ。
レーンの描画ロジックには一切触っていない(寸法だけ set_lane_geometry で
本家に合わせる)。

座標は TNDE のゲーム内キャプチャ(1280x720 ネイティブ)から実測した:

    y=  0..188   上部背景(どんちゃん・曲名・魂ゲージ)
    y=188..364   レーン一式   左パネル 333x176 / レーン x=333
    y=192..322   └ レーン本体 947x130   (333+947 = 1280 ちょうど)
    y=322..348   └ 打音表記帯 947x26
    y=360..720   下部背景(踊り子) 1280x360
    y=676..720   フッター 1280x44
    判定円の中心 x=414, y=257   (レーン左端から 81px)

FULL(録画用)は 1280x720 全部、COMPACT(再生モード)は上部背景とレーン一式
だけの 1280x360。どちらも同じ座標系なので、切り替えても位置がずれない。
"""

import os

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from neotja import gauge as gauge_mod
from neotja import settings as settings_mod
from neotja.chart_preview_widget import ChartPreviewWidget

SCREEN_W = 1280
SCREEN_H_FULL = 720
SCREEN_H_COMPACT = 360

# --- 実測した配置 --------------------------------------------------------
BG_TOP_H = 188            # 上部背景の高さ
PANEL_X, PANEL_Y = 0, 188      # 左パネル(スコア/コンボ/太鼓/銘板)
PANEL_W, PANEL_H = 333, 176
LANE_X, LANE_Y = 333, 192      # レーン本体の左上
LANE_W, LANE_H = 947, 130
SE_STRIP_H = 26                # 打音表記帯(レーン本体の直下)
JUDGE_X_IN_LANE = 81           # レーン左端から判定円の中心まで
BG_DOWN_Y, BG_DOWN_H = 360, 360
FOOTER_Y, FOOTER_H = 676, 44

# --- 左パネルの中身(本家スクショから採った位置) ----------------------
# 数字シートの1文字は 29.3x31.3。本家のスコアは高さ25px前後だったので
# 0.8 倍で置く(1.6 倍にしたら6桁がパネルからはみ出した)。
SCORE_RIGHT, SCORE_Y = 178, 198      # スコアは右詰め
SCORE_SCALE = 1.02
# 数字シートは1文字ぶんの枠(29.3px)に余白を含むので、そのまま送ると字間が
# 空きすぎる。本家は字が詰まっているので送り幅を枠の 76% にする。
SCORE_ADVANCE = 0.76
COURSE_SYM_POS = (26, 237)           # コース記号(おに 等)
DRUM_POS = (203, 199)                # 太鼓 120x133
# コンボの数字と「コンボ」文字は太鼓の上に載るが、太鼓だけを動かしたときに
# 一緒に動いてほしくないので、位置の基準は別に持つ。
COMBO_ANCHOR = (200, 196)
NAMEPLATE_POS = (-25, 291)           # 1P 銘板(素材の左余白ぶん外へ出す)
# コンボ数字は太鼓の中心にそろえると本家よりわずかに右へ寄って見えるので、
# 少し左へずらす。
COMBO_X_OFF = 0
COMBO_Y_OFF = 19                     # 太鼓の上端からコンボ数字までの距離
COMBO_TEXT_Y_OFF = 65                # 同 「コンボ」文字まで
COMBO_TEXT_X_OFF = 3                 # 「コンボ」文字の左右微調整
COMBO_SCALE = 0.954                  # 本家に合わせて 1.06 から 0.9 倍
COMBO_ADVANCE = 0.80
# Combo/Text.png (100x100) には「コンボ」が縦に2つ入っている(通常色と金色)。
# 実測では y=26..48 と y=76..98 で、間に空きがある。中身の範囲を単純に
# 2等分すると金色側だけ十数px下へずれるので、素材から不透明な帯を実際に
# 測って使う(_measure_combo_text_bands)。測れなかったときの保険がこれ。
COMBO_TEXT_BAND_FALLBACK = ((26, 23), (76, 23))   # ((y, 高さ), ...)
# コンボ数字の色: 0-49 白 / 50-99 銀 / 100- 金
COMBO_SILVER_AT, COMBO_GOLD_AT = 50, 100
# 太鼓が光る時間(叩いた瞬間からの秒数)。本家もごく短い。
DRUM_GLOW_SEC = 0.09

# --- 魂ゲージ ------------------------------------------------------------
# Gauge.png / Gauge_Base.png は 700x68 だが、ゲージ本体は上の 44px だけ。
# 残り(y=44..67)は使い回しの「クリア」文字が左下に詰め込まれているだけなので
# 切り捨てる。本体の形は「y=0..17 は x=547 から(クリア圏の背の高い部分)、
# y=18..43 は x=1 から(通常圏)」という段付き。
GAUGE_BAR_H = 44
# 黒枠(Taiko_Frame)の上帯も同じ段付きで、背の高い側が枠の x=715 から始まる。
# 段の位置を合わせると 枠左端331 + 715 - 547 = 499 がゲージの左端になる。
GAUGE_POS = (499, 144)
# クリア(ノルマ)の位置。本家は内部10000点中8000点＝80%。素材の段差
# (背の高いクリア圏の始まり)は実測 547/697 = 78.5% とわずかに手前だが、
# 魂が光るかどうかはゲームの規則どおり 80% で判定する。
GAUGE_CLEAR_RATIO = gauge_mod.CLEAR_RATIO
# 魂の文字 (Soul.png 80x160 = 80x80 が2段。上段=通常 / 下段=クリア)。
# ゲージ右端(499+697=1196)と枠の右端(331+950=1281)の間、85px の窓に収める。
SOUL_CELL = 80
SOUL_POS = (1198, 126)

# レーンと魂ゲージを囲む黒枠(1P_Frame.png 951x224)。アルファを測ったところ
# 中の透明な窓が y=56..185 = 高さ130 で、レーン本体とぴったり同じだった。
# よって窓をレーン(y=192)に合わせると frame_y = 192-56 = 136。横は 951 が
# レーン947 + 左右2pxの縁なので frame_x = 333-2 = 331。
# 上端(y=0..55)がゲージの載る黒帯、下端(y=220..)がレーン下の縁になる。
LANE_FRAME_POS = (LANE_X - 2, LANE_Y - 56)
# 枠のうち「レーンを囲う部分」(y=56 以降)は上の位置で固定。上の黒帯だけは
# 独立して上下できるようにする — 帯とゲージが重なって潰れるのを避けるため。
# 帯だけ下げてもレーンの窓は動かないので、レーンとの1px合わせは崩れない。
# 素材の y=48..55 はレーン箱の上辺(黒枠)なので、ここは動かしてはいけない。
# 動かしてよいのは魂ゲージを囲う箱(y=0..47)だけ。
FRAME_TOP_BAND = 48          # 素材のうち「ゲージの箱」はここまで
# 素材の y=190..214 の x=0 に、打音表記帯と同じ灰色 (140,140,142) の列が
# 1px だけ入っている。本家は帯が枠の内側ぎりぎりまで来るので繋がるが、
# こちらは帯がレーンと同じ x=333 から始まるため、この1pxだけが黒の中に
# 浮いて「何かが重なっている」ように見える。その列は描かない。
FRAME_SLIVER_Y0, FRAME_SLIVER_Y1 = 190, 215
FRAME_TOP_X_OFF = 5          # ゲージの箱だけ右へずらす量
FRAME_TOP_Y_OFF = 1          # ゲージの箱だけ下へずらす量

# 背景の絵(祭りの風景/下段/フッター)はまだ入れない。無地で出す。
SHOW_BACKGROUND = False

# 連打数と判定文字「良」はレーンより上(黒枠の帯の上)に出す。レーンの
# ウィジェットは帯ぴったりの高さしか無く、上へはみ出して描けないので、
# この2つは画面側(親)が描く。判定円の真上、魂ゲージの左に収まる位置。
# --- ゴーゴー / 魂MAX の演出 --------------------------------------------
# GoGoSplash.png 6900x460 = 230x460 が30コマ。下から吹き上がる金色の火花で、
# ゴーゴーが始まった瞬間に一度だけ流れる(ループしない)。下端中央アンカー。
GOGO_SPLASH_CELL = (230, 460)
GOGO_SPLASH_FRAMES = 30
GOGO_SPLASH_FRAME_SEC = 1.0 / 30.0
GOGO_SPLASH_SCALE = 0.55
GOGO_SPLASH_BOTTOM = LANE_Y + LANE_H   # 火花の足元をレーン下端に置く

# 魂ゲージが満タン(入魂)のあいだ、ゲージが虹色に変わる。
# Rainbow/<コース>/0..11.png 696x44。マスクがゲージ本体と dx=0,dy=0 で一致
# するので、ゲージと同じ位置にそのまま重ねるだけでよい。
GAUGE_RAINBOW_FRAMES = 12
GAUGE_RAINBOW_FRAME_SEC = 1.0 / 20.0

# 1P_Explosion.png 3240x180 = 180x180 が18コマ。先頭2コマは完全に透明。
# 12個の丸が輪になって広がるワンショット。中心アンカー。
# クリア(ノルマ)に到達した瞬間、魂の文字の後ろで開く。
SOUL_BURST_CELL = 180
SOUL_BURST_FRAMES = 18
SOUL_BURST_FIRST = 2
SOUL_BURST_FRAME_SEC = 1.0 / 30.0
SOUL_BURST_SCALE = 1.0

# --- 連打数(金の扇) ------------------------------------------------------
# 11_Balloon/Roll.png 1670x204 = 334x204 が5コマ。閉じた状態から開いていき、
# 最後のコマだけ「連打!!」の札が付く。数字は専用シート
# 11_Balloon/Number_Roll.png 630x75 = 63x75 が10文字(0..9)。
# 本家キャプチャ(1333px幅)で扇はおよそ x345..470 / y25..150 に見えたので、
# 1280 換算で幅 120px 前後。素材の絵は 231px 幅なので約 0.52 倍で置く。
ROLL_FAN_CELL = (334, 204)
ROLL_FAN_FRAMES = 5
ROLL_FAN_SCALE = 0.52
ROLL_FAN_CENTER_X = 398          # 扇の中心 x
ROLL_FAN_BOTTOM = 152            # 扇の下端 y
ROLL_NUM_CELL = (63, 75)
ROLL_NUM_SCALE = 0.60
ROLL_NUM_ADVANCE = 0.86          # 字送り(セル幅に対する割合)
ROLL_NUM_CENTER = (398, 96)      # 数字のかたまりの中心

# --- 風船・くす玉(白い吹き出し) ------------------------------------------
# 11_Balloon/Balloon.png 200x160。中身は x13..186 / y3..154 で、左下に尻尾。
# 数字は連打と同じ Number_Roll.png を使う。どんちゃんは描かない。
BALLOON_SCALE = 0.93             # 0.62 の 1.5 倍
BALLOON_CENTER_X = 438           # 吹き出しの中心 x
BALLOON_BOTTOM = 195             # 吹き出し(尻尾の先)の下端 y
BALLOON_NUM_SCALE = 0.72         # 0.60 の 1.2 倍
# 数字は吹き出しの楕円の中心に置く。素材(200x160)のうち下の尖りは尻尾なので
# 中心には含めず、楕円だけの中心 (100, 64) を基準にする。こうしておくと
# 吹き出しを拡大・移動しても数字が置いていかれない。
BALLOON_NUM_ANCHOR = (100, 64)
BALLOON_NUM_OFF = (-5, 5)        # そこからの微調整 (右, 下)

TAP_COUNT_BOTTOM = LANE_Y - 4    # (素材が無いときの文字表示)連打数の下端
JUDGE_BOTTOM = LANE_Y + 21       # 「良」の下端。レーンに 21px かぶる
JUDGE_SCALE = 1.05               # 「良」の拡大率
JUDGE_POP_RISE = 13.0            # 「良」が昇る高さ
JUDGE_POP_SEC = 0.34             # ポップの持続

# --- スコアの加算表示 ----------------------------------------------------
# 音符を叩くたびに、入った点をスコアの上へ浮かべて消す。数字は Score_Plate の
# 2段目(橙)を使う — 本家も加算分だけ色が違う。
SCORE_GAIN_SEC = 0.5             # 出てから消えるまで
SCORE_GAIN_RISE = 16.0           # 昇る高さ
SCORE_GAIN_SCALE = 0.902
SCORE_GAIN_ROW = 1               # Score_Plate.png の段(0=白 1=橙 2=水)
SCORE_GAIN_Y_OFF = 4             # スコアの上端からさらに上へ(正=下)
# 「良」を描くオーバーレイの大きさ(判定円の中心を基準にした矩形)。
# レーンより手前に重ねる必要があるので、レーンの兄弟ウィジェットにする。
OVERLAY_RECT = (-130, 88, 260, 250)    # (dx, y, w, h) dx は判定円中心からの左端


class _JudgeOverlay(QWidget):
    """判定文字「良」だけを描く、レーンより手前の板。

    「良」はレーンに少しかぶる位置に出したいが、親(画面)は子(レーン)より
    先に描かれるので、親に描くとレーンに隠れてしまう。レーンの兄弟として
    重ねることで手前に出す。背景は塗らない(下のレーン・黒枠が透ける)。
    """

    def __init__(self, screen):
        super().__init__(screen)
        self._screen = screen
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # ゴーゴー開始の火花(GoGoSplash)は判定枠まわりがうるさくなるので
        # 出さない。素材と描画関数は残してあるので、戻すならここを
        # 呼び直すだけでよい。
        recent = self._screen.judge_pop()
        if recent is None:
            p.end()
            return
        elapsed = recent[0]
        if not (0.0 <= elapsed < JUDGE_POP_SEC):
            p.end()
            return
        spr = self._screen.chart_preview.judge_sprite()
        jp = elapsed / JUDGE_POP_SEC
        rise = JUDGE_POP_RISE * (1.0 - (1.0 - jp) ** 2)
        p.setOpacity(max(0.0, 1.0 - jp))
        cx = self.width() // 2
        bottom = JUDGE_BOTTOM - self.y() - rise
        if spr is not None:
            w = int(spr.width() * JUDGE_SCALE)
            h = int(spr.height() * JUDGE_SCALE)
            p.drawPixmap(QRect(int(cx - w / 2), int(bottom - h), w, h), spr)
        else:
            p.setPen(QColor("#f5c518"))
            f = p.font()
            f.setPointSize(int(20 * JUDGE_SCALE))
            f.setBold(True)
            p.setFont(f)
            p.drawText(int(cx - 40), int(bottom - 26), 80, 26, Qt.AlignCenter, "良")
        p.end()


class GameScreenWidget(QWidget):
    """1280x720(または上半分だけの 1280x360)の画面を組み立てる。

    compact=True は「通常再生モード」用で、下部背景と踊り子を描かないぶん
    軽く、窓も小さい。録画は compact=False で全部描く。
    """

    def __init__(self, chart_preview: ChartPreviewWidget, compact=False, parent=None):
        super().__init__(parent)
        self.chart_preview = chart_preview
        self._compact = bool(compact)
        self._skin = {}
        self._score_timeline = None
        self._gauge = None
        self._clear_time = None
        self._course_key = None
        self._course_sym = None
        self._load_skin()

        chart_preview.setParent(self)
        # レーンの寸法を本家に合わせる。上下の余白は 0 にして、レーン本体と
        # 打音表記帯だけの高さ(130+26)にする — 余白ぶんの情報(連打カウント等)
        # は画面側の余白に描くほうが本家に近い。
        chart_preview.set_lane_geometry(LANE_W, LANE_H, JUDGE_X_IN_LANE,
                                        top_margin=0, bottom_margin=0)
        # コンボはこちらが左パネルの太鼓の上に描くので、レーン内には出さない。
        chart_preview._hide_lane_combo = True
        chart_preview.move(LANE_X, LANE_Y)

        self.setFixedSize(SCREEN_W, SCREEN_H_COMPACT if compact else SCREEN_H_FULL)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        # レーンが update() しても、Qt が塗り直すのはレーンの矩形だけ。
        # スコア・コンボ・太鼓・魂ゲージ・「良」はどれもレーンの外にあるので、
        # 放っておくと一度描かれたきり止まって見える。ここで毎フレーム
        # 塗り直す。レーンに重ならない2つの矩形だけを指定して、レーンを
        # 二重に描かせない。
        # 「良」はレーンにかぶるので、レーンより手前の板に描く。
        ox, oy, ow, oh = OVERLAY_RECT
        self._judge_overlay = _JudgeOverlay(self)
        self._judge_overlay.setGeometry(LANE_X + JUDGE_X_IN_LANE + ox, oy, ow, oh)
        self._judge_overlay.raise_()

        self._hud_timer = QTimer(self)
        self._hud_timer.setInterval(max(1, chart_preview._timer.interval()))
        self._hud_timer.timeout.connect(self._tick_hud)
        # 動かすのは表示されているあいだだけ。録画用の画面はずっと非表示の
        # まま render() されるだけなので、そこでタイマーを回す意味がない。

    def showEvent(self, event):
        super().showEvent(event)
        self._hud_timer.start()

    def hideEvent(self, event):
        self._hud_timer.stop()
        super().hideEvent(event)

    # --- 録画(オフライン描画)用の受け渡し ---------------------------------
    # 録画側は「1つのウィジェットに時刻を渡して render() する」だけの作りに
    # してある。画面ごと録るときも同じ扱いにできるよう、時刻の出し入れは
    # レーンへそのまま流す。HUD はレーンの現在値から描くので、これだけで
    # スコアもコンボも魂ゲージも録画に乗る。
    def begin_offline_render(self):
        self.chart_preview.begin_offline_render()

    def set_render_time(self, seconds):
        self.chart_preview.set_render_time(seconds)

    def end_offline_render(self):
        self.chart_preview.end_offline_render()

    def draw_gogo_splash(self, p, ox, oy):
        """ゴーゴーが始まった瞬間の金色の火花。オーバーレイ(レーンより手前)
        から呼ぶ。ox/oy はそのオーバーレイの左上。"""
        sheet = self._skin.get("gogo_splash")
        if sheet is None:
            return
        try:
            now = self.chart_preview.game_state()[0]
            regions = self.chart_preview.gogo_regions()
        except Exception:  # noqa: BLE001
            return
        span = GOGO_SPLASH_FRAMES * GOGO_SPLASH_FRAME_SEC
        f = None
        for g0, _g1 in regions:
            el = now - g0
            if 0.0 <= el < span:
                f = int(el / GOGO_SPLASH_FRAME_SEC)
                break
        if f is None:
            return
        cw, ch = GOGO_SPLASH_CELL
        k = GOGO_SPLASH_SCALE
        dw, dh = cw * k, ch * k
        cx = LANE_X + JUDGE_X_IN_LANE
        p.drawPixmap(QRect(int(cx - dw / 2 - ox), int(GOGO_SPLASH_BOTTOM - dh - oy),
                           int(dw), int(dh)),
                     sheet, QRect(min(f, GOGO_SPLASH_FRAMES - 1) * cw, 0, cw, ch))

    def judge_pop(self):
        """直近ヒット (経過秒, 音符の文字, コンボ番号)。オーバーレイ用。"""
        try:
            return self.chart_preview.game_state()[2]
        except Exception:  # noqa: BLE001
            return None

    def _tick_hud(self):
        if not self.isVisible():
            return
        # 上の帯: 魂ゲージ・魂・黒枠・連打数
        self.update(0, 0, SCREEN_W, LANE_Y)
        # 左パネル: スコア・コース記号・太鼓・コンボ・銘板
        self.update(PANEL_X, PANEL_Y, PANEL_W, PANEL_H)
        # 「良」の板(レーンの手前)
        self._judge_overlay.update()

    # ------------------------------------------------------------------
    def _load_skin(self):
        """skin/ から使う絵を読む。無ければ None のままで、描画側が黙って飛ばす
        (スキンは同梱しない外部パックなので、無くても動くのが前提)。"""
        base = str(settings_mod.skin_dir())
        for key, rel in (
            ("bg_top", "Background.png"),
            ("bg_down", "Bg_down.png"),
            ("footer", "Footer.png"),
            ("panel", "Taiko_Background.png"),
            ("lane_frame", "Taiko_Frame.png"),
            ("drum", os.path.join("Combo", "Base.png")),
            ("drum_don", os.path.join("Combo", "Don.png")),
            ("drum_ka", os.path.join("Combo", "Ka.png")),
            ("combo_white", os.path.join("Combo", "Digits.png")),
            ("combo_silver", os.path.join("Combo", "DigitsSilver.png")),
            ("combo_gold", os.path.join("Combo", "DigitsGold.png")),
            ("combo_text", os.path.join("Combo", "Text.png")),
            ("score_digits", "Score_Plate.png"),
            ("nameplate", "NamePlate.png"),
            ("gauge", "Gauge.png"),
            ("gauge_base", "Gauge_Base.png"),
            ("soul", "Soul.png"),
            ("roll_fan", "Roll.png"),
            ("roll_num", "Number_Roll.png"),
            ("balloon", "Balloon.png"),
            ("gogo_splash", "GoGoSplash.png"),
            ("soul_burst", "SoulExplosion.png"),
        ):
            path = os.path.join(base, rel)
            if os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self._skin[key] = pm
        self._combo_text_bands = self._measure_combo_text_bands()
        self._gauge_rainbow = self._load_gauge_rainbow()

    def _load_gauge_rainbow(self):
        """skin/GaugeRainbow/0..11.png を読む。1枚でも欠けたら None。"""
        base = os.path.join(str(settings_mod.skin_dir()), "GaugeRainbow")
        out = []
        for i in range(GAUGE_RAINBOW_FRAMES):
            path = os.path.join(base, "%d.png" % i)
            if not os.path.exists(path):
                return None
            pm = QPixmap(path)
            if pm.isNull():
                return None
            out.append(pm)
        return out

    def _measure_combo_text_bands(self):
        """Combo/Text.png の「コンボ」2つ(通常色/金色)の縦位置を測る。

        戻り値は ((y, 高さ), (y, 高さ))。素材の上下に余白があり、2つの間にも
        空きがあるので、範囲を等分するのではなく不透明な行のかたまりを
        そのまま拾う。拾えなければ実測値の定数へ落とす。"""
        ct = self._skin.get("combo_text")
        if ct is None:
            return COMBO_TEXT_BAND_FALLBACK
        try:
            import numpy as np
            img = ct.toImage().convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            a = np.frombuffer(memoryview(img.constBits()), dtype=np.uint8)
            a = a.reshape(h, img.bytesPerLine() // 4, 4)[:, :w, 3]
            rows = a.max(axis=1) > 16
            runs, start = [], None
            for y in range(h):
                if rows[y] and start is None:
                    start = y
                elif not rows[y] and start is not None:
                    runs.append((start, y - start))
                    start = None
            if start is not None:
                runs.append((start, h - start))
            if len(runs) >= 2:
                return (runs[0], runs[1])
        except Exception:  # noqa: BLE001
            pass
        return COMBO_TEXT_BAND_FALLBACK

    def set_chart(self, preview_data: dict, course_key=None):
        """譜面が変わったときに呼ぶ。スコアの配点をここで決めておく
        (毎フレーム計算しないで済むよう、時刻→点数の表を先に作る)。"""
        from neotja.score import ScoreTimeline

        self._score_timeline = ScoreTimeline(preview_data or {})
        # 魂ゲージの伸び方(おに基準)。譜面が決まればランクが決まる。
        self._gauge = gauge_mod.GaugeModel(preview_data or {})
        # クリアに届く音符の時刻。魂のバーストをそこから流す。毎フレーム
        # 探さずに済むよう譜面が決まった時点で1回だけ求めておく。
        try:
            self._clear_time = self.chart_preview.note_time(self._gauge.notes_to_clear)
        except Exception:  # noqa: BLE001
            self._clear_time = None
        self._course_key = course_key or (preview_data or {}).get("course_key")
        self._course_sym = None
        if self._course_key:
            # コース記号は Easy/Normal/Hard/Oni/Edit の5種。
            name = {"easy": "Easy", "normal": "Normal", "hard": "Hard",
                    "oni": "Oni", "edit": "Edit", "tower": "Oni",
                    "ura": "Oni"}.get(str(self._course_key).lower())
            if name:
                path = os.path.join(str(settings_mod.skin_dir()), "CourseSymbol", name + ".png")
                if os.path.exists(path):
                    pm = QPixmap(path)
                    self._course_sym = pm if not pm.isNull() else None
        self.update()

    # ------------------------------------------------------------------
    def _draw_digits(self, p, sheet, value, *, cols=10, rows=1, row=0,
                     right=None, left=None, y=0, scale=1.0, advance=1.0):
        """0-9 が横に並んだシートから数字を描く。right 指定で右詰め。

        advance は「次の字までどれだけ送るか」を1文字枠に対する割合で指定する。
        シートの1枠には字の左右に余白が入っているため、1.0 のまま送ると
        本家より字間が空いて間延びして見える。"""
        if sheet is None:
            return
        cw, ch = sheet.width() / cols, sheet.height() / rows
        w, h = cw * scale, ch * scale
        step = w * advance
        s = str(int(value))
        x = (right - step * len(s)) if right is not None else (left or 0)
        for c in s:
            i = int(c)
            p.drawPixmap(QRect(int(x), int(y), int(w) + 1, int(h) + 1), sheet,
                         QRect(int(i * cw), int(row * ch), int(cw), int(ch)))
            x += step

    def _draw_left_panel(self, p, combo, score, recent, now):
        """左パネル: スコア / コース記号 / 太鼓 + コンボ / 銘板。"""
        # --- スコア(右詰め) ---
        self._draw_digits(p, self._skin.get("score_digits"), score,
                          cols=10, rows=3, row=0, advance=SCORE_ADVANCE,
                          right=SCORE_RIGHT, y=SCORE_Y, scale=SCORE_SCALE)

        # --- スコアの加算分(スコアの上へ浮かんで消える) ---
        if self._score_timeline is not None:
            ev = self._score_timeline.last_event(now)
            if ev is not None:
                et, gain = ev
                el = now - et
                if 0.0 <= el < SCORE_GAIN_SEC and gain > 0:
                    q = el / SCORE_GAIN_SEC
                    rise = SCORE_GAIN_RISE * (1.0 - (1.0 - q) ** 2)
                    sheet = self._skin.get("score_digits")
                    if sheet is not None:
                        gh = sheet.height() / 3 * SCORE_GAIN_SCALE
                        p.setOpacity(max(0.0, 1.0 - q))
                        self._draw_digits(p, sheet, gain, cols=10, rows=3,
                                          row=SCORE_GAIN_ROW, advance=SCORE_ADVANCE,
                                          right=SCORE_RIGHT,
                                          y=int(SCORE_Y - gh + SCORE_GAIN_Y_OFF - rise),
                                          scale=SCORE_GAIN_SCALE)
                        p.setOpacity(1.0)

        # --- コース記号 ---
        if self._course_sym is not None:
            p.drawPixmap(COURSE_SYM_POS[0], COURSE_SYM_POS[1], self._course_sym)

        # --- 太鼓 ---
        drum = self._skin.get("drum")
        dx, dy = DRUM_POS
        if drum is not None:
            p.drawPixmap(dx, dy, drum)
            # 叩いた瞬間だけ、その音符の色で光らせる(面=赤 / 縁=水色)。
            if recent is not None:
                elapsed, char, n = recent
                if 0.0 <= elapsed < DRUM_GLOW_SEC:
                    glow = self._skin.get("drum_don" if char in "13" else "drum_ka")
                    if glow is not None:
                        # 本家は両面同時ではなく片面ずつ。音符ごとに
                        # 左→右→左…と交互に光らせる。
                        gw, gh = glow.width(), glow.height()
                        half = gw // 2
                        sx = 0 if (n % 2 == 0) else half
                        # 叩いた直後がいちばん明るく、すぐ消える。
                        p.setOpacity(max(0.0, 1.0 - elapsed / DRUM_GLOW_SEC))
                        p.drawPixmap(dx + sx, dy, glow, sx, 0, gw - half, gh)
                        p.setOpacity(1.0)

        # --- コンボ(太鼓の上に重ねる) ---
        # 位置の基準は太鼓ではなく COMBO_ANCHOR。太鼓だけを動かしても
        # コンボが一緒に動かないようにするため。
        cx0, cy0 = COMBO_ANCHOR
        dw = drum.width() if drum is not None else 120
        if combo > 0:
            key = ("combo_gold" if combo >= COMBO_GOLD_AT else
                   "combo_silver" if combo >= COMBO_SILVER_AT else "combo_white")
            sheet = self._skin.get(key)
            if sheet is not None:
                step = sheet.width() / 10 * COMBO_SCALE * COMBO_ADVANCE
                # 基準の中心に対して左右対称に置く。
                right = cx0 + dw // 2 + int(step * len(str(combo))) // 2 + COMBO_X_OFF
                self._draw_digits(p, sheet, combo, right=right, advance=COMBO_ADVANCE,
                                  y=cy0 + COMBO_Y_OFF, scale=COMBO_SCALE)
            ct = self._skin.get("combo_text")
            if ct is not None:
                # 素材には「コンボ」が縦に2つ入っている。上段=通常色 /
                # 下段=金 とみなし、数字の色に合わせて片方だけを切り出す。
                # 位置は実測した帯を使うので、どちらの色でも同じ場所に出る。
                bands = getattr(self, "_combo_text_bands", COMBO_TEXT_BAND_FALLBACK)
                src_y, band = bands[1] if combo >= COMBO_GOLD_AT else bands[0]
                p.drawPixmap(QRect(cx0 + dw // 2 - ct.width() // 2 + COMBO_TEXT_X_OFF,
                                   cy0 + COMBO_TEXT_Y_OFF, ct.width(), band),
                             ct, QRect(0, src_y, ct.width(), band))

        # --- 銘板 ---
        np_ = self._skin.get("nameplate")
        if np_ is not None:
            p.drawPixmap(NAMEPLATE_POS[0], NAMEPLATE_POS[1], np_)

    def _draw_gauge(self, p, ratio, now):
        """魂ゲージ。全良前提なので「叩いた数 / 総数」で満ちていく。"""
        base = self._skin.get("gauge_base")
        fill = self._skin.get("gauge")
        gx, gy = GAUGE_POS
        ratio = max(0.0, min(1.0, ratio))
        if base is not None:
            p.drawPixmap(gx, gy, base, 0, 0, base.width(), GAUGE_BAR_H)
        if fill is not None:
            wpx = int(fill.width() * ratio)
            if wpx > 0:
                p.drawPixmap(gx, gy, fill, 0, 0, wpx, GAUGE_BAR_H)
        # 入魂(満タン)のあいだはゲージが虹色になる。素材のマスクがゲージ本体と
        # 一致しているので、同じ位置に重ねるだけで色だけ入れ替わる。
        if ratio >= 1.0 and self._gauge_rainbow:
            i = int(now / GAUGE_RAINBOW_FRAME_SEC) % len(self._gauge_rainbow)
            p.drawPixmap(gx, gy, self._gauge_rainbow[i])

        # クリア(ノルマ)に届いた瞬間、魂の文字の後ろで輪が開く。
        burst = self._skin.get("soul_burst")
        if burst is not None and self._clear_time is not None:
            el = now - self._clear_time
            n = SOUL_BURST_FRAMES - SOUL_BURST_FIRST
            if 0.0 <= el < n * SOUL_BURST_FRAME_SEC:
                f = SOUL_BURST_FIRST + int(el / SOUL_BURST_FRAME_SEC)
                c = SOUL_BURST_CELL
                d = c * SOUL_BURST_SCALE
                cx = SOUL_POS[0] + SOUL_CELL / 2.0
                cy = SOUL_POS[1] + SOUL_CELL / 2.0
                p.drawPixmap(QRect(int(cx - d / 2), int(cy - d / 2), int(d), int(d)),
                             burst, QRect(f * c, 0, c, c))

        # 魂の文字。ゲージの右端に置き、クリア圏まで溜まったら光る段に変える。
        soul = self._skin.get("soul")
        if soul is not None:
            row = 1 if ratio >= GAUGE_CLEAR_RATIO else 0
            p.drawPixmap(SOUL_POS[0], SOUL_POS[1], soul,
                         0, row * SOUL_CELL, SOUL_CELL, SOUL_CELL)

    def _draw_lane_readouts(self, p, now, recent):
        """連打・風船の打数を、本家と同じ金の扇で出す。
        (「良」はレーンにかぶるので _JudgeOverlay が手前に描く)"""
        try:
            count, kind = self.chart_preview.live_tap_state(now)
        except Exception:  # noqa: BLE001
            count, kind = None, None
        if count is None:
            return

        num = self._skin.get("roll_num")
        # --- 風船・くす玉: 白い吹き出しに残り打数 ---
        if kind == "balloon":
            bl = self._skin.get("balloon")
            if bl is not None and num is not None:
                bw, bh = bl.width() * BALLOON_SCALE, bl.height() * BALLOON_SCALE
                p.drawPixmap(QRect(int(BALLOON_CENTER_X - bw / 2),
                                   int(BALLOON_BOTTOM - bh), int(bw), int(bh)), bl)
                bx0 = BALLOON_CENTER_X - bw / 2.0
                by0 = BALLOON_BOTTOM - bh
                ncx = bx0 + BALLOON_NUM_ANCHOR[0] * BALLOON_SCALE + BALLOON_NUM_OFF[0]
                ncy = by0 + BALLOON_NUM_ANCHOR[1] * BALLOON_SCALE + BALLOON_NUM_OFF[1]
                nw, nh = ROLL_NUM_CELL
                gw, gh = nw * BALLOON_NUM_SCALE, nh * BALLOON_NUM_SCALE
                step = gw * ROLL_NUM_ADVANCE
                text = str(int(count))
                x = ncx - step * len(text) / 2.0
                y = ncy - gh / 2.0
                for c in text:
                    p.drawPixmap(QRect(int(x), int(y), int(gw) + 1, int(gh) + 1),
                                 num, QRect(int(c) * nw, 0, nw, nh))
                    x += step
                return
            # 素材が無ければ下の文字表示へ落とす。

        fan = self._skin.get("roll_fan")
        if kind == "balloon" or fan is None or num is None:
            # 素材が無い環境では従来どおり文字で出す。
            jx = LANE_X + JUDGE_X_IN_LANE
            p.setPen(QColor("#ffd24a"))
            f = p.font()
            f.setPointSize(22)
            f.setBold(True)
            p.setFont(f)
            p.drawText(jx - 100, TAP_COUNT_BOTTOM - 34, 200, 34,
                       Qt.AlignHCenter | Qt.AlignBottom, str(count))
            return

        # 扇は叩くほど開く。最後のコマ(「連打!!」付き)で止める。
        cw, ch = ROLL_FAN_CELL
        frame = max(0, min(ROLL_FAN_FRAMES - 1, int(count)))
        dw, dh = cw * ROLL_FAN_SCALE, ch * ROLL_FAN_SCALE
        p.drawPixmap(QRect(int(ROLL_FAN_CENTER_X - dw / 2),
                           int(ROLL_FAN_BOTTOM - dh), int(dw), int(dh)),
                     fan, QRect(frame * cw, 0, cw, ch))

        # 打数。専用の数字シート(0..9 が横に10個)を中央そろえで。
        nw, nh = ROLL_NUM_CELL
        gw, gh = nw * ROLL_NUM_SCALE, nh * ROLL_NUM_SCALE
        step = gw * ROLL_NUM_ADVANCE
        text = str(int(count))
        x = ROLL_NUM_CENTER[0] - step * len(text) / 2.0
        y = ROLL_NUM_CENTER[1] - gh / 2.0
        for c in text:
            p.drawPixmap(QRect(int(x), int(y), int(gw) + 1, int(gh) + 1),
                         num, QRect(int(c) * nw, 0, nw, nh))
            x += step

    def set_compact(self, compact: bool):
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedSize(SCREEN_W, SCREEN_H_COMPACT if compact else SCREEN_H_FULL)
        self.update()

    def is_compact(self) -> bool:
        return self._compact

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        h = self.height()
        # スキンが無い環境でも「黒い箱」にならないよう下地は必ず塗る。
        p.fillRect(self.rect(), QColor("#0d1117"))

        # --- 上部背景 (0..188 が見える範囲) ---
        bg = self._skin.get("bg_top") if SHOW_BACKGROUND else None
        if bg is not None:
            # 素材(1280x316)を 188px で切ると絵が途中で断ち切られるので、
            # 丸ごと描いて下端(188 以降)はレーン一式で隠す。地面の線が
            # レーンの上に来るので、本家と同じ「奥行きのある」見え方になる。
            p.drawPixmap(0, 0, bg)

        if not self._compact and SHOW_BACKGROUND:
            # --- 下部背景 (360..720) ---
            bd = self._skin.get("bg_down")
            if bd is not None:
                p.drawPixmap(0, BG_DOWN_Y, bd)
            # --- フッター (676..720) ---
            ft = self._skin.get("footer")
            if ft is not None:
                p.drawPixmap(0, FOOTER_Y, ft)

        # --- 左パネル (0,188 - 333x176) ---
        panel = self._skin.get("panel")
        if panel is not None:
            p.drawPixmap(PANEL_X, PANEL_Y, panel)
        else:
            p.fillRect(QRect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H), QColor("#8c1d1d"))

        # --- レーンと魂ゲージを囲む黒枠 ---
        # 素材そのもの。中の窓は透明なのでレーン(子ウィジェット)がそのまま見える。
        frame = self._skin.get("lane_frame")
        if frame is not None:
            fx, fy = LANE_FRAME_POS
            # 魂ゲージを囲う箱(素材の y=0..FRAME_TOP_BAND)だけ独立して動かす。
            p.drawPixmap(fx + FRAME_TOP_X_OFF, fy + FRAME_TOP_Y_OFF, frame,
                         0, 0, frame.width(), FRAME_TOP_BAND)
            # レーンを囲う部分(上辺・左右・下辺)は動かさない。透明窓がレーンと
            # 1px 合わせなので、ここを動かすとレーンとずれる。
            # ただし y=190..214 の左端1pxだけは打音表記帯の色が入っていて、
            # こちらの帯とは繋がらず浮いて見えるので、その帯だけ x=1 から描く。
            s0, s1 = FRAME_SLIVER_Y0, FRAME_SLIVER_Y1
            fw = frame.width()
            p.drawPixmap(fx, fy + FRAME_TOP_BAND, frame,
                         0, FRAME_TOP_BAND, fw, s0 - FRAME_TOP_BAND)
            p.drawPixmap(fx + 1, fy + s0, frame, 1, s0, fw - 1, s1 - s0)
            p.drawPixmap(fx, fy + s1, frame, 0, s1, fw, frame.height() - s1)
        else:
            p.fillRect(QRect(LANE_X, LANE_Y - 2, LANE_W, 2), QColor(0, 0, 0, 220))
            p.fillRect(QRect(LANE_X, LANE_Y + LANE_H + SE_STRIP_H, LANE_W, 2),
                       QColor(0, 0, 0, 220))

        # --- HUD(スコア・コンボ・太鼓・ゲージ) ---
        # ゲージは黒枠の上端に載るので、枠を描いたあとに描く。
        # 現在値はレーン側が持っているものをそのまま使う。HUD 用に別のカウントを
        # 持たないので、シークしても再生を止めてもズレようがない。
        try:
            now, combo, recent = self.chart_preview.game_state()
        except Exception:  # noqa: BLE001
            now, combo, recent = 0.0, 0, None
        score = self._score_timeline.at(now) if self._score_timeline else 0
        # 魂ゲージは「叩いた数 × ランク / 10000」。音符数で決まるランクが
        # 1個あたりの点なので、譜面の7割半ばで入魂して以降は満タンのまま
        # — 最後の音符でちょうど満タンになる線形の伸び方とは違う。
        self._draw_left_panel(p, combo, score, recent, now)
        self._draw_gauge(p, self._gauge.ratio(combo) if self._gauge else 0.0, now)
        self._draw_lane_readouts(p, now, recent)

        p.end()
        # レーン本体は子ウィジェット(ChartPreviewWidget)が自分で描く。

    # ------------------------------------------------------------------
    def lane_rect(self) -> QRect:
        """レーン本体の矩形(照合・デバッグ用)。"""
        return QRect(LANE_X, LANE_Y, LANE_W, LANE_H)

    def judge_center(self):
        """判定円の中心(画面座標)。実測値と突き合わせるのに使う。"""
        return (LANE_X + JUDGE_X_IN_LANE, LANE_Y + LANE_H // 2)
