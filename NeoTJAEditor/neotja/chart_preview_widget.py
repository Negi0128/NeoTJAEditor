import bisect
import os
import time as _time

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QRadialGradient,
    QStaticText,
)
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QWidget

from neotja import settings as settings_mod
from neotja import theme
from neotja.theme import COLORS

NOTE_COLOR = {"1": "don", "2": "ka", "3": "don", "4": "ka"}
NOTE_BIG = {"3", "4"}
GOGO_TINT = QColor(255, 90, 90, 55)
DEFAULT_BPM = 120.0
# 判定文字「良」の色。本家太鼓の GOOD 判定と同じ金色。テーマに依らず固定
# (レーンは常にダーク基調のため)。このプレビューは全ノーツを自動で・正確な
# 時刻に叩く静的可視化なので、判定は常に「良」になる(可/不可は出ない)。
JUDGE_GOOD = QColor(255, 206, 70)

# --- 叩いた音符の飛び方 (PeepoDrumKit の GameNoteHitPath 移植) -------------
# chart_editor_widgets_game.cpp:5-38 の `GameNoteHitPath`: 60fps 換算で
# 0..30 フレーム (= 0.5 秒) の 31 点 2D 経路。ワールド座標 (y は下が正)。
# 音符は右上へ跳ね上がり、17 フレーム目付近で頂点に達してから落ちていく
# 放物線状の弧を描く。値はそのままの転記(スケーリングは下の
# _scaled_hit_path で行う)。
_HIT_PATH_RAW = (
    (615, 386), (639, 342), (664, 300), (693, 260), (725, 222),
    (758, 186), (793, 153), (830, 122), (870, 93), (912, 66),
    (954, 43), (1001, 27), (1046, 11), (1094, -2), (1142, -14),
    (1192, -18), (1240, -22), (1292, -23), (1336, -22), (1385, -16),
    (1435, -8), (1479, 3), (1526, 16), (1570, 36), (1612, 56),
    (1658, 83), (1696, 115), (1734, 144), (1770, 176), (1803, 210),
    (1836, 247),
)
_HIT_PATH_FPS = 60.0
# PeepoDrumKit の縦方向の基準は GameLaneSlice.Content = 195 world units
# (chart_editor_theme.h:30)。素直なレーン高さ比は 106/195 ≈ 0.5436 で、
# 音符半径比 (28 / GameHitCircle.InnerOutlineRadius 50 = 0.56) ともほぼ一致
# する。**が、それは採用していない**: 弧の頂点は開始点から 409 world units も
# 上にあり (17 フレーム目の y = -23 対 開始 386)、0.5436 倍でも 222 px 上へ
# 飛ぶ。原典はレーン (264) の 4 倍以上あるビューポート全体にクリップしている
# のでその放物線が丸ごと見えるが、こちらのレーン枠は高さ 106 px 固定で、
# そこにクリップされる以上 0.5436 倍だと音符は 57 ms で枠外へ消えてしまい、
# 「放物線」が一度も見えない(直線的な閃光にしか見えない)。
#
# そこで **等方スケールのまま**(=曲線の形は原典と完全に同一)、頂点で音符の
# 中心がちょうどレーン帯の上端に来る倍率を選ぶ:
#     HIT_PATH_SCALE = (LANE_HEIGHT / 2) / 409 ≈ 0.1296
# これで弧の全体が固定枠の中に収まり、上がって・被さって・落ちてくる動きが
# そのまま見える。レーンの比率は一切変えていない。
_PEEPO_LANE_CONTENT_H = 195.0
# 弧の頂点の、開始点からの上向き変位 (world units)。テーブルから直接求める。
_HIT_PATH_APEX_RISE = _HIT_PATH_RAW[0][1] - min(y for _x, y in _HIT_PATH_RAW)  # 409


def _scaled_hit_path(scale: float):
    """`_HIT_PATH_RAW` を「開始点からの相対オフセット」に変換し、`scale` を
    掛けたタプルを返す(PeepoDrumKit も `SampleBezierFCurve(...) -
    GameNoteHitPath[0].Value` と開始点を引いている)。"""
    bx, by = _HIT_PATH_RAW[0]
    return tuple(((x - bx) * scale, (y - by) * scale) for x, y in _HIT_PATH_RAW)


def _pil_to_qpixmap(img) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


class ChartPreviewWidget(QWidget):
    """Fixed-judgment-line, real-time scrolling note preview (taiko-simulator
    style), synced to the audio playback position.

    Notes/rolls/bar-lines are pre-flattened into sorted chart-time lists once
    per chart edit (see set_preview_data) rather than walked fresh every
    frame, and paintEvent only iterates the slice within the visible time
    window (bisect for notes/bars, a plain overlap filter for rolls since
    that list is much shorter) so redraws stay cheap regardless of how large
    the chart is. Note sprites (if the user has a notes.png sheet configured
    for image export) are cropped and scaled once up front rather than per
    frame, for the same reason.

    Spacing (PeepoDrumKit-style): each note/roll/bar carries the BPM active
    when it occurred, and its on-screen speed is BASE_PIXELS_PER_BEAT * bpm /
    60 rather than one fixed real-time scroll speed - so a 16th-note run
    always looks like a 16th-note run regardless of the song's tempo, instead
    of getting cramped at high BPM or stretched out at low BPM.

    QMediaPlayer's positionChanged fires somewhat irregularly and its
    reported position can jitter by a few ms either way. Naively re-anchoring
    the extrapolation on every signal turns that jitter into a visible
    stutter, so small deltas (under RESYNC_THRESHOLD_SEC) are ignored and the
    smooth 16ms-timer extrapolation is left alone; only a real drift or seek
    re-anchors it."""

    # PeepoDrumKit の拡張レーン(広い窓)は 1 拍 = GameWorldSpaceDistancePerLane
    # Beat(356) を多めに見せて詰まって見えるので、こちらも 4 拍ぶん見せて
    # 177px/拍 に詰める(レーンの総ピクセル幅は 200+4*177 で従来とほぼ同じ)。
    # 本家(TNDE)のスクリーンショット実測: 16分の隣接間隔がどこも 60px
    # (連打の切れ目も 358px≒6個ぶん / 298px≒5個ぶん と、60px の格子に
    # ぴったり乗っていた)。よって 1拍 = 60*4 = 240px。
    # 以前は 177px で、本家の 74% しかなく音符が詰まって見えていた。
    BASE_PIXELS_PER_BEAT = 240.0
    WINDOW_REF_BPM = 60.0  # lower bound used only to size the visible-time window (see _visible_window)
    JUDGE_X = 200.0            # fixed pixel offset - not a ratio of widget width, so it never moves on resize
    LOOKAHEAD_BEATS = 4.0      # one full 4/4 measure ahead
    # 単体表示(本家レイアウトを使わないとき)のレーン幅。1拍を広げても窓の
    # 大きさが変わらないよう、従来どおりの実寸で固定しておく。本家レイアウトの
    # ときは set_lane_geometry() が 947 に差し替える。
    LANE_WIDTH = 908.0
    # 音符の半径。スキン(Notes.png)を使うときは素材の透明な余白ごと
    # この直径へ縮めるため、見た目の円は半径より一回り小さく出る。本家の
    # キャプチャと 1:1 で並べて、円の直径が実測 62px に見えるまで上げた値。
    NOTE_R_SMALL = 38
    NOTE_R_BIG = 52
    # 判定円。本家(TNDE)のキャプチャ実測値: 外径 106px(半径53)、内側の輪は
    # 半径35。音符がこの内側にすっぽり入る比率になっている。
    JUDGE_RING_R = 53
    JUDGE_RING_R_INNER = 35
    LANE_HEIGHT = NOTE_R_BIG * 2 + 30  # fixed box height too, so resizing the window can't stretch it vertically either
    TOP_MARGIN = 56    # room above the lane box for the live roll/balloon count readout
    # 打音表記 (SE text) strip, directly under the note band and inside the
    # lane box. PeepoDrumKit draws the syllable horizontally centered on the
    # note but vertically in a dedicated footer slice below the lane content
    # (DrawGamePreviewNoteSEText, chart_editor_widgets_game.cpp:241-245,
    # offsetting by FooterCenterY() - ContentCenterY()); its slice is
    # Content=195 / Footer=39, i.e. the footer is 20% of the content height.
    # 20% of LANE_HEIGHT would be 21 px, rounded up to 26 here so 12-14 pt
    # kana stay legible at this much smaller scale. Putting the text here
    # (rather than on the note) keeps it off the red/blue note fills, clear
    # of the roll/balloon count in TOP_MARGIN and of the combo panel, which
    # only covers the note band.
    SE_FOOTER_HEIGHT = 26
    BOTTOM_MARGIN = 24
    # The SE footer strip only costs height when 打音表記 is actually enabled -
    # the user explicitly rejected making this window taller, so the strip is
    # reserved on demand (see widget_height()/set_se_text_enabled) instead of
    # unconditionally.
    WIDGET_HEIGHT_NO_SE = TOP_MARGIN + LANE_HEIGHT + BOTTOM_MARGIN            # 186
    WIDGET_HEIGHT = TOP_MARGIN + LANE_HEIGHT + SE_FOOTER_HEIGHT + BOTTOM_MARGIN  # 212
    SE_FONT_SIZE_SMALL = 12
    SE_FONT_SIZE_BIG = 14
    RESYNC_THRESHOLD_SEC = 0.05

    # --- hit fly-off -----------------------------------------------------
    # 叩いた音符は判定線を越えると右上へ直線的に飛んで消える(太鼓の達人風)。
    # 以前はこの直線方式で、一度 PeepoDrumKit の放物線(GameNoteHitPath)に
    # 差し替えたが、利用者の希望で直線方式に戻した。0.25 秒、右へ HIT_FLY_DX、
    # 上へ HIT_FLY_DY、透明度と半径は progress に対して線形。
    HIT_ANIM_DURATION = 0.25
    HIT_FLY_DX = 90.0
    HIT_FLY_DY = 70.0

    # --- 叩いた瞬間の判定エフェクト (本家風) ----------------------------
    # このプレビューは全ノーツを正確な時刻に自動ヒットする静的可視化なので、
    # 判定は常に「良」。直近ヒット音符からの経過時間だけで演出を描くステートレス
    # 方式なので、シーク・部分再生・逆再生でも余計な状態を持たずに整合する。
    HIT_BURST_DURATION = 0.18   # 判定枠から広がる閃光リング + 内側フラッシュ
    JUDGE_POP_DURATION = 0.34   # 「良」の文字が上へ昇りながらフェードする時間
    COMBO_POP_DURATION = 0.16   # コンボ数字がヒットごとに拡大→等倍へ戻る時間

    # --- GOGO judgment-ring pulse (PeepoDrumKit getGogoZoomAmount port) --
    # chart_editor_widgets_game.cpp:120-134. Only the "fire" envelope is
    # ported; the lane zoom (tAttLane) is deliberately NOT - this lane's
    # proportions are fixed by design.
    GOGO_ATT = 0.05
    GOGO_DEC = 0.20
    GOGO_REL = 0.10

    PANEL_INSET = 14           # left margin so the combo/course block reads as a floating card, not edge-to-edge
    PANEL_GAP = 24             # gap between the panel's right edge and the judgment ring

    # Emitted whenever widget_height() changes (i.e. 打音表記 toggled), so the
    # fixed-size container window can re-fit itself.
    heightChanged = Signal(int)

    # Duration (seconds) of the ease-out tween that plays when the user steps
    # between measures while stopped/paused, so the lane glides to the target
    # instead of jumping there instantly (機能2). Kept short so navigation
    # still feels immediate.
    SCROLL_ANIM_SEC = 0.14

    def __init__(self, parent=None, course_select_cb=None, toggle_play_cb=None,
                 seek_cursor_cb=None, seek_seconds_cb=None, info_update_cb=None,
                 hit_sound_engine=None, branch_select_cb=None, play_cb=None, pause_cb=None,
                 cycle_bottom_mode_cb=None, set_speed_cb=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setFocusPolicy(Qt.StrongFocus)
        # paintEvent fills the ENTIRE widget rect first thing (fillRect(rect,
        # bg)), so tell Qt the widget paints all its own pixels. Without this,
        # Qt erases the background to the window color before every paint, and
        # at 60 fps that erase-then-repaint shows up as a flicker/shimmer on
        # the scrolling lane. Declaring it opaque skips the erase (no flash)
        # and is also a little cheaper.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        # Don't let the system paint a background under us on resize/expose.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._note_times = []
        self._note_chars = []
        self._note_bpms = []
        self._note_scrolls = []
        # 音符/小節線の見かけ速度(px/秒)。_rebuild_min_vis_speed で作り直す。
        self._note_speeds = []
        self._bar_speeds = []
        # 打音表記: one syllable (or None) per note, precomputed by the
        # analyzer - see neotja/se_text.py. Never derived in paintEvent.
        self._note_se = []
        self._rolls = []
        self._balloons = []
        self._kusudamas = []
        self._live_spans = []
        self._bar_times = []
        self._bar_bpms = []
        self._bar_scrolls = []
        self._bar_visible = []
        self._gogo_regions = []
        # Start-time column of _gogo_regions, so gogo_pulse() can bisect for
        # "the last region at or before now" instead of scanning every frame.
        self._gogo_starts = []
        # Precomputed draw geometry for span notes (roll/balloon/kusudama):
        # (start, end, head_speed, tail_speed[, radius]). Head and tail each
        # carry the on-screen speed implied by the BPM/SCROLL in effect at
        # THEIR OWN time, so a #SCROLL or #BPMCHANGE landing inside the span
        # stretches/compresses the bar exactly like the real game. Resolved
        # once per chart edit (set_preview_data), never in the paint loop.
        self._roll_draw = []
        self._balloon_draw = []
        self._kusudama_draw = []
        self._bpm_changes = [(0.0, DEFAULT_BPM)]
        self._measure_changes = [(0.0, 4, 4)]
        self._scroll_changes = [(0.0, 1.0)]
        # Slowest on-screen note/bar speed in the current chart, used to size
        # the visible-time window in _visible_window so even very slow charts
        # slide their notes in from the right edge (rebuilt per chart edit).
        self._min_vis_speed = self.BASE_PIXELS_PER_BEAT * self.WINDOW_REF_BPM / 60.0
        self._course_key = None
        self._course_label = ""
        self._course_color = COLORS["fg_bright"]
        self._course_level = None
        self._available_courses = []
        self._has_branches = False
        self._branch_level = "M"
        self._course_select_cb = course_select_cb
        self._branch_select_cb = branch_select_cb
        self._toggle_play_cb = toggle_play_cb
        self._seek_cursor_cb = seek_cursor_cb
        self._seek_seconds_cb = seek_seconds_cb
        self._play_cb = play_cb
        self._pause_cb = pause_cb
        self._info_update_cb = info_update_cb
        self._hit_sound_engine = hit_sound_engine
        # 各フレーム(120fps外挿クロック)で現在の音源時刻を渡すフック。作譜
        # モードの波形をレーンと同じ滑らかさ・同じ時刻で追従させるのに使う。
        self._frame_cb = None
        # 操作フィードバックのドン/カ単発再生 cb(kind)。ナビ=カ、再生=ドン。
        self._hit_feedback_cb = None
        # f/j 再生: 小節頭の少し前からフェードインして再生する cb(measure_time)。
        self._fadein_play_cb = None
        # チェックポイント(音源時刻の昇順リスト)。アンカーの概念を廃止し、
        # これで「戻る位置」を表す。p でトグル、p 押しながら小節移動で最寄りへ。
        self._checkpoints = []
        self._checkpoints_changed_cb = None
        # p の「タップ(=トグル) か 押しながら移動(=ジャンプ)」判定用。
        self._p_held = False
        self._p_moved = False
        # f/j リード再生の「表示開始時刻(譜面時刻)」。再生位置がここに達するまで
        # 譜面(音符/連打/効果/SE表記)を隠し、SE も止める。到達で reveal_cb を
        # 呼んで元に戻す。None なら通常表示。
        self._reveal_time = None
        self._reveal_cb = None
        # 等速モード(Tab): 流れる譜面を HS(スクロール速度)=1 固定で表示する。
        # #SCROLL/HS ギミックを無視して一定速でスクロールする「表示だけ」の
        # モード(譜面データ自体は変えない)。BPM による間隔は通常どおり。
        self._constant_speed = False
        # 下部パネルのモード循環(Tab / トグルボタン)と速度変更([ ] キー)を
        # ゲーム窓側へ通知するコールバック(フェーズ3)。
        self._cycle_bottom_mode_cb = cycle_bottom_mode_cb
        self._set_speed_cb = set_speed_cb
        self._offset = 0.0
        self._pos_sec = 0.0
        self._pos_wall = _time.monotonic()
        self._playing = False
        # 録画(オフライン描画)中に外から与えられる表示時刻。None なら通常どおり
        # monotonic 時計で外挿する。begin_offline_render() を参照。
        self._render_time = None
        # 再生速度倍率(作譜モード。0.25〜1.0)。1.0=等速。再生中の外挿
        # (_current_audio_time / set_playback の予測)にこの倍率を掛けて、
        # 低速再生でスクロール・打音が同じ倍率で遅くなるようにする。
        self._playback_rate = 1.0

        # --- Independent player model (機能1) --------------------------------
        # This preview drives its own playback/navigation rather than
        # following the editor cursor. `_nav_points` is the sorted list of
        # audio-time (seconds) snap targets the user can step between: the
        # song head (0小節目/曲頭) plus every bar line, each converted to
        # audio time. It's rebuilt whenever the timeline or OFFSET changes.
        self._nav_points = [0.0]
        # カレント小節 (moved by PgUp/PgDn/wheel) and アンカー (the measure a
        # Space-play started from, i.e. Q's return target), both as indices
        # into `_nav_points`.
        self._current_idx = 0
        self._anchor_idx = 0
        # The anchor is bound to a measure on the first Space that begins
        # playback; until then this stays False so navigating while stopped
        # doesn't prematurely pin it (see _toggle_play).
        self._anchor_set = False
        # "stopped" (freshly opened, parked at the head), "playing", or
        # "paused". Audio playingChanged drives the playing<->paused edges via
        # set_playback; reset_to_start sets "stopped".
        self._state = "stopped"
        # Scroll tween state (机能2). While `_animating`, _current_audio_time
        # interpolates from `_anim_start_sec` to `_anim_target_sec` with an
        # ease-out curve, and set_playback ignores audio.seek()-driven
        # position snaps so they don't cut the tween short.
        self._animating = False
        self._anim_start_sec = 0.0
        self._anim_target_sec = 0.0
        self._anim_start_wall = _time.monotonic()
        # Transient badge drawn in the lane's top-left (show_toast). Used for
        # the F1 hit-sound ON/OFF feedback, which otherwise has no visible
        # effect at all on a silent passage.
        self._toast_text = ""
        # 音源の読み込み中はレーンに幕を出す(この間は再生できない)。
        self._loading = False
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)
        # Precomputed change-time columns so per-frame lookups (_push_realtime_info)
        # bisect a ready-made list instead of rebuilding [c[0] for c in changes]
        # every tick. Rebuilt only in set_preview_data.
        self._bpm_times = [0.0]
        self._measure_times = [0.0]
        self._scroll_times = [0.0]
        # Only forward realtime info to the (relatively expensive) info-bar
        # QLabel updates when a displayed value actually changes, not on every
        # extrapolation tick - the numbers only change a few times per second.
        self._last_info = None
        # Per-frame paint allocations (QColor/QFont) are cached once and reused
        # rather than reconstructed from theme strings/font family on every
        # frame, which matters a lot at 120 fps.
        # えぬいーさん次郎(ゲーム風プレビュー)はアプリのテーマに関わらず
        # 常にダーク基調で描く。ライトテーマに切り替えても本家太鼓のような
        # 暗いレーンの見た目を保つため、live な COLORS ではなく固定の dark
        # パレットを色源にする。
        self._palette = theme.THEMES["dark"]
        self._qcolor_cache = {k: QColor(v) for k, v in self._palette.items()}
        self._font_cache = {}
        # 打音表記の表示可否(settings.json の se_text_enabled、既定 True)。
        self._se_text_enabled = True
        # Laying out kana costs more than blitting them, and this draws on
        # every visible note every frame, so each distinct (syllable, size)
        # pair is laid out once into a QStaticText and reused. Keyed by
        # (label, size) and dropped whenever the widget font family changes;
        # QStaticText holds no color, so a theme switch doesn't invalidate it
        # (the pen does that) - but _font()/_color() above are still the
        # single source of both, so nothing here reads COLORS directly.
        self._se_static_cache = {}
        # 打音表記スプライトを帯の高さへ縮小したもののキャッシュ((label,big,帯高)->QPixmap)。
        # 帯の高さが変わるときに捨てる。
        self._se_scaled_cache = {}
        # 直近に渡されたプレビューデータ(set_lane_geometry の組み直し用)。
        self._preview_data_cache = None
        # レーンの地と打音表記帯の素材(あれば自前の塗りより優先して使う)。
        self._skin_lane_main = self._load_skin_pixmap("Lane_Main.png")
        self._skin_lane_gogo = self._load_skin_pixmap("Lane_GoGo.png")
        self._skin_lane_sub = self._load_skin_pixmap("Lane_Sub.png")
        # 打音表記の文字も素材で描く(自前のフォント描きは細くて本家と違う)。
        self._skin_se = self._load_se_sprites()
        # 叩いた瞬間の火花。
        self._skin_explosion = self._load_explosion_sprites()
        # 判定円。Notes.png の左上1コマ目がそれ(音符ではない)。
        self._skin_judge_ring = self._load_judge_ring()
        # 風船が膨らんで割れるまで (Breaking_0..5.png)。
        self._skin_balloon_seq = [self._load_skin_pixmap('Breaking_%d.png' % i) for i in range(6)]
        if any(b is None for b in self._skin_balloon_seq):
            self._skin_balloon_seq = None
        # ゴーゴー中に判定円で燃える炎 (10_Effects/Fire.png 360x370 が7コマ)。
        self._skin_gogo_fire = self._load_sheet('GoGoFire.png', 7, 360, 370)
        # 切った矩形の原点を覚えておく。中身で切ると絵の重心が変わるので、
        # 置くときは「切る前のセル中心」を基準に戻す(そうしないと判定円から
        # ずれる)。大きさだけ中身の幅で決める。
        self._skin_gogo_fire, self._gogo_fire_org = self._crop_frames(self._skin_gogo_fire)
        # レーン内のコンボパネルを描かない(本家レイアウトでは左パネルへ移す)。
        self._hide_lane_combo = False
        self._se_static_family = None

        self._timer = QTimer(self)
        # PreciseTimer is required on Windows to actually tick faster than the
        # ~15.6 ms default timer granularity - without it, sub-16 ms intervals
        # get rounded back up and the preview is capped near 60 fps.
        self._timer.setTimerType(Qt.PreciseTimer)
        self._apply_timer_interval()
        self._timer.timeout.connect(self._on_tick)

        self._sprites_small, self._sprites_big = self._load_sprites()
        # Optional 良 judge sprite (skin/Judge.png). None -> drawn text fallback.
        self._skin_judge_good = self._load_skin_judge()
        # Optional balloon sprite (for 風船/くす玉). None -> procedural circle.
        self._skin_balloon = self._load_skin_balloon()
        # Optional 黄色連打 sprite. None -> procedural bar.
        self._skin_roll = self._load_skin_roll()

        # 風船/くす玉の破裂音 (skin/balloon.wav)。打数を叩ききって割れた瞬間に
        # 1回だけ鳴らす。無ければ無音(演出だけ)。
        self._pop_sound = self._load_pop_sound()
        # 破裂時刻(= 各風船/くす玉の終点、譜面時間・昇順)。再生中に now が
        # これを跨いだ瞬間に _pop_sound を鳴らす。set_preview_data で再構築。
        self._pop_times = []
        # 直近に破裂スキャンした譜面時間。再生中のみ有効、シーク/一時停止で
        # None に戻して跨ぎ判定をリセットする。
        self._last_pop_scan_t = None

        # 実測fpsの表示(左上)。paintEvent 間隔の指数移動平均から出す。実際に
        # 何フレーム描けているかの目安 - 「本当に出てる?」を可視化するため。
        try:
            self._show_fps = bool(settings_mod.load_settings().get("preview_show_fps", True))
        except Exception:
            self._show_fps = True
        self._fps_ema = 0.0
        self._fps_last_wall = None

    def _apply_timer_interval(self):
        # Match the redraw cadence to the display's refresh rate: 60 fps on a
        # 60 Hz panel, up to 120/144 fps on a high-refresh one (capped so we
        # never render faster than the monitor can show, which would be pure
        # wasted CPU). The monotonic-clock extrapolation in paintEvent keeps
        # scrolling time-accurate regardless of the exact interval.
        hz = 60.0
        try:
            scr = self.screen()
            if scr is not None:
                r = scr.refreshRate()
                if r and r > 0:
                    hz = r
        except Exception:
            pass
        # Cap the redraw rate to keep CPU use down. Default 60 fps; tunable via
        # settings "preview_max_fps" (20-144).
        cap = 60
        try:
            cap = int(settings_mod.load_settings().get("preview_max_fps", 60))
        except Exception:
            cap = 60
        cap = max(20, min(240, cap))
        # This is a plain software-rendered QWidget, so its redraw timer is NOT
        # synchronized to the display's vblank. Rendering at ~62.5 fps (the
        # 16 ms floor) against a 60 Hz panel produces a ~2.5 Hz beat: every
        # ~0.4 s a rendered frame lands on the same refresh as the previous one
        # (or misses one), so the scroll periodically slips - perceived as
        # judder/shimmer even though the fps counter reads ~60.
        #
        # Without a real vsync hook, the best software fix is to render at an
        # integer MULTIPLE of the refresh (2x): every vblank then samples a
        # frame at a stable phase and at most ~half a refresh old, so the beat
        # disappears and frame-age jitter is halved. On a 60 Hz panel that's
        # 120 fps (8 ms); on a 144 Hz panel the cap keeps it at 144. Bounded by
        # the user's preview_max_fps so it can be dialed back for CPU.
        target = min(float(cap), max(hz * 2.0, 60.0))
        hz = max(20.0, target)
        # Round to the nearest ms so the interval actually lands on the target
        # (8 ms for 120 fps) instead of being biased fast/slow.
        self._timer.setInterval(max(1, round(1000.0 / hz)))

    def _color(self, key: str) -> QColor:
        # 固定のダークパレット(self._palette)から引くだけ。テーマ切替で
        # 色が変わらないので、GENERATION による無効化はしない。
        c = self._qcolor_cache.get(key)
        if c is None:
            c = QColor(self._palette.get(key, "#ffffff"))
            self._qcolor_cache[key] = c
        return c

    def _font(self, size: int, bold: bool = False) -> QFont:
        cache_key = (size, bold)
        f = self._font_cache.get(cache_key)
        if f is None:
            f = QFont(self.font().family(), size, QFont.Bold if bold else QFont.Normal)
            self._font_cache[cache_key] = f
        return f

    def widget_height(self) -> int:
        """打音表記の帯を含めた現在の固定高さ。オフのときは帯の 26 px を
        まるごと確保しない(窓を大きくしないという要望のため)。"""
        return self.WIDGET_HEIGHT if self._se_text_enabled else self.WIDGET_HEIGHT_NO_SE

    def set_se_text_enabled(self, enabled: bool):
        """打音表記(ド/ドン/コ/カ/カッ)の表示切り替え。settings.json の
        se_text_enabled から復元され、環境設定ダイアログのチェックボックスで
        変更される。オフでも解析側のラベル計算は残る(切り替えが再解析なしで
        即反映されるように)が、描画だけを止める。

        あわせてウィジェットの固定高さも切り替える: オンなら帯の分だけ背が
        高く(212)、オフならもとの高さ(186)。収める側の窓は heightChanged を
        受けて自分の固定サイズを取り直す。"""
        enabled = bool(enabled)
        changed = (enabled != self._se_text_enabled)
        self._se_text_enabled = enabled
        h = self.widget_height()
        # setFixedHeight は min/max 両方を固定するので、まだ固定されていない
        # 段階(コンストラクタ直後、窓に入る前)でも安全に呼べる。
        if self.maximumHeight() != h or self.minimumHeight() != h:
            self.setFixedHeight(h)
        if changed:
            self.heightChanged.emit(h)
            self.update()

    # ------------------------------------------------------------------
    # 叩いた音符の飛び方(右上への直線移動)
    # ------------------------------------------------------------------
    @classmethod
    def hit_fly_offset(cls, elapsed: float):
        """判定線を通過してから `elapsed` 秒後の、判定点からのオフセット
        (dx, dy) を px で返す。dy は Qt と同じく下が正なので、上方向へは負。"""
        if elapsed <= 0.0:
            return (0.0, 0.0)
        progress = min(1.0, elapsed / cls.HIT_ANIM_DURATION)
        return (cls.HIT_FLY_DX * progress, -cls.HIT_FLY_DY * progress)

    def game_state(self):
        """本家レイアウト(game_screen.py)がHUDを描くのに要る現在値をまとめて返す。

        戻り値: (譜面時刻, コンボ数, 直近ヒット or None)
        直近ヒットは (判定線を通過してからの秒数, 音符の文字, コンボ番号)。
        レーン側が既に持っている情報をそのまま渡すだけなので、HUD 用に
        別途カウントを持たずに済み、シークしてもズレない。"""
        now = self._current_chart_time()
        combo = bisect.bisect_right(self._note_times, now)
        return now, combo, self._recent_hit(now)

    def total_notes(self) -> int:
        return len(self._note_times)

    def live_tap_count(self, now=None):
        """連打の数え上げ / 風船・くす玉の残り打数。区間外は None。
        本家レイアウトではレーンの上に余白が無く、この読み出しを画面側
        (game_screen.py)が描くので、そこから呼べるように公開する。"""
        return self._live_top_count(self._current_chart_time() if now is None else now)

    def gogo_regions(self):
        """ゴーゴー区間 [(start, end), ...]。画面側の演出用。"""
        return self._gogo_regions

    def note_time(self, index):
        """index 番目(1始まり)の音符の譜面時刻。範囲外は None。"""
        i = int(index) - 1
        if 0 <= i < len(self._note_times):
            return self._note_times[i]
        return None

    # 連打が終わったあと、扇を出しておく時間。叩き終わった打数を見せる。
    # 音源読み込み中にレーンへ出す幕。
    LOADING_TEXT = "Loading Now"
    LOADING_FONT_SIZE = 34

    ROLL_HOLD_SEC = 1.0
    # そのうち最後の何秒かけて薄くしながら消すか。
    ROLL_FADE_SEC = 0.1

    def live_tap_state(self, now=None):
        """(打数, 種別) を返す。何も出さないときは (None, None)。

        戻り値は (打数, 種別, 濃さ)。種別は "roll"(連打) か "balloon"
        (風船・くす玉)。本家は連打が金の扇、風船が吹き出しと見た目が別なので、
        画面側が描き分けられるようにする。

        連打は区間を過ぎても ROLL_HOLD_SEC のあいだ最終打数を出しておく。
        ただし次の連打・風船が始まるまで — 次が来たらそちらが優先で、
        前の打数が残って「風船の脇に連打の数が出ている」ようにはしない。
        自然に消えるときだけ最後の ROLL_FADE_SEC で薄くする(次が来て
        入れ替わるときは薄くしない — 一瞬なので、かえって目につく)。"""
        t = self._current_chart_time() if now is None else now
        for r in self._rolls:
            if r[0] <= t <= r[1]:
                return self._live_top_count(t), "roll", 1.0
        for spans in (self._balloons, self._kusudamas):
            for sp in spans:
                if sp[0] <= t < sp[1]:
                    return self._live_top_count(t), "balloon", 1.0
        held = self._held_roll(t)
        if held is not None:
            count, alpha = held
            return count, "roll", alpha
        return None, None, 0.0

    def _held_roll(self, now):
        """直前に終わった連打の (最終打数, 濃さ)。出す時間を過ぎている /
        次の区間が始まっているなら None。"""
        if not self._rolls:
            return None
        last = None
        for r in self._rolls:
            if r[1] > now:
                break
            last = r
        if last is None:
            return None
        stop = last[1] + self.ROLL_HOLD_SEC
        faded = True          # 時間切れで自然に消えるのか
        # 次に始まる区間(連打・風船・くす玉)より前でだけ出す。
        starts = [sp[0] for sp in self._live_spans]
        i = bisect.bisect_right(starts, last[1])
        if i < len(starts) and starts[i] < stop:
            stop = starts[i]
            faded = False     # 入れ替わりなので薄くしない
        if now >= stop:
            return None
        alpha = 1.0
        if faded and self.ROLL_FADE_SEC > 0:
            left = stop - now
            if left < self.ROLL_FADE_SEC:
                alpha = max(0.0, left / self.ROLL_FADE_SEC)
        return int(last[-1]), alpha


    def judge_sprite(self):
        """判定文字「良」の絵 (skin/Judge.png の上段)。無ければ None。"""
        return self._skin_judge_good

    def _recent_hit(self, now: float):
        """直近に判定線を通過した音符の (経過秒, 文字, コンボ番号) を返す。
        判定エフェクト(しぶき・「良」・コンボ演出)はこれだけから描ける。
        まだ1つも叩いていなければ None。

        f/j のリード再生中は、開始位置より前の音符は隠している。その音符で
        火花・「良」・太鼓の色が動くと、何も無いところが光って見えるので、
        隠している間はヒット自体を無かったことにする。"""
        i = bisect.bisect_right(self._note_times, now) - 1
        if i < 0:
            return None
        if self._reveal_time is not None and self._note_times[i] < self._reveal_time:
            return None
        return (now - self._note_times[i], self._note_chars[i], i + 1)

    # ------------------------------------------------------------------
    # GOGO 判定リングの脈動 (PeepoDrumKit getGogoZoomAmount 移植)
    # ------------------------------------------------------------------
    def gogo_pulse(self, now: float) -> float:
        """ゴーゴータイムの ADSR 風エンベロープを 0..1 で返す。

        PeepoDrumKit (chart_editor_widgets_game.cpp:120-134) の fireAmount は
        0→2 (アタック 0.05s)、2→1 (ディケイ 0.20s)、ゴーゴー中は 1 を保持、
        区間終了後 1→0 (リリース 0.10s、二次関数)。ここでは扱いやすいよう
        2 で割って 0..1 に正規化しているので、ピーク 1.0 / サステイン 0.5。

        原典の laneAmount(レーンの縦ズーム)は **意図的に移植していない**:
        譜面レーンの比率は固定という設計方針のため。"""
        starts = self._gogo_starts
        if not starts:
            return 0.0
        i = bisect.bisect_right(starts, now) - 1
        if i < 0:
            return 0.0
        g0, g1 = self._gogo_regions[i]
        is_gogo = now < g1
        peak = self.GOGO_ATT + self.GOGO_DEC
        ft = now - g0
        if ft > peak:
            ft = peak
        if not is_gogo:
            ft += (now - g1)
        if ft > peak + self.GOGO_REL:
            return 0.0
        if ft > peak:
            v = 1.0 - ((ft - peak) / self.GOGO_REL) ** 2
        elif ft >= self.GOGO_ATT:
            v = 2.0 - (1.0 - (1.0 - (ft - self.GOGO_ATT) / self.GOGO_DEC) ** 2)
        else:
            v = 2.0 * (ft / self.GOGO_ATT)
        v *= 0.5
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

    SE_MIN_CONTRAST = 3.0  # WCAG 2.1 minimum for large/bold text

    @staticmethod
    def _relative_luminance(c: QColor) -> float:
        def channel(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * channel(c.redF()) + 0.7152 * channel(c.greenF())
                + 0.0722 * channel(c.blueF()))

    @classmethod
    def _contrast_ratio(cls, a: QColor, b: QColor) -> float:
        la, lb = cls._relative_luminance(a), cls._relative_luminance(b)
        hi, lo = (la, lb) if la >= lb else (lb, la)
        return (hi + 0.05) / (lo + 0.05)

    def _se_color(self, key: str) -> QColor:
        """打音表記用の色。基本は音符と同じ don/ka 色だが、ライトテーマの
        `surface`(白)の上では ka (#0dcaf0) のように輝度が高い色がそのままだと
        読めない(コントラスト比 1.9)。WCAG のコントラスト比で測って足りない
        場合だけ、背景と反対方向へ段階的に寄せてから使う。テーマ生成番号が
        進むと _color() 側で _qcolor_cache ごと捨てられるので、ここも同じ
        タイミングで作り直される(計算はテーマ切替時のみ、毎フレームではない)。
        """
        cache_key = ("se", key)
        # Touch _color first: it is what drops the shared cache on a theme
        # switch, which must happen before we look for our own entry.
        base = self._color(key)
        c = self._qcolor_cache.get(cache_key)
        if c is None:
            bg = self._color("surface")
            bg_is_light = self._relative_luminance(bg) > 0.5
            c = QColor(base)
            # 10 steps of 25% is always enough to reach black/white, so this
            # terminates regardless of palette.
            for _ in range(10):
                if self._contrast_ratio(c, bg) >= self.SE_MIN_CONTRAST:
                    break
                c = c.darker(125) if bg_is_light else c.lighter(125)
            self._qcolor_cache[cache_key] = c
        return c

    def _se_static_text(self, label: str, size: int) -> QStaticText:
        family = self.font().family()
        if family != self._se_static_family:
            self._se_static_cache.clear()
            self._se_static_family = family
        key = (label, size)
        st = self._se_static_cache.get(key)
        if st is None:
            st = QStaticText(label)
            st.setTextFormat(Qt.PlainText)
            # Freeze the layout now, with the exact font it will be painted
            # with, so paintEvent never runs text shaping or font metrics.
            st.prepare(font=self._font(size, True))
            self._se_static_cache[key] = st
        return st

    def _on_tick(self):
        # Finalize a scroll tween once it runs out - snap the extrapolation
        # base exactly to the target and, if we're not actually playing, let
        # the timer go idle again (it's only kept running for the animation).
        if self._animating and (_time.monotonic() - self._anim_start_wall) >= self.SCROLL_ANIM_SEC:
            self._animating = False
            self._pos_sec = self._anim_target_sec
            self._pos_wall = _time.monotonic()
            if not self._playing:
                self._timer.stop()
        self.update()
        self._push_realtime_info()
        # Hit sounds only while genuinely playing - a stopped/paused scroll
        # tween moves the display but must stay silent.
        if self._hit_sound_engine is not None and self._playing:
            self._hit_sound_engine.check_and_play(self._current_audio_time())
        self._scan_balloon_pops()
        # リード再生: 再生位置が表示開始時刻に達したら譜面/SEを復帰させる。
        if self._reveal_time is not None and self._current_chart_time() >= self._reveal_time:
            self._clear_reveal()
        # Drive any follower (作譜モードの波形) off this same smoothly-extrapolated
        # clock so it scrolls at the lane's 120fps and stays perfectly in sync
        # (no separate clock to drift). Cheap when the follower is hidden.
        if self._frame_cb is not None:
            self._frame_cb(self._current_audio_time())

    # A seek/jump can move `now` by seconds in one tick; anything bigger than
    # normal forward playback (~one tick) is a jump, not a burst of pops, so we
    # resync silently instead of firing every crossed pop at once.
    POP_JUMP_THRESHOLD_SEC = 0.5

    def _scan_balloon_pops(self):
        """再生中に譜面時間 now が風船/くす玉の終点を跨いだら破裂音を鳴らす。
        叩ききって割れた瞬間の演出音。停止/一時停止/シーク中は鳴らさない。"""
        if not self._playing or self._pop_sound is None or not self._pop_times:
            self._last_pop_scan_t = None
            return
        now = self._current_chart_time()
        prev = self._last_pop_scan_t
        self._last_pop_scan_t = now
        if prev is None or abs(now - prev) > self.POP_JUMP_THRESHOLD_SEC:
            return
        if now > prev:
            lo = bisect.bisect_right(self._pop_times, prev)
            hi = bisect.bisect_right(self._pop_times, now)
            # リード再生中は開始位置より前の風船を隠しているので、破裂音も出さない。
            if self._reveal_time is not None:
                lo = max(lo, bisect.bisect_left(self._pop_times, self._reveal_time))
            if hi > lo:
                self._pop_sound.play()

    JUDGE_SPRITE_H = 46  # on-screen height the 良 judge sprite is scaled to

    def _load_skin_roll(self):
        """Drumroll art from skin/Notes.png: the yellow head (a round 連打
        note) and the body bar (flat left, rounded right cap), small & big.
        Returns {"small": pack, "big": pack} where pack = {head, mid, cap,
        body_h}, or None. The body is pre-split into a stretchable middle and a
        fixed rounded cap so any length keeps a clean tail."""
        path = os.path.join(str(settings_mod.skin_dir()), "Notes.png")
        if not os.path.exists(path):
            return None
        try:
            import numpy as np
            from PIL import Image
            sheet = Image.open(path).convert("RGBA")
            w, h = sheet.size
            row_h = h // 3
            y0 = row_h
            band = np.asarray(sheet)[y0:y0 + row_h, :, 3]
            col = band.max(axis=0) > 16
            spans, start = [], None
            for x in range(w):
                if col[x] and start is None:
                    start = x
                elif not col[x] and start is not None:
                    spans.append((start, x - 1)); start = None
            if start is not None:
                spans.append((start, w - 1))
            if len(spans) < 9:              # [5]head [6]body [7]big-head [8]big-body
                return None

            def cell(sp):
                c = sheet.crop((sp[0], y0, sp[1] + 1, y0 + row_h))
                ca = np.asarray(c)[:, :, 3]
                ys, xs = np.where(ca > 16)
                if len(xs) == 0:
                    return None
                return c.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))

            def pack(head, body, r):
                if head is None or body is None:
                    return None
                bw, bh = body.width, body.height
                cap_w = min(bh, bw)
                mid = body.crop((0, 0, max(1, bw - cap_w), bh))
                cap = body.crop((max(0, bw - cap_w), 0, bw, bh))
                head_pix = _pil_to_qpixmap(head)
                # Pre-scale the head to the exact on-screen size (a normal note
                # diameter) so the paint loop just blits it - the per-frame
                # scaledToHeight it used to do was a frame-drop source whenever
                # a roll was on screen.
                head_scaled = head_pix.scaledToHeight(int(r * 2), Qt.SmoothTransformation)
                return {"head": head_scaled, "mid": _pil_to_qpixmap(mid),
                        "cap": _pil_to_qpixmap(cap), "body_h": bh}

            return {"small": pack(cell(spans[5]), cell(spans[6]), self.NOTE_R_SMALL),
                    "big": pack(cell(spans[7]), cell(spans[8]), self.NOTE_R_BIG)}
        except Exception:
            return None

    def _draw_roll_sprite(self, painter, x0, x1, cy, r, big) -> bool:
        """本家風の黄色連打を skin 素材で描く: 伸縮する胴 + 丸い尾 + 頭。
        スキン無し/逆スクロール時は False を返し、呼び出し側が従来のバーへ。"""
        pack = self._skin_roll.get("big" if big else "small") if self._skin_roll else None
        if pack is None or x1 < x0:
            return False
        d = float(r * 2)
        scale = d / pack["body_h"]
        mid, cap, head = pack["mid"], pack["cap"], pack["head"]
        cap_dst = cap.width() * scale
        total = max(cap_dst, x1 - x0)
        mid_dst = max(0.0, total - cap_dst)
        painter.drawPixmap(QRectF(x0, cy - r, mid_dst, d), mid,
                           QRectF(0, 0, mid.width(), mid.height()))
        painter.drawPixmap(QRectF(x0 + mid_dst, cy - r, cap_dst, d), cap,
                           QRectF(0, 0, cap.width(), cap.height()))
        painter.drawPixmap(QPointF(x0 - r, cy - r), head)  # pre-scaled, sub-pixel
        return True

    def _load_skin_balloon(self):
        """The balloon sprite (last cell of skin/Notes.png - the orange
        round-face balloon), tight-cropped, plus the height fraction its round
        face takes up (so the face can be sized to a note). Returns
        {pix, face_frac} or None. Used for both 風船 and くす玉."""
        path = os.path.join(str(settings_mod.skin_dir()), "Notes.png")
        if not os.path.exists(path):
            return None
        try:
            import numpy as np
            from PIL import Image
            sheet = Image.open(path).convert("RGBA")
            w, h = sheet.size
            row_h = h // 3
            y0 = row_h                       # middle animation frame
            band = np.asarray(sheet)[y0:y0 + row_h, :, 3]
            col = band.max(axis=0) > 16
            spans, start = [], None
            for x in range(w):
                if col[x] and start is None:
                    start = x
                elif not col[x] and start is not None:
                    spans.append((start, x - 1)); start = None
            if start is not None:
                spans.append((start, w - 1))
            if len(spans) < 10:
                return None
            sp = spans[9]
            cell = sheet.crop((sp[0], y0, sp[1] + 1, y0 + row_h))
            ca = np.asarray(cell)[:, :, 3]
            ys, xs = np.where(ca > 16)
            if len(xs) == 0:
                return None
            cell = cell.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
            ca2 = np.asarray(cell)[:, :, 3]
            col_h = (ca2 > 16).sum(axis=0)
            left = col_h[:max(1, int(cell.width * 0.4))]
            face_frac = float(left.max()) / cell.height if cell.height else 1.0
            face_frac = max(0.3, face_frac)
            pix = _pil_to_qpixmap(cell)
            # Pre-scale to the exact on-screen size a small note uses, so the
            # paint loop just blits it (per-frame scaledToHeight was a real
            # frame-drop source). face_r/off let the caller center the round
            # face on the judgment point without re-deriving them each frame.
            sprite_h = (2.0 * self.NOTE_R_SMALL) / face_frac
            scaled = pix.scaledToHeight(max(1, int(sprite_h)), Qt.SmoothTransformation)
            face_r = sprite_h * face_frac / 2.0
            return {"pix": pix, "face_frac": face_frac,
                    "scaled": scaled, "face_r": face_r}
        except Exception:
            return None

    def _load_pop_sound(self):
        """風船/くす玉の破裂音を skin/balloon.wav から読み込む。無ければ None
        (音は鳴らさず演出だけ)。QSoundEffect は低遅延で短い WAV に向く。"""
        path = os.path.join(str(settings_mod.skin_dir()), "balloon.wav")
        if not os.path.exists(path):
            return None
        try:
            snd = QSoundEffect(self)
            snd.setSource(QUrl.fromLocalFile(path))
            snd.setVolume(0.9)
            return snd
        except Exception:
            return None

    def _load_skin_judge(self):
        """Top cell (良) of an OpenTaiko-style skin/Judge.png, scaled for the
        judge pop, or None. This preview auto-hits every note so the judgment
        is always 良 - only that first row is needed."""
        path = os.path.join(str(settings_mod.skin_dir()), "Judge.png")
        if not os.path.exists(path):
            return None
        try:
            import numpy as np
            from PIL import Image
            img = Image.open(path).convert("RGBA")
            a = np.asarray(img)[:, :, 3]
            row_opaque = a.max(axis=1) > 16
            y0 = y1 = None
            start = None
            for y in range(a.shape[0]):
                if row_opaque[y] and start is None:
                    start = y
                elif not row_opaque[y] and start is not None:
                    y0, y1 = start, y - 1
                    break
            if y0 is None:
                if start is None:
                    return None
                y0, y1 = start, a.shape[0] - 1
            band = img.crop((0, y0, img.width, y1 + 1))
            ba = np.asarray(band)[:, :, 3]
            cols = np.where(ba.max(axis=0) > 16)[0]
            if len(cols) == 0:
                return None
            band = band.crop((int(cols.min()), 0, int(cols.max()) + 1, band.height))
            scale = self.JUDGE_SPRITE_H / band.height
            band = band.resize((max(1, round(band.width * scale)), self.JUDGE_SPRITE_H),
                               Image.Resampling.LANCZOS)
            return _pil_to_qpixmap(band)
        except Exception:
            return None

    def _load_sprites(self):
        """Note art for the preview, in priority order per note:
        1. an OpenTaiko-style skin (skin/Notes.png next to the exe), the
           optional 本家 look the author hands out separately;
        2. a user-configured notes.png sheet (same one image-export uses);
        3. a procedurally drawn glossy sprite (see _make_note_sprite), which
           needs no asset and ships in the app.
        Each tier only fills notes the previous one didn't, so the preview
        always has a full set - and looks decent with nothing installed."""
        small, big = {}, {}
        # tier 1: OpenTaiko-style skin the user dropped in
        skin_small, skin_big = self._load_skin_sprites()
        small.update(skin_small)
        big.update(skin_big)
        # tier 2: legacy notes.png sheet (image-export format)
        pil_sprites = {}
        try:
            from PIL import Image
            from neotja.tja_image_export import load_sprites
            pil_sprites = load_sprites(settings_mod.notes_png_path())
        except Exception:
            pil_sprites = {}
        for c in ("1", "2"):
            if c not in small and c in pil_sprites:
                d = self.NOTE_R_SMALL * 2
                small[c] = _pil_to_qpixmap(pil_sprites[c].resize((d, d), Image.Resampling.LANCZOS))
        for c in ("3", "4"):
            if c not in big and c in pil_sprites:
                d = self.NOTE_R_BIG * 2
                big[c] = _pil_to_qpixmap(pil_sprites[c].resize((d, d), Image.Resampling.LANCZOS))
        # tier 3: procedural fallback
        for c in ("1", "2"):
            small.setdefault(c, self._make_note_sprite(NOTE_COLOR[c], self.NOTE_R_SMALL))
        for c in ("3", "4"):
            big.setdefault(c, self._make_note_sprite(NOTE_COLOR[c], self.NOTE_R_BIG))
        return small, big

    def _load_skin_sprites(self):
        """Slice don/ka (small & big) out of an OpenTaiko-style Notes.png skin,
        if the user dropped one into the `skin` folder. The sheet is 3 stacked
        animation frames; the opaque sprite columns are detected by alpha (so
        it adapts to different skin resolutions) and the middle frame is used
        as the still pose. Returns ({},{}) when there's no skin or on any error
        - the caller then falls back to the built-in art."""
        small, big = {}, {}
        path = settings_mod.skin_notes_path()
        if not path or not os.path.exists(str(path)):
            return small, big
        try:
            import numpy as np
            from PIL import Image

            sheet = Image.open(str(path)).convert("RGBA")
            w, h = sheet.size
            row_h = h // 3
            y0 = row_h                      # middle animation frame
            band = np.asarray(sheet)[y0:y0 + row_h, :, 3]
            col_opaque = band.max(axis=0) > 16

            # group consecutive opaque columns into sprite spans
            spans, start = [], None
            for x in range(w):
                if col_opaque[x] and start is None:
                    start = x
                elif not col_opaque[x] and start is not None:
                    spans.append((start, x - 1))
                    start = None
            if start is not None:
                spans.append((start, w - 1))

            # Standard layout: [0]=hit target ring, [1]=don, [2]=ka,
            # [3]=don-big, [4]=ka-big, then rolls/balloons we don't need here.
            if len(spans) < 5:
                return {}, {}
            picks = {"1": (spans[1], self.NOTE_R_SMALL, small),
                     "2": (spans[2], self.NOTE_R_SMALL, small),
                     "3": (spans[3], self.NOTE_R_BIG, big),
                     "4": (spans[4], self.NOTE_R_BIG, big)}
            for c, ((sx0, sx1), r, target) in picks.items():
                cell = sheet.crop((sx0, y0, sx1 + 1, y0 + row_h))
                # tighten to the sprite's own alpha box, then center on a square
                ca = np.asarray(cell)[:, :, 3]
                ys, xs = np.where(ca > 16)
                if len(xs) == 0:
                    continue
                cell = cell.crop((int(xs.min()), int(ys.min()),
                                  int(xs.max()) + 1, int(ys.max()) + 1))
                side = max(cell.width, cell.height)
                sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2))
                d = r * 2
                target[c] = _pil_to_qpixmap(sq.resize((d, d), Image.Resampling.LANCZOS))
        except Exception:
            return {}, {}
        return small, big

    @staticmethod
    def _blend(a: QColor, b: QColor, t: float) -> QColor:
        """Linear mix of two colors; t=0 -> a, t=1 -> b."""
        return QColor(
            round(a.red() * (1 - t) + b.red() * t),
            round(a.green() * (1 - t) + b.green() * t),
            round(a.blue() * (1 - t) + b.blue() * t),
        )

    def _make_note_sprite(self, color_key: str, r: int) -> QPixmap:
        """Render a glossy, 本家-style note once and cache it as a pixmap: a
        colored sphere shaded top-left-to-bottom-right (highlight -> body ->
        dark rim), wrapped in the game's cream ring and a thin dark outline,
        with a soft specular gloss. Rendered at 2x and smooth-scaled down so
        the edges stay crisp. Blitting this each frame is both prettier and
        cheaper than re-running a gradient fill per note."""
        ss = 2  # supersample factor
        d = r * 2 * ss
        img = QImage(d, d, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        cx = cy = d / 2.0
        big_r = r * ss

        base = self._color(color_key)
        highlight = self._blend(base, QColor(255, 255, 255), 0.55)
        rim = base.darker(170)
        ring = QColor("#fbf3e0")       # 本家のクリーム色のフチ
        outline = QColor(54, 32, 30)

        # thin dark outline, then the cream ring inside it
        p.setBrush(outline)
        p.drawEllipse(QRectF(cx - big_r, cy - big_r, 2 * big_r, 2 * big_r))
        p.setBrush(ring)
        p.drawEllipse(QRectF(cx - big_r + ss, cy - big_r + ss,
                             2 * (big_r - ss), 2 * (big_r - ss)))

        # colored body: radial gradient lit from the upper-left
        inner_r = big_r - max(2.0 * ss, big_r * 0.16)
        fx, fy = cx - inner_r * 0.33, cy - inner_r * 0.33
        grad = QRadialGradient(fx, fy, inner_r * 1.5, fx, fy)
        grad.setColorAt(0.0, highlight)
        grad.setColorAt(0.55, base)
        grad.setColorAt(1.0, rim)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r, 2 * inner_r, 2 * inner_r))

        # soft specular gloss near the top
        p.setBrush(QColor(255, 255, 255, 95))
        gw, gh = inner_r * 0.80, inner_r * 0.48
        p.drawEllipse(QRectF(cx - gw / 2, cy - inner_r * 0.60, gw, gh))
        p.end()

        return QPixmap.fromImage(
            img.scaled(r * 2, r * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def set_preview_data(self, data: dict):
        """`data` is the dict returned by TJACourseAnalyzer.build_preview_timeline:
        notes/rolls/balloons/kusudamas/gogo_regions/bar_times/bpm_changes/
        measure_changes/scroll_changes/course_key/course_label/course_color/
        level/available_courses."""
        # 寸法を差し替えた(set_lane_geometry)ときに組み直せるよう控えておく。
        self._preview_data_cache = data
        notes = sorted(data.get("notes") or [], key=lambda n: n[0])
        self._note_times = [n[0] for n in notes]
        self._note_chars = [n[1] for n in notes]
        self._note_bpms = [n[2] for n in notes]
        self._note_scrolls = [n[3] for n in notes]
        # 5th element is the precomputed 打音表記 syllable (see se_text.py).
        # Tolerated as absent so an older/hand-built dict still loads.
        self._note_se = [(n[4] if len(n) > 4 else None) for n in notes]
        self._rolls = sorted(data.get("rolls") or [], key=lambda r: r[0])
        self._balloons = sorted(data.get("balloons") or [], key=lambda b: b[0])
        # Kusudama ('9'...'8') is a balloon-shaped span (same 5-tuple shape:
        # start, end, bpm, scroll, hits) but drawn in its own color and kept
        # in its own list rather than tagged onto _balloons, so callers that
        # want "just balloons" (info-bar balloon count, etc.) don't need to
        # filter it back out.
        self._kusudamas = sorted(data.get("kusudamas") or [], key=lambda k: k[0])
        # 風船・くす玉が割れる秒速(環境設定の連打秒速)。区間の終わりまで
        # 引き延ばすのではなく、この速さで叩いて必要打数に達した時点で割れる。
        self._roll_hit_speed = max(1.0, float(data.get("roll_hit_speed", 45) or 45))
        from neotja.tja_analyzer import balloon_pop_spans
        self._balloons = balloon_pop_spans(self._balloons, self._roll_hit_speed)
        self._kusudamas = balloon_pop_spans(self._kusudamas, self._roll_hit_speed)
        # (start, end, hits) view combining all three span types, used only
        # for the live/held combo-count readout - independent of the rolls/
        # balloons/kusudamas lists above since those keep their full
        # per-type tuples for rendering.
        self._live_spans = sorted(
            [(r[0], r[1], r[-1]) for r in self._rolls]
            + [(b[0], b[1], b[-1]) for b in self._balloons]
            + [(k[0], k[1], k[-1]) for k in self._kusudamas],
            key=lambda s: s[0],
        )
        self._gogo_regions = sorted(data.get("gogo_regions") or [])
        self._gogo_starts = [g[0] for g in self._gogo_regions]
        bars = sorted(data.get("bar_times") or [])
        self._bar_times = [t for t, _, _, _ in bars]
        self._bar_bpms = [bpm for _, bpm, _, _ in bars]
        self._bar_scrolls = [sc for _, _, sc, _ in bars]
        self._bar_visible = [vis for _, _, _, vis in bars]
        self._bpm_changes = sorted(data.get("bpm_changes") or [(0.0, DEFAULT_BPM)])
        self._measure_changes = sorted(data.get("measure_changes") or [(0.0, 4, 4)])
        self._scroll_changes = sorted(data.get("scroll_changes") or [(0.0, 1.0)])
        self._bpm_times = [c[0] for c in self._bpm_changes]
        self._measure_times = [c[0] for c in self._measure_changes]
        self._scroll_times = [c[0] for c in self._scroll_changes]
        self._rebuild_span_draw_data()
        self._rebuild_min_vis_speed()
        # 風船の破裂音の走査位置は前の譜面のもの。持ち越すと、差し替え直後に
        # 「前回見た時刻〜今」の区間を跨いだ扱いになって、割ってもいない風船の
        # 破裂音が1回鳴ることがある。次のフレームで無音のまま取り直す。
        self._last_pop_scan_t = None
        prev_course = self._course_key
        prev_branch = self._branch_level
        self._course_key = data.get("course_key")
        self._course_label = data.get("course_label") or ""
        self._course_color = data.get("course_color") or COLORS["fg_bright"]
        self._course_level = data.get("level")
        self._available_courses = data.get("available_courses") or []
        self._has_branches = bool(data.get("has_branches"))
        self._branch_level = data.get("branch_level") or "M"
        # コース/分岐が切り替わったら、リード再生(f/j)の「ここまで隠す」は
        # 前の譜面の時刻なので畳む。畳まないと別の譜面の音符が消えたままになり、
        # 打音も抑制されたままになる。編集による差し替えでは畳まない
        # (打っている最中のリードを毎回取り消してしまうため)。
        if (prev_course, prev_branch) != (self._course_key, self._branch_level):
            self._clear_reveal()
        # New nav targets after the timeline changed. This deliberately keeps
        # the current audio position (_pos_sec) untouched and only re-derives
        # which measure is "current" from it, so an edit/refresh never yanks
        # playback to the end or the cursor (机能3).
        self._rebuild_nav_points()
        # Force the next _push_realtime_info to fire even if the current
        # values happen to match the previous chart's - the info bar must
        # refresh after a chart/course/branch change.
        self._last_info = None
        self.update()
        self._push_realtime_info()

    def mousePressEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    def cycle_course(self):
        # Each call steps down one rank (Ura -> Oni -> Hard -> Normal ->
        # Easy), wrapping back to the top after the last one - called from
        # the info bar's course button.
        if not self._available_courses:
            return
        keys = [c["key"] for c in self._available_courses]
        try:
            idx = keys.index(self._course_key)
        except ValueError:
            idx = -1
        self._course_select_cb(keys[(idx + 1) % len(keys)])

    def cycle_branch(self):
        # Steps 普通(N) -> 玄人(E) -> 達人(M), wrapping - called from the
        # info bar's branch button. This preview is a static, non-judged
        # visualization (every note is assumed hit), so it can't meaningfully
        # simulate the real game's accuracy-based branch switching; instead
        # the user picks one branch level to view for the whole course.
        if not self._has_branches or not self._branch_select_cb:
            return
        order = ["N", "E", "M"]
        idx = order.index(self._branch_level) if self._branch_level in order else 2
        self._branch_select_cb(order[(idx + 1) % len(order)])

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def event(self, e):
        # Tab は通常 keyPressEvent に届く前に QWidget::event 内のフォーカス移動
        # に消費されてしまうので、event() レベルで横取りする。操作刷新で Tab は
        # 「等速モード(HS1固定表示)」のトグルに割り当てた(下部パネルのモード
        # 切替は画面上のモードボタンで行う)。
        if e.type() == QEvent.KeyPress and e.key() == Qt.Key_Tab:
            self.toggle_constant_speed()
            e.accept()
            return True
        return super().event(e)

    # --- 操作キー割り当て(一新) --------------------------------------
    # 再生:            f / j (小節頭0.5s前からフェードイン) / Space(その場から即)
    # 一時停止:        Enter / Space
    # 前の小節:        d / s / PageDown / →   (カの音)
    # 次の小節:        k / l / PageUp / ←     (カの音)
    # 曲頭/最終小節:   Home / End
    # 再生速度 -/+:    z / ↓  と  c / ↑
    # チェックポイント: p(タップでトグル / 押しながら小節移動で最寄りへジャンプ)
    # ※アンカー(Q)は廃止。チェックポイントで代替。
    # ←/→ は画面の向きどおり(← が戻る、→ が進む)。d/s・k/l は太鼓の
    # 左打面/右打面の並びに合わせてあるので、そちらは入れ替えない。
    _KEY_PREV = frozenset((Qt.Key_D, Qt.Key_S, Qt.Key_PageDown, Qt.Key_Left))
    _KEY_NEXT = frozenset((Qt.Key_K, Qt.Key_L, Qt.Key_PageUp, Qt.Key_Right))

    def keyPressEvent(self, event):
        key = event.key()
        # 再生/一時停止(f/j): 再生中なら一時停止、そうでなければ小節頭の少し前
        # からリード再生(リード中は開始位置より前の譜面/SEを隠す)。ドンの音。
        # フィードバックは動作(シーク)の後に鳴らす: ミキサーはシーク時に再生中の
        # ボイスを消すため、先に鳴らすと直後のシークで消えてしまう。
        if key in (Qt.Key_F, Qt.Key_J):
            if self._state == "playing":
                self.pause()
            else:
                self.play_from_current_measure(fade=True)
            self._feedback("don")
            return
        # 再生/一時停止(Space): その場から即開始 or 一時停止。ドンの音。
        if key == Qt.Key_Space:
            self.toggle_play()
            self._feedback("don")
            return
        # 一時停止(Enter)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.pause()
            return
        # チェックポイント(p): 押した瞬間は保持フラグだけ立て、離したとき
        # (移動していなければ)トグルする。押しながら小節移動はジャンプ。
        if key == Qt.Key_P:
            if not event.isAutoRepeat():
                self._p_held = True
                self._p_moved = False
            return
        # 小節移動(カの音)。p 押しながらなら最寄りチェックポイントへジャンプ。
        if key in self._KEY_PREV:
            self._measure_key(-1)
            return
        if key in self._KEY_NEXT:
            self._measure_key(1)
            return
        if key == Qt.Key_Home:
            self.seek_to_first_measure()
            return
        if key == Qt.Key_End:
            self.seek_to_last_measure()
            return
        # 再生速度(z/↓ で遅く、c/↑ で速く)。
        if key in (Qt.Key_Z, Qt.Key_Down):
            self._adjust_speed(-0.05)
            return
        if key in (Qt.Key_C, Qt.Key_Up):
            self._adjust_speed(0.05)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_P and not event.isAutoRepeat():
            if self._p_held and not self._p_moved:
                self.toggle_checkpoint()
            self._p_held = False
            return
        super().keyReleaseEvent(event)

    def _feedback(self, kind: str):
        if self._hit_feedback_cb is not None:
            self._hit_feedback_cb(kind)

    def _measure_key(self, direction: int):
        """小節移動キー共通処理。p 押しながらなら最寄りのチェックポイントへ
        ジャンプ、そうでなければ隣の小節へ。カの音は動作(シーク)の後に鳴らす:
        ミキサーはシーク時に再生中ボイスを消すので、先に鳴らすと消される。"""
        if self._p_held:
            self._p_moved = True
            self.jump_to_checkpoint(direction)
        else:
            self.seek_relative_measure(direction)
        self._feedback("ka")

    def _apply_speed(self, rate: float) -> float:
        """目標倍率(0.25〜2.0にクランプ・小数2桁に丸め)を適用して、実際に
        適用された値を返す。スライダー配線済みなら set_speed_cb 経由(→
        スライダー値変更→valueChanged で audio/chart_preview 双方に同期反映)、
        未配線(単体使用)なら自分の _playback_rate を直接更新するフォール
        バック。"""
        rate = round(max(0.25, min(2.0, rate)), 2)
        if self._set_speed_cb:
            self._set_speed_cb(rate)
        else:
            self.set_playback_rate(rate)
        return rate

    def _adjust_speed(self, delta: float, toast: bool = False):
        rate = self._apply_speed(self._playback_rate + delta)
        if toast:
            self.show_toast(f"再生速度 : ×{rate:.2f}")

    def set_playback_rate(self, rate: float):
        """再生速度倍率(0.25〜2.0)を設定。再生中の時間外挿に使う。"""
        self._playback_rate = max(0.25, min(2.0, rate))

    def set_loading(self, loading: bool):
        """音源の読み込み中(=再生できない)かどうか。レーンに幕を出す。"""
        loading = bool(loading)
        if loading == self._loading:
            return
        self._loading = loading
        self.update()
        if self.parent() is not None:
            self.parent().update()

    def show_toast(self, text: str, seconds: float = 3.0):
        """レーン左上に text を seconds 秒だけ表示する。"""
        self._toast_text = text
        self._toast_timer.start(int(seconds * 1000))
        self.update()

    def _clear_toast(self):
        self._toast_text = ""
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.seek_relative_measure(1)
        elif delta < 0:
            self.seek_relative_measure(-1)
        event.accept()

    # ------------------------------------------------------------------
    # Navigation-point helpers (机能1)
    # ------------------------------------------------------------------
    def _rebuild_nav_points(self):
        """Rebuilds `_nav_points` from the current bar list + OFFSET and
        re-derives `_current_idx`/`_anchor_idx` against the live position so
        the preview stays put across edits/OFFSET changes.

        OFFSET convention: audio_time = chart_time - OFFSET. Bars before the
        audio start (negative after subtracting OFFSET) clamp to 0, then the
        list is sorted and near-duplicates are collapsed so stepping never
        lands on two points at effectively the same time."""
        pts = [0.0]
        for bt in self._bar_times:
            pts.append(max(0.0, bt - self._offset))
        pts.sort()
        normalized = []
        for p in pts:
            if not normalized or p - normalized[-1] > 1e-4:
                normalized.append(p)
        self._nav_points = normalized
        # Re-derive current from the live audio position rather than resetting
        # it - this is what keeps an edit/OFFSET tweak from jumping playback.
        self._current_idx = self._nearest_nav_idx(self._pos_sec)
        self._anchor_idx = max(0, min(self._anchor_idx, len(self._nav_points) - 1))

    def _nearest_nav_idx(self, t: float) -> int:
        pts = self._nav_points
        if not pts:
            return 0
        i = bisect.bisect_left(pts, t)
        if i <= 0:
            return 0
        if i >= len(pts):
            return len(pts) - 1
        return i if (pts[i] - t) < (t - pts[i - 1]) else i - 1

    def _nav_idx_at_or_before(self, t: float) -> int:
        pts = self._nav_points
        if not pts:
            return 0
        # +epsilon so a position sitting exactly on a nav point counts as
        # "at" it rather than snapping back to the previous measure.
        return max(0, min(bisect.bisect_right(pts, t + 1e-4) - 1, len(pts) - 1))

    def _start_scroll_anim(self, target_sec: float):
        # Tween from wherever the display currently reads (which itself may be
        # mid-tween if the user is stepping quickly) to the new target, and
        # keep the timer running so _on_tick advances/finalizes it even while
        # stopped/paused.
        self._anim_start_sec = self._current_audio_time()
        self._anim_target_sec = target_sec
        self._anim_start_wall = _time.monotonic()
        self._animating = True
        self._apply_timer_interval()
        self._timer.start()

    # ------------------------------------------------------------------
    # Transport (机能1)
    # ------------------------------------------------------------------
    def reset_to_start(self):
        """Called when the game-preview window is (re)opened: park カレントを
        曲頭(0小節目)へ、音源を 0 に巻き戻して停止状態にする。チェックポイントは
        譜面ごとの目印なので消さずに保持する。"""
        self._current_idx = 0
        self._state = "stopped"
        self._playing = False
        self._animating = False
        self._pos_sec = 0.0
        self._pos_wall = _time.monotonic()
        self._timer.stop()
        if self._pause_cb:
            self._pause_cb()
        if self._seek_seconds_cb:
            self._seek_seconds_cb(0.0)
        self.update()
        self._push_realtime_info()

    def toggle_play(self):
        """Space: 停止/一時停止中は「その場(現在位置)から」即再生(フェード
        なし)、再生中は一時停止。小節頭にスナップしないので、一時停止した位置
        からそのまま続けられる。"""
        if not self._nav_points:
            return
        if self._state == "playing":
            self.pause()
            return
        self._clear_reveal()   # 保留中のリード表示があれば解除して即その場から
        target = self._pos_sec
        self._animating = False
        self._pos_wall = _time.monotonic()
        if self._seek_seconds_cb:
            self._seek_seconds_cb(target)
        if self._play_cb:
            self._play_cb()

    def pause(self):
        """Enter / 一時停止。再生中なら止める(playingChanged → set_playback で
        state が paused に落ちる)。リード表示中なら解除する。"""
        self._clear_reveal()
        if self._pause_cb:
            self._pause_cb()

    def play_from_current_measure(self, fade: bool = True):
        """f/j: 今いる小節の頭から再生する。fade=True かつ fadein_play_cb が
        あれば小節頭の少し前からフェードインして再生(音源とSEを徐々に上げる)。
        未配線なら通常の即時再生にフォールバック。"""
        if not self._nav_points:
            return
        idx = self._nav_idx_at_or_before(self._current_audio_time())
        self._current_idx = idx
        target = self._nav_points[idx]
        self._animating = False
        if fade and self._fadein_play_cb is not None:
            # リード中(開始位置に達するまで)は譜面/SEを隠す。reveal_time は
            # 譜面時刻(= 音源時刻 target + OFFSET)。実際のシーク/再生/SE停止は
            # preview_dock 側。位置は positionChanged で追従する。
            self._reveal_time = target + self._offset
            self._fadein_play_cb(target)
        else:
            self._clear_reveal()
            self._pos_sec = target
            self._pos_wall = _time.monotonic()
            if self._seek_seconds_cb:
                self._seek_seconds_cb(target)
            if self._play_cb:
                self._play_cb()

    def set_reveal_cb(self, cb):
        """リード再生の「表示開始」到達時に呼ぶコールバック(SEを元に戻す等)。"""
        self._reveal_cb = cb

    def _clear_reveal(self):
        """リード表示の抑制を解除する(到達 or 別操作でキャンセル)。armされて
        いれば reveal_cb を1回呼ぶ。"""
        if self._reveal_time is not None:
            self._reveal_time = None
            if self._reveal_cb is not None:
                self._reveal_cb()

    CHECKPOINT_SNAP = 0.02  # チェックポイント同一視/探索のしきい値(秒)

    def toggle_checkpoint(self):
        """p タップ: 現在位置の小節頭にチェックポイントを作成、既にあれば削除。"""
        if not self._nav_points:
            return
        idx = self._nav_idx_at_or_before(self._current_audio_time())
        t = self._nav_points[idx]
        for i, c in enumerate(self._checkpoints):
            if abs(c - t) < self.CHECKPOINT_SNAP:
                self._checkpoints.pop(i)
                self.show_toast("チェックポイント削除", 1.0)
                self._on_checkpoints_changed()
                return
        self._checkpoints.append(t)
        self._checkpoints.sort()
        self.show_toast("チェックポイント追加", 1.0)
        self._on_checkpoints_changed()

    def _on_checkpoints_changed(self):
        if self._checkpoints_changed_cb:
            self._checkpoints_changed_cb(list(self._checkpoints))
        self.update()
        self._push_realtime_info()

    def jump_to_checkpoint(self, direction: int):
        """p 押しながら小節移動: 現在位置から direction 方向で最寄りの
        チェックポイントへ一気に移動する。無ければ何もしない。"""
        if not self._checkpoints:
            return
        pos = self._current_audio_time()
        if direction > 0:
            cands = [c for c in self._checkpoints if c > pos + self.CHECKPOINT_SNAP]
            target = cands[0] if cands else None
        else:
            cands = [c for c in self._checkpoints if c < pos - self.CHECKPOINT_SNAP]
            target = cands[-1] if cands else None
        if target is None:
            return
        self._current_idx = self._nearest_nav_idx(target)
        self._animating = False
        if self._state == "playing":
            self._pos_sec = target
            self._pos_wall = _time.monotonic()
            if self._seek_seconds_cb:
                self._seek_seconds_cb(target)
        else:
            self._start_scroll_anim(target)
            if self._seek_seconds_cb:
                self._seek_seconds_cb(target)

    def return_to_anchor(self):
        """Q: seek back to the アンカー measure and pause there (stopping
        playback first if it's running). Right after opening this is the song
        head, since the anchor is index 0."""
        if not self._nav_points:
            return
        self._anchor_idx = max(0, min(self._anchor_idx, len(self._nav_points) - 1))
        self._current_idx = self._anchor_idx
        target = self._nav_points[self._anchor_idx]
        self._animating = False
        self._pos_sec = target
        self._pos_wall = _time.monotonic()
        self._state = "paused"
        if self._pause_cb:
            self._pause_cb()
        if self._seek_seconds_cb:
            self._seek_seconds_cb(target)
        self.update()
        self._push_realtime_info()

    def seek_relative_measure(self, direction: int):
        """小節を1つ前後へ。基準は常に「いま譜面が見えている位置」。

        以前は再生中以外を _current_idx(最後に小節移動/ジャンプした位置)基準に
        していた。_current_idx は再生中に更新されないので、5小節目で止めて→
        再開→最後まで流して止める、と _current_idx は 5 のままになり、そこから
        小節移動が始まってしまっていた。トゥイーン中(連打で送っている最中)だけは
        表示位置が目標へ向かう途中なので、そのときだけ _current_idx を使う。"""
        if self._animating:
            base = self._current_idx
        else:
            base = self._nav_idx_at_or_before(self._current_audio_time())
        self._seek_to_nav_idx(base + direction)

    def _pause_for_jump(self):
        """曲頭/最終小節へ飛ぶ前に止める。

        端へ飛ぶのは「そこを見たい」ときなので、鳴らしたまま飛ぶと結局すぐ
        止めることになる。pause() の状態反映は playingChanged 経由で1拍遅れる
        ため、自分の状態も同時に落とす — そうしないと直後の移動が「再生中の
        移動」と判定され、トゥイーンせずに飛んでしまう。"""
        self.pause()
        self._state = "paused"
        self._playing = False

    def seek_to_first_measure(self):
        """Home: 一時停止して カレントを 0小節目(曲頭) へ。"""
        self._pause_for_jump()
        self._seek_to_nav_idx(0)

    def seek_to_last_measure(self):
        """End: 一時停止して カレントを最終小節へ。"""
        self._pause_for_jump()
        self._seek_to_nav_idx(len(self._nav_points) - 1)

    def _seek_to_nav_idx(self, idx: int):
        if not self._nav_points or not self._seek_seconds_cb:
            return
        self._clear_reveal()   # 手動移動したらリード表示は解除
        new_idx = max(0, min(idx, len(self._nav_points) - 1))
        self._current_idx = new_idx
        target = self._nav_points[new_idx]
        if self._state == "playing":
            # 再生中はトゥイーンせず、その位置へシークして再生継続。
            self._pos_sec = target
            self._pos_wall = _time.monotonic()
            self._animating = False
            self._seek_seconds_cb(target)
        else:
            self._start_scroll_anim(target)
            self._seek_seconds_cb(target)

    @staticmethod
    def _load_skin_pixmap(name):
        """skin/<name> を読む。無ければ None(呼び出し側が自前描画へ落とす)。"""
        path = os.path.join(str(settings_mod.skin_dir()), name)
        if not os.path.exists(path):
            return None
        pm = QPixmap(path)
        return None if pm.isNull() else pm

    # SENotes.png は 12 段。上から ドン / ド / コ / カッ / カ / ドン(大) /
    # カッ(大) / 連打 / ー / ーっ‼ / 連打(大) / ふうせん。
    SE_SPRITE_ROWS = 12
    # 帯の高さに対する打音文字の倍率。段には字の上下に余白があるので、
    # 1.0 より大きくしても字そのものは帯に収まる。
    SE_SPRITE_SCALE = 1.2
    SE_SPRITE_INDEX = {"ドン": 0, "ド": 1, "コ": 2, "カッ": 3, "カ": 4,
                       "れんだ": 7, "ふうせん": 11, "くすだま": 11}
    SE_SPRITE_INDEX_BIG = {"ドン": 5, "カッ": 6, "れんだ": 10}

    def _load_se_sprites(self):
        """skin/SENotes.png を1段ずつ切り出して返す(無ければ None)。

        段の高さは一定だが字幅は段ごとに違うので、左右だけ実際の字の幅に
        詰めて切る。こうすると音符の x に中心をそろえて置ける。縦は段の
        高さのまま残すので、どの段も同じ大きさで並ぶ。"""
        path = os.path.join(str(settings_mod.skin_dir()), "SENotes.png")
        if not os.path.exists(path):
            return None
        img = QImage(path)
        if img.isNull() or img.height() < self.SE_SPRITE_ROWS:
            return None
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            return None
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        arr = np.frombuffer(memoryview(ptr), dtype=np.uint8)
        arr = arr.reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :]
        alpha = arr[:, :, 3]
        rh = h // self.SE_SPRITE_ROWS
        out = []
        base = QPixmap.fromImage(img)
        for r in range(self.SE_SPRITE_ROWS):
            cols = alpha[r * rh:(r + 1) * rh].max(axis=0) > 16
            xs = np.flatnonzero(cols)
            if xs.size == 0:
                out.append(None)
                continue
            x0, x1 = int(xs[0]), int(xs[-1])
            out.append(base.copy(QRect(x0, r * rh, x1 - x0 + 1, rh)))
        return out

    # --- 判定円 (skin/Notes.png の左上1コマ目) -----------------------------
    # Notes.png は 130px グリッドのシートで、**列0は音符ではなく判定円**。
    # 1コマ 130x130 の中で、円の中心はコマの幾何中心(64.5)ではなく 63.5。
    # 実測: 外輪 r=53(線3px) / 内輪 r=35(線3px) / 中央の塗り r=26。
    JUDGE_RING_CELL = 130
    JUDGE_RING_SPRITE_CX = 63.5

    # 風船 (Breaking_0..5.png 各 280x280)。6枚とも結び目が x=11 で固定、
    # 絵の縦中心は 141.5 で一定。よって「セル内の (20, 141.5) を判定円に
    # 合わせる」と、結び目を判定円に留めたまま右へ膨らむ。
    # ゴーゴー中に判定円で燃える炎 (10_Effects/Fire.png)。7コマのループ。
    # 素材の絵は 234x192 と判定円(108)より大きいので縮めて置く。
    GOGO_FIRE_FRAME_SEC = 1.0 / 15.0
    GOGO_FIRE_FIT = 2.25   # 1.0 = 大音符の判定枠ぴったり
    GOGO_FIRE_CELL = (360, 370)
    GOGO_FIRE_OFF = (0, 0)
    BALLOON_CELL = 280
    BALLOON_ANCHOR = (20.0, 141.5)
    BALLOON_SPRITE_SCALE = 0.62      # 満タン(174px)がレーン(130px)に収まる大きさ
    BALLOON_BURST_SEC = 0.07         # 割れたあと破片のコマを出す時間

    def _load_sheet(self, name, cols, cw, ch):
        """横1列のスプライトシートを cols 枚に切る。無ければ None。"""
        sheet = self._load_skin_pixmap(name)
        if sheet is None or sheet.width() < cw * cols or sheet.height() < ch:
            return None
        return [sheet.copy(QRect(i * cw, 0, cw, ch)) for i in range(cols)]

    def _crop_frames(self, frames):
        """コマ列を「全コマの中身の和集合」で切り直す。

        素材のセルは中身より大きく、しかもコマごとに位置が違う。和集合で
        切っておくと、置く側は「切った絵の中心を判定円に合わせる」だけで
        済み、コマ間の揺れもそのまま残る。"""
        if not frames:
            return frames, (0, 0)
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            return frames, (0, 0)
        x0 = y0 = 10 ** 9
        x1 = y1 = -1
        for pm in frames:
            img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            a = np.frombuffer(memoryview(img.constBits()), dtype=np.uint8)
            a = a.reshape(h, img.bytesPerLine() // 4, 4)[:, :w, 3]
            xs = np.flatnonzero(a.max(axis=0) > 16)
            ys = np.flatnonzero(a.max(axis=1) > 16)
            if xs.size == 0:
                continue
            x0 = min(x0, int(xs[0])); x1 = max(x1, int(xs[-1]))
            y0 = min(y0, int(ys[0])); y1 = max(y1, int(ys[-1]))
        if x1 < x0:
            return frames, (0, 0)
        r = QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        return [pm.copy(r) for pm in frames], (x0, y0)

    def _load_judge_ring(self):
        """Notes.png の左上 130x130 を判定円として切り出す。無ければ None。"""
        sheet = self._load_skin_pixmap("Notes.png")
        if sheet is None:
            return None
        c = self.JUDGE_RING_CELL
        if sheet.width() < c or sheet.height() < c:
            return None
        return sheet.copy(QRect(0, 0, c, c))

    # --- 叩いた瞬間の火花 (skin/HitExplosion.png) -------------------------
    # 1820x1040 = 260x260 のセルが 7列x4行。ただし中身があるのは左5列だけで、
    # 右2列は完全な空白。行の意味は「面/縁」ではなく2層構成:
    #   0行=小音符の炎 / 1行=小音符の銀   2行=大音符の炎 / 3行=大音符の銀
    # 銀は炎の上に重ねて1つの絵になる(銀の形は炎にほぼ内包される)。
    # 5コマ目は4コマ目と同じ絵のアルファ半分＝消え際なので、そのまま使う。
    HIT_EXP_CELL = 260
    HIT_EXP_FRAMES = 5
    # コマ送りが速いほど「弾ける」感じが強くなる。0.025 だと 40fps 相当で
    # 動きがきつかったので、60fps で1コマ2フレーム相当まで緩めた。
    HIT_EXP_FRAME_SEC = 0.033      # 5コマで約 0.167 秒
    # 全体の濃さ。素材そのままの全不透明だと音符ごとに強く光って目が疲れる。
    HIT_EXP_OPACITY = 0.80
    # 終わり際は濃さを落としてなめらかに消す(最後のコマで急に消えると
    # 瞬いて見える)。ここから先を線形に 0 へ。
    HIT_EXP_FADE_FROM = 0.55       # 0..1 のうちどこからフェードを始めるか
    HIT_EXP_ROWS = (0, 1, 2, 3)    # 小炎, 小銀, 大炎, 大銀

    def _load_explosion_sprites(self):
        """HitExplosion.png を 4行x5コマに切り出す。無ければ None。"""
        sheet = self._load_skin_pixmap("HitExplosion.png")
        if sheet is None:
            return None
        c = self.HIT_EXP_CELL
        if sheet.width() < c * self.HIT_EXP_FRAMES or sheet.height() < c * 4:
            return None
        return [[sheet.copy(QRect(f * c, r * c, c, c))
                 for f in range(self.HIT_EXP_FRAMES)] for r in range(4)]

    def _draw_hit_explosion(self, painter, now, judge_x, mid_y):
        """判定円の位置に火花を出す。音符帯にクリップされたまま呼ぶこと
        (音符より先に描いて、音符が上に来るようにする)。"""
        if not self._skin_explosion:
            return
        recent = self._recent_hit(now)
        if recent is None:
            return
        elapsed, char, _n = recent
        span = self.HIT_EXP_FRAME_SEC * self.HIT_EXP_FRAMES
        if not (0.0 <= elapsed < span):
            return
        f = min(self.HIT_EXP_FRAMES - 1, int(elapsed / self.HIT_EXP_FRAME_SEC))
        fire, silver = (2, 3) if char in NOTE_BIG else (0, 1)
        c = self.HIT_EXP_CELL
        x, y = int(judge_x - c / 2), int(mid_y - c / 2)
        # 終わり際だけ濃さを落とす。素材の5コマ目も半透明だが、それだけだと
        # 段が粗くて瞬いて見えるので、時間で連続に落とす。
        q = elapsed / span
        op = self.HIT_EXP_OPACITY
        if q > self.HIT_EXP_FADE_FROM:
            op *= max(0.0, 1.0 - (q - self.HIT_EXP_FADE_FROM) / (1.0 - self.HIT_EXP_FADE_FROM))
        painter.setOpacity(op)
        painter.drawPixmap(x, y, self._skin_explosion[fire][f])
        painter.drawPixmap(x, y, self._skin_explosion[silver][f])
        painter.setOpacity(1.0)

    def _se_scaled(self, label, big, footer_h):
        """打音表記スプライトを帯の高さに合わせて縮小したものを返す(キャッシュ)。

        倍率は footer_h * SE_SPRITE_SCALE で固定なので、毎フレーム変倍する
        必要はない。帯の高さが変わる set_lane_geometry / set_se_text_enabled で
        キャッシュを捨てる。"""
        key = (label, bool(big), int(footer_h))
        pm = self._se_scaled_cache.get(key)
        if pm is None:
            spr = self._se_sprite_for(label, big)
            if spr is None:
                return None
            sh = footer_h * self.SE_SPRITE_SCALE
            sw = spr.width() * (sh / spr.height())
            pm = spr.scaled(max(1, int(round(sw))), max(1, int(round(sh))),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self._se_scaled_cache[key] = pm
        return pm

    def _se_sprite_for(self, label, big):
        """ラベル(と大音符かどうか)から SENotes.png の1枚を選ぶ。"""
        if not self._skin_se:
            return None
        idx = None
        if big:
            idx = self.SE_SPRITE_INDEX_BIG.get(label)
        if idx is None:
            idx = self.SE_SPRITE_INDEX.get(label)
        if idx is None or idx >= len(self._skin_se):
            return None
        return self._skin_se[idx]

    def set_lane_geometry(self, lane_width, lane_height, judge_x,
                          top_margin=0, bottom_margin=0):
        """レーンの寸法を本家(TNDE)の実測値へ差し替える。

        描画コードは一貫して self.LANE_WIDTH / self.JUDGE_X … と参照している
        ので、インスタンス側に同名の属性を置けばクラス定数より優先され、
        描画ロジックには一行も手を入れずに寸法だけ入れ替えられる。

        本家の実測値 (1280x720 のキャプチャより):
            レーン本体 947x130 / 判定円は左端から 81px / 打音表記帯 26px
        従来の単体表示(908x106, 判定円 200px)はクラス定数のままなので、
        この呼び出しをしない限り今までと同じ見た目で動く。"""
        self.LANE_WIDTH = float(lane_width)
        self.LANE_HEIGHT = int(lane_height)
        self.JUDGE_X = float(judge_x)
        # 帯の高さが変わりうるので、事前スケール済み打音スプライトは捨てる。
        self._se_scaled_cache = {}
        self.TOP_MARGIN = int(top_margin)
        self.BOTTOM_MARGIN = int(bottom_margin)
        se = self.SE_FOOTER_HEIGHT
        self.WIDGET_HEIGHT = top_margin + self.LANE_HEIGHT + se + bottom_margin
        self.WIDGET_HEIGHT_NO_SE = top_margin + self.LANE_HEIGHT + bottom_margin
        self.setFixedSize(int(self.LANE_WIDTH), self.widget_height())
        self._rebuild_draw_cache()
        self.update()

    def _rebuild_draw_cache(self):
        """寸法を変えたあとに、寸法依存の下準備をやり直す。"""
        self._se_static_cache.clear()
        if self._preview_data_cache is not None:
            self.set_preview_data(self._preview_data_cache)

    def set_offset(self, offset: float):
        self._offset = offset
        self._rebuild_nav_points()
        self.update()
        self._push_realtime_info()

    # ------------------------------------------------------------------
    # オフライン描画(録画用) — neotja/recorder.py から使う
    # ------------------------------------------------------------------
    def begin_offline_render(self):
        """表示時刻を「外から与える」モードに入る。

        通常このウィジェットは monotonic 時計から現在位置を外挿して描くため、
        描画にかかった時間ぶんだけ絵が進んでしまい、1コマずつ書き出す用途には
        使えない。このモードでは set_render_time() で指定した時刻だけを見て
        描くので、何ms かかっても出来上がりは必ず同じになる(=コマ落ちしない)。

        あわせて録画に写ってはいけないもの/意味の無いものを止める:
        実測fps表示、小節移動のトゥイーン、再描画タイマー。"""
        self._render_time = 0.0
        self._show_fps = False
        self._animating = False
        self._timer.stop()

    def set_render_time(self, seconds: float):
        """オフライン描画モードでの表示時刻(音源時間の秒)。"""
        self._render_time = float(seconds)

    def end_offline_render(self):
        """通常の(時計で進む)描画に戻す。"""
        self._render_time = None

    def set_playback(self, position_seconds: float, playing: bool):
        now_wall = _time.monotonic()
        # During a stopped/paused scroll tween the audio.seek() we issued fires
        # positionChanged; snapping to it would cut the animation short, so
        # ignore those until the tween finalizes. (Only relevant while not
        # playing - navigation can't run during playback, so a tween never
        # overlaps genuine play.)
        if self._animating and not playing:
            return
        if self._playing and playing:
            # 低速再生でも予測が音源位置と合うようレート補正する。
            predicted = self._pos_sec + (now_wall - self._pos_wall) * self._playback_rate
            if abs(position_seconds - predicted) < self.RESYNC_THRESHOLD_SEC:
                return  # ignore small jitter; let the smooth extrapolation continue
        self._pos_sec = position_seconds
        self._pos_wall = now_wall
        if playing != self._playing:
            self._playing = playing
            if playing:
                self._state = "playing"
                # Re-check the display refresh here (the widget is on a real
                # screen once playback starts) so a high-refresh monitor gets
                # 120/144 fps instead of the 60 fps fallback picked at init.
                self._apply_timer_interval()
                self._timer.start()
            else:
                # playing -> paused; also how end-of-media stop arrives (the
                # audio side re-emits playingChanged(False) at EndOfMedia).
                self._state = "paused"
                if not self._animating:
                    self._timer.stop()
        self.update()
        self._push_realtime_info()

    def _current_audio_time(self) -> float:
        # 録画中は外から与えられた時刻がすべて(begin_offline_render 参照)。
        if self._render_time is not None:
            return self._render_time
        if self._animating:
            elapsed = _time.monotonic() - self._anim_start_wall
            if elapsed >= self.SCROLL_ANIM_SEC:
                return self._anim_target_sec
            # Ease-out cubic: fast at first, settling gently onto the target.
            t = elapsed / self.SCROLL_ANIM_SEC
            eased = 1.0 - (1.0 - t) ** 3
            return self._anim_start_sec + (self._anim_target_sec - self._anim_start_sec) * eased
        # 再生中はレート補正付きで外挿(トゥイーン中の上ブランチはUIアニメ用
        # なので速度倍率とは無関係、変更しない)。
        if self._playing:
            return self._pos_sec + (_time.monotonic() - self._pos_wall) * self._playback_rate
        return self._pos_sec

    def _current_chart_time(self) -> float:
        # OFFSET convention: chart_time = audio_time + OFFSET.
        return self._current_audio_time() + self._offset

    def _speed(self, bpm: float, scroll: float = 1.0) -> float:
        b = bpm if bpm and bpm > 0 else DEFAULT_BPM
        # 等速モードでは HS(scroll)を 1.0 固定にする(表示だけ)。
        if self._constant_speed:
            s = 1.0
        else:
            s = scroll if scroll is not None else 1.0
        return self.BASE_PIXELS_PER_BEAT * b / 60.0 * s

    def toggle_constant_speed(self):
        """Tab: 等速モード(HS1固定表示)のオン/オフ。スパンの描画速度や可視窓は
        _speed に依存するので、切替時に前計算をやり直す。"""
        self._constant_speed = not self._constant_speed
        self._rebuild_span_draw_data()
        self._rebuild_min_vis_speed()
        self.show_toast("等速モード: " + ("ON (HS1固定)" if self._constant_speed else "OFF"), 1.5)
        self.update()

    def _speed_at(self, t: float) -> float:
        """On-screen speed implied by the BPM/SCROLL in effect at chart time
        `t`. build_preview_timeline already hands us sorted "bpm_changes" /
        "scroll_changes" for exactly this bisect lookup, so nothing here
        re-derives timing. Only ever called from _rebuild_span_draw_data
        (once per chart edit), never per frame."""
        bpm = self._bpm_changes[self._idx_at(self._bpm_times, t)][1]
        scroll = self._scroll_changes[self._idx_at(self._scroll_times, t)][1]
        return self._speed(bpm, scroll)

    def _rebuild_span_draw_data(self):
        """Resolve head/tail on-screen speeds for every roll/balloon/kusudama.

        Previously both ends of a span were positioned with the span's
        *starting* bpm+scroll, so a #SCROLL or #BPMCHANGE landing inside the
        span put the tail in the wrong place. PeepoDrumKit computes the head
        and tail lane coordinates completely independently
        (chart_editor_widgets_game.cpp:733-735, two separate
        GetNoteCoordinatesLane calls with the tail's own Tempo/ScrollSpeed),
        which is what this reproduces: the bar visibly stretches or
        compresses across a mid-span change.

        The head keeps the bpm/scroll the analyzer already attached to it
        (authoritative for mid-measure cases); only the tail needs a lookup.
        Both are resolved here, outside the paint loop, so the 144 Hz redraw
        just multiplies two precomputed floats."""
        self._roll_draw = [
            (r[0], r[1],
             self._speed(r[3], r[4]), self._speed_at(r[1]),
             self.NOTE_R_BIG if r[2] == "6" else self.NOTE_R_SMALL)
            for r in self._rolls
        ]
        # 風船/くす玉は打数(最後の要素)も持ち回る: 判定枠に固定されている間、
        # 残り打数をカウントダウン表示するため。
        self._balloon_draw = [
            (b[0], b[1], self._speed(b[2], b[3]), self._speed_at(b[1]), int(b[-1]))
            for b in self._balloons
        ]
        self._kusudama_draw = [
            (k[0], k[1], self._speed(k[2], k[3]), self._speed_at(k[1]), int(k[-1]))
            for k in self._kusudamas
        ]
        # 破裂時刻(= 各風船/くす玉の終点、譜面時間・昇順)。再生中に now が
        # ここを跨いだら破裂音を鳴らす。
        self._pop_times = sorted(
            [b[1] for b in self._balloons] + [k[1] for k in self._kusudamas]
        )
        self._last_pop_scan_t = None

    def _rebuild_min_vis_speed(self):
        """Slowest positive on-screen speed among the chart's notes and bars,
        capped at the 60-BPM reference. Drives the visible-time window so even
        very slow charts (low BPM or #SCROLL < 1) slide their notes in from the
        right edge instead of popping in mid-lane. Rebuilt once per chart edit.
        Non-positive speeds (#SCROLL <= 0 gimmicks) are ignored - those notes
        don't approach from the right edge, and the span cull handles them by
        pixel extent anyway."""
        ref = self.BASE_PIXELS_PER_BEAT * self.WINDOW_REF_BPM / 60.0
        # 音符ごとの見かけ速度は BPM と #SCROLL だけで決まる(等速モードでも
        # 一括で 1.0 固定になるだけ)。毎フレーム 300〜400 回 _speed() を
        # 呼んでいたので、ここで1本の配列にしておいて描画側は掛け算1つで済ます。
        self._note_speeds = [self._speed(bpm, sc)
                             for bpm, sc in zip(self._note_bpms, self._note_scrolls)]
        self._bar_speeds = [self._speed(bpm, sc)
                            for bpm, sc in zip(self._bar_bpms, self._bar_scrolls)]
        slowest = ref
        for s in self._note_speeds:
            if 0.0 < s < slowest:
                slowest = s
        for s in self._bar_speeds:
            if 0.0 < s < slowest:
                slowest = s
        self._min_vis_speed = slowest

    def _visible_window(self, now, w, judge_x):
        # Convert the visible pixel span into a time window for the note/bar
        # bisect. Window width = pixels / speed, so the SLOWER the on-screen
        # speed, the WIDER the time window a note needs to be caught before it
        # reaches the right edge. Using a fixed 60-BPM reference used to
        # under-size the window for genuinely slow charts (low BPM or #SCROLL
        # < 1), so a slow note got culled until it had already scrolled partway
        # in - it "appeared mid-lane" instead of sliding in from the edge.
        # _min_vis_speed is the actual slowest on-screen speed in the chart
        # (never above the 60-BPM reference), so the window is always wide
        # enough for the slowest note; faster notes just get a few harmless
        # extra candidates bisected in (all clipped to the box anyway).
        speed = self._min_vis_speed
        return now - judge_x / speed, now + (w - judge_x) / speed

    def _draw_balloon_sprite(self, painter, judge_x, mid_y, frame):
        """風船を1コマ描く。結び目(セル内 (20,141.5))を判定円に合わせる。"""
        spr = self._skin_balloon_seq[max(0, min(5, int(frame)))]
        if spr is None:
            return
        k = self.BALLOON_SPRITE_SCALE
        ax, ay = self.BALLOON_ANCHOR
        w = self.BALLOON_CELL * k
        painter.drawPixmap(QRectF(judge_x - ax * k, mid_y - ay * k, w, w),
                           spr, QRectF(spr.rect()))

    def _live_top_count(self, now):
        """上部読み出し(判定リングの右)に出す打数。連打・風船・くす玉で
        表記位置とデザインを共通化する。
        - 連打: 区間中は補間で数え上がる。
        - 風船/くす玉: 区間中は残り打数をカウントダウン(割れる終点で消える)。
        **今まさに進行中(now が [start,end] の中)** の区間だけを対象にする。
        以前は連打終了後 1 秒だけ最終打数を保持していたが、その保持中に直後の
        風船が流れてくると、風船の脇に連打の打数が出たままになり「風船の打数が
        連打の打数に影響される」ように見えるバグになっていた。保持はやめ、常に
        進行中の区間の値だけを出す。"""
        for r in self._rolls:
            start, end, hits = r[0], r[1], r[-1]
            if start <= now <= end:
                if end <= start:
                    return hits
                return int(hits * (now - start) / (end - start))
        for spans in (self._balloons, self._kusudamas):
            for s in spans:
                start, end, hits = s[0], s[1], s[-1]
                if start <= now < end:
                    span = end - start
                    frac = (now - start) / span if span > 0 else 1.0
                    rem = int(hits * (1.0 - frac)) + (1 if frac < 1.0 else 0)
                    return max(0, min(int(hits), rem))
        return None

    def _cumulative_hits(self, now):
        # Running total across the whole song so far: full hits for every
        # roll/balloon that's already finished, plus the in-progress partial
        # count for whichever one (if any) is currently active - so it
        # climbs continuously during playback instead of jumping straight to
        # the whole-course total up front.
        total = 0
        for spans in (self._rolls, self._balloons, self._kusudamas):
            for span in spans:
                start, end, hits = span[0], span[1], span[-1]
                if end <= now:
                    total += hits
                elif start <= now < end and end > start:
                    total += int(hits * (now - start) / (end - start))
        return total

    def set_info_update_cb(self, cb):
        self._info_update_cb = cb

    def set_frame_cb(self, cb):
        """毎フレーム(120fps)現在の音源時刻(秒)を受け取るコールバックを登録。
        作譜モードの波形をレーンと同じクロックで滑らかに追従させるのに使う。"""
        self._frame_cb = cb

    def set_hit_feedback_cb(self, cb):
        """操作フィードバックのドン/カ単発再生 cb(kind: 'don'|'ka')を登録。"""
        self._hit_feedback_cb = cb

    def set_fadein_play_cb(self, cb):
        """f/j 再生用: 小節頭の少し前からフェードインして再生する cb(measure_time)
        を登録。未登録なら f/j は通常再生にフォールバックする。"""
        self._fadein_play_cb = cb

    def set_checkpoints_changed_cb(self, cb):
        self._checkpoints_changed_cb = cb

    def set_checkpoints(self, times):
        """チェックポイント(音源時刻の列)を外部から設定する(将来のエディタ同期
        用の入口)。昇順に正規化して保持する。"""
        self._checkpoints = sorted(float(t) for t in (times or []))
        self.update()

    @staticmethod
    def _idx_at(times, now):
        # times is a sorted list with an entry at 0.0; index of the last one
        # at or before now.
        return max(0, min(bisect.bisect_right(times, now) - 1, len(times) - 1))

    def _push_realtime_info(self):
        if not self._info_update_cb:
            return
        now = self._current_chart_time()
        bpm = self._bpm_changes[self._idx_at(self._bpm_times, now)][1]
        scroll = self._scroll_changes[self._idx_at(self._scroll_times, now)][1]
        mi = self._idx_at(self._measure_times, now)
        m_num = int(self._measure_changes[mi][1])
        m_den = int(self._measure_changes[mi][2])
        cumulative_hits = self._cumulative_hits(now)
        # live_count (the in-progress roll/balloon tap count) is drawn
        # directly above the lane in paintEvent now, not routed through the
        # info bar below it, so it isn't passed here anymore.
        #
        # Only push when a displayed value actually changed - these update a
        # few times per second at most, so firing the info-bar QLabel setters
        # on every 8-16 ms tick would be almost entirely redundant work.
        info = (bpm, scroll, m_num, m_den, cumulative_hits)
        if info == self._last_info:
            return
        self._last_info = info
        self._info_update_cb(bpm, scroll, m_num, m_den, cumulative_hits)

    def _draw_note(self, painter: QPainter, x: float, y: float, r: int, c: str, big: bool):
        sprite = (self._sprites_big if big else self._sprites_small).get(c)
        if sprite is not None:
            painter.drawPixmap(QPointF(x - r, y - r), sprite)  # sub-pixel placement
            return
        painter.setPen(QPen(self._color("fg_bright"), 2))
        painter.setBrush(QBrush(self._color(NOTE_COLOR[c])))
        painter.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)

    def _draw_roll_bar(self, painter: QPainter, x0: float, x1: float, cy: float, r: int, color: QColor):
        d = r * 2
        # A negative/zero #SCROLL (or a big enough mid-span speed change) can
        # put the tail to the LEFT of the head, so the body rect is built
        # from the ordered pair rather than assuming x1 >= x0 - otherwise the
        # rect would collapse to the 1 px minimum and the bar would look
        # broken.
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(int(x0 - r), int(cy - r), d, d)
        painter.drawRect(int(lo), int(cy - r), max(1, int(hi - lo)), d)
        painter.drawEllipse(int(x1 - r), int(cy - r), d, d)
        # Re-outline just the start point like a normal note (white border)
        # so it reads clearly as "the roll begins here", distinct from the
        # plain bar body and tail.
        painter.setPen(QPen(self._color("fg_bright"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(x0 - r), int(cy - r), d, d)

    def _draw_balloon_note(self, painter, x, cy):
        """A single 風船/くす玉 note centred at (x, cy): the balloon sprite's
        round face sized to a small note (no duration bar). Falls back to a
        procedural balloon-coloured circle when there's no skin."""
        r = self.NOTE_R_SMALL
        if self._skin_balloon is not None:
            scaled = self._skin_balloon["scaled"]
            face_r = self._skin_balloon["face_r"]
            painter.drawPixmap(QPointF(x - face_r, cy - scaled.height() / 2.0), scaled)
        else:
            painter.setPen(QPen(self._color("fg_bright"), 2))
            painter.setBrush(QBrush(self._color("balloon")))
            painter.drawEllipse(int(x - r), int(cy - r), r * 2, r * 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Sample note/roll/balloon sprites at sub-pixel offsets so a note gliding
        # across the lane moves smoothly instead of snapping a whole pixel at a
        # time - this makes the scroll look noticeably smoother at the SAME fps.
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        # The lane box is a fixed pixel size (LANE_WIDTH x LANE_HEIGHT), not
        # tied to the widget's actual size, so resizing/maximizing the
        # window just leaves blank space (or crops it) rather than
        # stretching the scale or revealing more lookahead.
        #
        # band_top/band_bottom are resolved to ints once, here, and
        # band_bottom is derived as band_top + band_h rather than computed
        # independently from mid_y - int(band_top) and int(band_bottom)
        # used to each round mid_y +/- band_h/2 separately, which could
        # disagree by a pixel and show up as the fill/border/notes not quite
        # lining up top vs. bottom.
        lane_w = int(self.LANE_WIDTH)
        judge_x = int(self.JUDGE_X)
        band_h = int(self.LANE_HEIGHT)
        # Fixed top margin (not vertically centered) - the leftover space
        # above the lane box is where the live roll/balloon tap count reads
        # out, so it needs to be consistently tall enough for that text
        # rather than shrinking to whatever's left after centering.
        band_top = int(self.TOP_MARGIN)
        band_bottom = band_top + band_h
        mid_y = band_top + band_h / 2.0
        # 打音表記の帯: 音符帯の直下、レーン枠の内側(PeepoDrumKit の
        # GameLaneSlice.Footer 相当)。band_bottom の線がそのまま MidBorder。
        # 打音表記がオフのときは帯そのものを確保しない(= ウィジェットが
        # 26px 低くなる。窓を大きくしたくないという要望のため)。
        footer_h = int(self.SE_FOOTER_HEIGHT) if self._se_text_enabled else 0
        footer_bottom = band_bottom + footer_h

        painter.fillRect(self.rect(), self._color("bg"))

        now = self._current_chart_time()
        # f/j リード再生: 再生開始位置(reveal_t)より前の音符/連打/風船だけを
        # 隠す。開始位置以降の音符は通常どおり右から流れてくる(いきなり全部が
        # 出現しない)。到達後(reveal_cb で None化)は全表示に戻る。
        reveal_t = self._reveal_time

        # 実測fps(paintEvent 間隔のEMA)。左上に小さく出す。実際にフレームが
        # 出ているかの目安 - 表示がカクつく時に「何fps出ているか」を可視化する。
        # 本家レイアウト(GameScreenWidget の中)では TOP_MARGIN=0 なので、この
        # fps 文字も下の打数も**画面外**にしか描けない。文字列組み立てと
        # drawText、さらに _live_top_count の全span走査ごと丸ごと省く。
        if self._show_fps and band_top > 0:
            wall = _time.monotonic()
            if self._fps_last_wall is not None:
                dt = wall - self._fps_last_wall
                if dt > 0.0:
                    inst = 1.0 / dt
                    self._fps_ema = inst if self._fps_ema <= 0.0 else self._fps_ema * 0.85 + inst * 0.15
            self._fps_last_wall = wall
            painter.setPen(self._color("fg_dim"))
            painter.setFont(self._font(10, True))
            painter.drawText(6, 2, 96, band_top - 4, Qt.AlignLeft | Qt.AlignVCenter,
                             f"{self._fps_ema:.0f} fps")

        # Live tap count, in the top margin to the RIGHT of the judgment ring,
        # so it no longer collides with the 良 judge pop that rises just above
        # the ring. Shared by 連打 (count up) and 風船/くす玉 (count down) with
        # the same position and design. Must be drawn before the lane clip
        # below (that clip starts at band_top, so top-margin text would
        # otherwise be clipped away).
        roll_count = self._live_top_count(now) if band_top > 0 else None
        if roll_count is not None:
            judge_r_top = self.NOTE_R_BIG + 5
            painter.setPen(self._color("roll"))
            painter.setFont(self._font(22, True))
            box_x = judge_x + judge_r_top + 8
            box_w = max(60, lane_w - box_x)
            painter.drawText(int(box_x), 2, int(box_w), band_top - 4,
                             Qt.AlignLeft | Qt.AlignVCenter, str(roll_count))

        # (The カレント/アンカー readout used to live here in the top margin.
        # Removed by request - the measure counter under the judgment ring and
        # the highlighted anchor bar line already convey the same thing.)

        # Transient toast badge, lane top-left. Drawn as a filled box so it
        # stays legible over whatever notes happen to be scrolling under it,
        # and before the lane clip so it isn't cut off.
        if self._toast_text:
            painter.setFont(self._font(15, True))
            tw = painter.fontMetrics().horizontalAdvance(self._toast_text) + 24
            th = 32
            tx, ty = self.PANEL_INSET, band_top + 8
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color("bg2"))
            painter.drawRoundedRect(tx, ty, tw, th, 6, 6)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(self._color("accent"))
            painter.drawRoundedRect(tx, ty, tw, th, 6, 6)
            painter.setPen(self._color("fg_bright"))
            painter.drawText(tx, ty, tw, th, Qt.AlignCenter, self._toast_text)

        # Notes/rolls/bars are positioned by real formulas that can compute
        # an x past lane_w (the bisect window is deliberately sized
        # conservatively - see _visible_window - so a note whose own BPM
        # gives it a faster on-screen speed than that conservative estimate
        # can land beyond the box edge). Clipping to the box, rather than
        # trying to filter every element by its exact rendered x, guarantees
        # nothing ever draws outside the fixed-size lane regardless of
        # window size or per-note speed.
        painter.setClipRect(0, band_top, lane_w, band_h)

        # レーンの地。スキンに素材があればそれを敷く(本家と同じ色になる)。
        skinned = self._skin_lane_main is not None
        if skinned:
            painter.drawPixmap(QRectF(0, band_top, lane_w, band_h),
                               self._skin_lane_main,
                               QRectF(self._skin_lane_main.rect()))
        else:
            painter.fillRect(0, band_top, lane_w, band_h, self._color("surface"))

        # ゴーゴー。本家の素材は「半透明の赤(左が濃く右へ薄れる)」なので、
        # 地に差し替えるのではなく地の上に重ねる。素材が無いときだけ、
        # 従来どおり画面全体を一色で染める。
        # 全区間の線形走査をやめ、開始時刻列(_gogo_starts)の bisect で
        # 「now 以前に始まった最後の区間」だけを見る(gogo_pulse と同じ手法)。
        _gi = bisect.bisect_right(self._gogo_starts, now) - 1
        in_gogo = (_gi >= 0 and now <= self._gogo_regions[_gi][1])
        if in_gogo:
            if self._skin_lane_gogo is not None:
                painter.drawPixmap(QRectF(0, band_top, lane_w, band_h),
                                   self._skin_lane_gogo,
                                   QRectF(self._skin_lane_gogo.rect()))
            else:
                painter.fillRect(self.rect(), GOGO_TINT)

        # 叩いた瞬間の火花。地の上・音符の下に置く(本家も音符が上に来る)。
        # 音符帯へのクリップが効いているので、260px の絵は帯の高さで切れる。
        self._draw_hit_explosion(painter, now, judge_x, mid_y)

        # 枠線と中央の破線は自前で描いていたもの。素材を敷いているときは
        # 黒枠(Taiko_Frame)が外周を担当するし、本家に破線は無いので描かない。
        if not skinned:
            painter.setPen(QPen(self._color("border"), 2))
            painter.drawLine(0, band_top, lane_w, band_top)
            painter.drawLine(0, band_bottom, lane_w, band_bottom)
            painter.drawLine(lane_w, band_top, lane_w, band_bottom)
            painter.setPen(QPen(self._color("border"), 1, Qt.DashLine))
            painter.drawLine(0, int(mid_y), lane_w, int(mid_y))

        t_past, t_future = self._visible_window(now, lane_w, judge_x)

        # --- bar (measure) lines. #BARLINEOFF/#BARLINEON only hides the
        # visual line - the boundary itself always stays in _bar_times (and
        # therefore in _nav_points/measure navigation/the measure counter
        # below), so skipping a hidden entry here is purely cosmetic. ---
        lo_bar = bisect.bisect_left(self._bar_times, t_past)
        hi_bar = bisect.bisect_right(self._bar_times, t_future)
        # チェックポイントの小節線は黄色で強調する(アンカーの代替)。
        # チェックポイントは音源時刻なので、小節の譜面時刻から OFFSET を引いて
        # 突き合わせる。
        cps = self._checkpoints
        snap = self.CHECKPOINT_SNAP
        pen_bar = QPen(self._color("fg_dim"), 2)
        pen_cp = QPen(self._color("checkpoint"), 3)
        for i in range(lo_bar, hi_bar):
            if not self._bar_visible[i]:
                continue
            bt = self._bar_times[i]
            x = judge_x + (bt - now) * self._bar_speeds[i]
            is_cp = False
            if cps:
                at = bt - self._offset
                for c in cps:
                    if abs(c - at) < snap:
                        is_cp = True
                        break
            painter.setPen(pen_cp if is_cp else pen_bar)
            painter.drawLine(int(x), band_top, int(x), band_bottom)

        # --- judgment ring (drawn BEFORE notes/rolls so they pass over it,
        # like notes crossing the drum face in the real game). ---
        # 本家(TNDE)のキャプチャ実測: 外側の輪が半径53、内側の輪が半径35で、
        # 音符(直径52)がちょうど内側に収まる大きさ。従来は NOTE_R_BIG+5(=43)
        # と小さく、そのぶん音符が痩せて見えていた。JUDGE_RING_R を持たせて
        # おき、本家レイアウト以外でも同じ比率になるようにする。
        judge_r = self.JUDGE_RING_R
        judge_r_inner = self.JUDGE_RING_R_INNER
        if self._skin_judge_ring is not None:
            # 本家の判定円は Notes.png の左上1コマ目に入っている。しかも
            # **加算合成**で描かれている — レーンの地色に素材の値を足すと、
            # 本家キャプチャの画素と1の位まで一致する。通常のアルファ合成で
            # 描くと本家より暗い灰色の塊になってしまう。
            spr = self._skin_judge_ring
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            painter.drawPixmap(int(judge_x - self.JUDGE_RING_SPRITE_CX),
                               int(mid_y - self.JUDGE_RING_SPRITE_CX), spr)
            painter.restore()
        else:
            painter.setPen(QPen(self._color("fg_bright"), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(judge_x - judge_r), int(mid_y - judge_r),
                                judge_r * 2, judge_r * 2)
            painter.drawEllipse(int(judge_x - judge_r_inner), int(mid_y - judge_r_inner),
                                judge_r_inner * 2, judge_r_inner * 2)

        # --- GOGO judgment-ring pulse ------------------------------------
        # PeepoDrumKit pulses a flame sprite centered on the hit circle with
        # an ADSR-ish envelope (getGogoZoomAmount,
        # chart_editor_widgets_game.cpp:120-134; used at :640/:690 as the
        # Game_Lane_GogoFire sprite's scale). There is no sprite sheet here,
        # so the same envelope drives a QPainter glow ring instead: it grows
        # outward from the judgment circle and thickens, snapping bright on
        # every gogo entry and easing back to a steady hum while inside.
        # Drawn in the note "don" color, which is a saturated red in both the
        # light and dark palettes, and layered ON TOP of the flat GOGO_TINT
        # wash above (the wash is unchanged - it was explicitly requested).
        # NOTE: the lane-zoom half of getGogoZoomAmount (tAttLane) is
        # deliberately not ported - the lane's proportions are fixed.
        gogo_env = self.gogo_pulse(now)
        if gogo_env > 0.0 and self._skin_gogo_fire is not None:
            # 本家の炎(7コマのループ)を判定円に重ねる。素材が無いときだけ
            # 下の自前リングに落ちる。
            fr = self._skin_gogo_fire[int(now / self.GOGO_FIRE_FRAME_SEC)
                                      % len(self._skin_gogo_fire)]
            # 大音符の判定枠(外輪の直径 = JUDGE_RING_R*2)に横幅を合わせる。
            k = (2.0 * self.JUDGE_RING_R * self.GOGO_FIRE_FIT / fr.width()
                 * (0.92 + 0.16 * gogo_env))
            fw, fh = fr.width() * k, fr.height() * k
            # 切る前のセル中心(180,185)が判定円に来るように置く。切った矩形の
            # 中心に合わせると、炎が右上に伸びている絵なので位置がずれる。
            ox, oy = self._gogo_fire_org
            ax = (self.GOGO_FIRE_CELL[0] / 2.0 - ox) * k
            ay = (self.GOGO_FIRE_CELL[1] / 2.0 - oy) * k
            # 素材は不透明に近いフラットな橙のシルエットなので、そのまま置くと
            # 判定円を塗りつぶした塊になる。判定円と同じく**加算合成**にすると
            # 地の上で光って見え、下の判定円も透ける。
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            painter.setOpacity(min(1.0, 0.45 + 0.35 * gogo_env))
            painter.drawPixmap(QRectF(judge_x - ax + self.GOGO_FIRE_OFF[0],
                                      mid_y - ay + self.GOGO_FIRE_OFF[1],
                                      fw, fh), fr, QRectF(fr.rect()))
            painter.restore()
        elif gogo_env > 0.0:
            glow_r = int(judge_r + 4 + 11 * gogo_env)
            painter.setOpacity(0.18 + 0.55 * gogo_env)
            painter.setPen(QPen(self._color("don"), 2.0 + 5.0 * gogo_env))
            painter.drawEllipse(int(judge_x - glow_r), int(mid_y - glow_r), glow_r * 2, glow_r * 2)
            painter.setOpacity(0.30 + 0.60 * gogo_env)
            painter.setPen(QPen(self._color("don"), 3))
            painter.drawEllipse(int(judge_x - judge_r), int(mid_y - judge_r), judge_r * 2, judge_r * 2)
            painter.setOpacity(1.0)

        # --- notes, rolls, balloons and kusudama, drawn in ONE pass sorted
        # by time descending so earlier objects land on top of later ones -
        # exactly like the real game (太鼓の達人: 時間が早い音符ほど手前)。
        # Previously rolls/balloons were drawn in their own passes *before* all
        # notes, so an earlier roll that a #SCROLL let overtake a later note
        # got drawn UNDERNEATH that note ("後ろからぬかす"). Merging everything
        # into a single back-to-front pass fixes that ordering. Rolls/spans use
        # their START time as the z-key (an earlier-starting roll is in front).
        #
        # Roll head/tail are positioned INDEPENDENTLY from the on-screen speed
        # at their own time (precomputed in _rebuild_span_draw_data), so a
        # mid-span #SCROLL/#BPMCHANGE stretches the bar like the real game; the
        # cull is on the actual pixel extent (Camera.IsRangeVisibleOnLane,
        # chart_editor_widgets_game.cpp:779), not the time window.
        note_t_past = now - self.HIT_ANIM_DURATION
        lo = bisect.bisect_left(self._note_times, note_t_past)
        hi = bisect.bisect_right(self._note_times, t_future)
        rs = self.NOTE_R_SMALL
        draw_items = []
        for r_start, r_end, sp0, sp1, r in self._roll_draw:
            x0 = judge_x + (r_start - now) * sp0
            x1 = judge_x + (r_end - now) * sp1
            if (x0 < -r and x1 < -r) or (x0 > lane_w + r and x1 > lane_w + r):
                continue
            draw_items.append((r_start, "roll", (x0, x1, r, r_start, r_end)))
        # 風船・くす玉: 終点バーは出さず、風船ノーツ1個だけを描く。区間に入る
        # 前は右から流れてきて、区間中(now が [start,end])は判定枠に固定する。
        # 固定されている間ずっと表示されるので、いつまで残っているか分かる。
        # 割れたあとも少しだけ破片のコマを残す。
        burst = self.BALLOON_BURST_SEC if self._skin_balloon_seq is not None else 0.0
        for b_start, b_end, sp0, sp1, b_hits in self._balloon_draw:
            if now >= b_end + burst:
                continue
            x0 = judge_x + (b_start - now) * sp0
            if now < b_start and x0 > lane_w + rs:
                continue
            draw_items.append((b_start, "balloon", (b_start, b_end, sp0, b_hits)))
        for k_start, k_end, sp0, sp1, k_hits in self._kusudama_draw:
            if now >= k_end + burst:
                continue
            x0 = judge_x + (k_start - now) * sp0
            if now < k_start and x0 > lane_w + rs:
                continue
            draw_items.append((k_start, "kusudama", (k_start, k_end, sp0, k_hits)))
        # 音符もレーンの外に出るものはここで落とす。可視の時間窓は「譜面で
        # いちばん遅い見かけ速度」に合わせて広めに取ってあるので(_visible_window)、
        # 速い譜面では窓に入る音符の 6〜9 割がレーンの外にいる。Qt のクリップに
        # 任せると外の音符ぶんだけ drawPixmap と並べ替えが丸ごと無駄になる
        # (実測: ある譜面で 1フレーム 177個の候補のうち可視は 8.8個)。
        # x はここで一度だけ求め、描画側へ渡して二度計算しない。
        rb = self.NOTE_R_BIG
        for i in range(lo, hi):
            t = self._note_times[i]
            if t > now:
                x = judge_x + (t - now) * self._note_speeds[i]
                if x < -rb or x > lane_w + rb:
                    continue
                draw_items.append((t, "note", (i, x)))
            else:
                # 叩いた後の飛んでいく音符。位置は経過時間から別に決まるので
                # ここでは落とさない(描画側で画面外なら飛ばす)。
                draw_items.append((t, "note", (i, None)))
        # Latest first -> earliest drawn last -> earliest ends up on top.
        if reveal_t is not None:
            # 開始位置より前に始まる音符/連打/風船を隠す(z キーは各要素の時刻/
            # 開始時刻なので、これで「開始位置から流れる」見え方になる)。
            draw_items = [d for d in draw_items if d[0] >= reveal_t]
        draw_items.sort(key=lambda d: d[0], reverse=True)
        for t0, kind, payload in draw_items:
            if kind == "roll":
                x0, x1, r, r_start, r_end = payload
                if not self._draw_roll_sprite(painter, x0, x1, mid_y, r, r >= self.NOTE_R_BIG):
                    # Red while being hit (now inside the span), yellow otherwise.
                    color = self._color("don") if r_start <= now <= r_end else self._color("roll")
                    self._draw_roll_bar(painter, x0, x1, mid_y, r, color)
            elif kind in ("balloon", "kusudama"):
                # くす玉も風船と同じ見た目。区間に入る前は右から流れてきて、
                # 区間中(now が [start,end))は判定枠に固定し、残り打数を
                # カウントダウン表示する。終点(end)で割れる = 破裂音を鳴らして
                # 消す(描画対象からも外れている)。指定打数を叩ききれなかった
                # 場合だけ終点まで残る、という本家挙動を「等速で叩いて終点で
                # ちょうど割れる」前提で再現している。
                # 残り打数は上部読み出し(_live_top_count)に連打と同じ表記で
                # 出すので、面には数字を描かない。区間中は判定枠に固定。
                b_start, b_end, sp0, b_hits = payload
                bx = judge_x if now >= b_start else judge_x + (b_start - now) * sp0
                if self._skin_balloon_seq is not None and now >= b_start:
                    # 叩いている間は本家素材に差し替える。残り打数が減るほど
                    # 膨らみ、割れると破片のコマになる。流れてくる間は顔つきの
                    # 音符のままなので、絵が変わるのは判定枠に着いた一度だけ。
                    span = max(1e-6, b_end - b_start)
                    prog = min(1.0, max(0.0, (now - b_start) / span))
                    f = 5 if now >= b_end else min(4, int(prog * 5))
                    self._draw_balloon_sprite(painter, judge_x, mid_y, f)
                else:
                    self._draw_balloon_note(painter, bx, mid_y)
            else:  # note - approach, then fly off after crossing the line.
                i, pre_x = payload
                t = self._note_times[i]
                c = self._note_chars[i]
                big = c in NOTE_BIG
                r = self.NOTE_R_BIG if big else self.NOTE_R_SMALL
                if t <= now:
                    elapsed = now - t
                    dx, dy = self.hit_fly_offset(elapsed)
                    x = judge_x + dx
                    y = mid_y + dy   # path y is world-space (down positive), same as Qt
                    if y + r < band_top or y - r > band_bottom or x - r > lane_w:
                        continue
                    progress = elapsed / self.HIT_ANIM_DURATION
                    if progress > 1.0:
                        progress = 1.0
                    painter.setOpacity(max(0.0, 1.0 - progress))
                    self._draw_note(painter, x, y, max(1, int(r * (1.0 - 0.25 * progress))), c, big)
                    painter.setOpacity(1.0)
                else:
                    self._draw_note(painter, pre_x, mid_y, r, c, big)

        # --- 叩いた瞬間の判定エフェクト (本家風) --------------------------
        # 直近ヒット音符からの経過時間だけで、判定枠から広がるしぶきと「良」の
        # ポップを描く。判定枠のすぐ上・レーンクリップ内なので他の演出の上に
        # 重なって出る。全ノーツ自動ヒットのため判定は常に「良」。
        hit = self._recent_hit(now)
        if hit is not None and (reveal_t is None or (now - hit[0]) >= reveal_t):
            h_elapsed, h_char, _h_combo = hit
            h_big = h_char in NOTE_BIG
            h_base = self.NOTE_R_BIG if h_big else self.NOTE_R_SMALL
            # ヒットしぶき: 判定枠から外へ広がって消える閃光リング + 内側フラッシュ。
            # これは本家の火花(HitExplosion.png)が無いときの代用なので、
            # 素材があるときは描かない — 二重に出て濁って見えるため。
            if self._skin_explosion is None and 0.0 <= h_elapsed < self.HIT_BURST_DURATION:
                bp = h_elapsed / self.HIT_BURST_DURATION      # 0..1
                ring_r = int(h_base + 6 + 34 * bp)
                painter.setBrush(Qt.NoBrush)
                painter.setOpacity(max(0.0, 0.6 * (1.0 - bp)))
                painter.setPen(QPen(self._color("fg_bright"), 3))
                painter.drawEllipse(int(judge_x - ring_r), int(mid_y - ring_r), ring_r * 2, ring_r * 2)
                flash_r = int(judge_r_inner * (1.0 - 0.35 * bp))
                painter.setOpacity(max(0.0, 0.5 * (1.0 - bp)))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(self._color("fg_bright")))
                painter.drawEllipse(int(judge_x - flash_r), int(mid_y - flash_r), flash_r * 2, flash_r * 2)
                painter.setOpacity(1.0)
            # 判定文字「良」: 判定枠の上にポップし、上へ昇りながらフェード。
            # 上マージンへはみ出すので、この文字だけレーンクリップを一時解除して
            # 描き(本家でも判定文字はレーン枠の上に出る)、直後にクリップを戻す。
            # 本家レイアウトでは判定文字は画面側(game_screen.py)がレーンの上へ
            # 重ねて描く。ここで描くとレーン上端で切れて下端だけ残ってしまう。
            if not self._hide_lane_combo and 0.0 <= h_elapsed < self.JUDGE_POP_DURATION:
                jp = h_elapsed / self.JUDGE_POP_DURATION      # 0..1
                rise = 13.0 * (1.0 - (1.0 - jp) ** 2)         # ease-out で上昇(控えめ)
                painter.setClipRect(self.rect())
                painter.setOpacity(max(0.0, 1.0 - jp))
                if self._skin_judge_good is not None:
                    spr = self._skin_judge_good
                    jy = int(mid_y - judge_r - 6 - rise - spr.height())
                    painter.drawPixmap(int(judge_x - spr.width() / 2), jy, spr)
                else:
                    painter.setPen(JUDGE_GOOD)
                    painter.setFont(self._font(20, True))
                    jy = int(mid_y - judge_r - 8 - rise)
                    painter.drawText(int(judge_x - 40), jy, 80, 26, Qt.AlignCenter, "良")
                painter.setOpacity(1.0)
                painter.setClipRect(0, band_top, lane_w, band_h)

        # --- combo readout, covering the lane left of the judgment line
        # (like the real game's score/combo panel). A small gap separates
        # it from the judgment ring - safe now that passed notes fly off
        # instead of lingering there, so there's nothing left to flicker in
        # that gap - and it's inset from the widget's own left edge so it
        # reads as a floating card rather than edge-to-edge. The combo
        # itself is just "how many notes have a time <= now", which comes
        # straight out of the same bisect index already used to pick which
        # notes are visible, so it counts up live during playback and
        # re-syncs instantly on seeks without any extra state.
        #
        # Course/level moved below the lane (see the info bar under the
        # widget), so this panel is combo-only now.
        # 本家レイアウト(game_screen.py)ではコンボは左パネルの太鼓の上に出す
        # ので、レーンの中には描かない。単体表示のときだけ従来どおり出す。
        # 打音表記帯はコンボ表示の有無に関係なく描くので、ここで return しては
        # いけない(以前は return していて、本家レイアウトのときだけ帯が丸ごと
        # 消えていた)。コンボのパネルだけを飛ばす。
        if not self._hide_lane_combo:
            combo = bisect.bisect_right(self._note_times, now)
            panel_right = max(self.PANEL_INSET + 80, judge_x - judge_r - self.PANEL_GAP)
            panel_x = self.PANEL_INSET
            panel_w = panel_right - panel_x
            painter.setPen(QPen(self._color("accent"), 2))
            painter.setBrush(QBrush(self._color("surface")))
            painter.drawRect(int(panel_x), band_top, int(panel_w), band_h)

            painter.setPen(self._color("fg_dim"))
            painter.setFont(self._font(9))
            painter.drawText(int(panel_x), band_top + 6, int(panel_w), 18, Qt.AlignCenter, "コンボ")
            # コンボ数字はヒットのたびにポップ(拡大→等倍)する。直近ヒットからの
            # 経過で倍率を出すステートレス方式なので、シークでも余計な状態を持たない。
            pop = 1.0
            if combo > 0:
                ce = now - self._note_times[combo - 1]
                if 0.0 <= ce < self.COMBO_POP_DURATION:
                    pop = 1.0 + 0.14 * (1.0 - ce / self.COMBO_POP_DURATION)
            num_h = band_h - 28
            num_cx = panel_x + panel_w / 2.0
            num_cy = band_top + 24 + num_h / 2.0
            painter.setPen(self._color("fg_bright"))
            painter.setFont(self._font(22, True))
            if pop > 1.0:
                painter.save()
                painter.translate(num_cx, num_cy)
                painter.scale(pop, pop)
                painter.drawText(int(-panel_w / 2.0), int(-num_h / 2.0), int(panel_w), int(num_h),
                                 Qt.AlignCenter, str(combo))
                painter.restore()
            else:
                painter.drawText(int(panel_x), band_top + 24, int(panel_w), num_h,
                                 Qt.AlignCenter, str(combo))

        painter.setClipRect(self.rect())

        # --- 打音表記 (automatic SE text) -------------------------------
        # A footer strip inside the lane box, mirroring PeepoDrumKit's
        # GameLaneSlice.Footer: each syllable sits horizontally on its note's
        # x but vertically below the note band, so it never covers the note
        # art and never has to fight the red/blue fills for contrast in
        # either theme (it is drawn on `surface`, the same background the
        # notes themselves are drawn on, in the note's own don/ka color).
        # The strip costs height only while se_text_enabled is on - toggling
        # it re-fixes the widget height (see set_se_text_enabled) and the
        # containing window re-fits, rather than the window permanently
        # carrying 26 px of empty strip.
        if self._se_text_enabled:
            # ここまでは音符帯にクリップしたままなので、帯の地を描く前に
            # 打音表記帯の範囲へ切り替える(そうしないと丸ごと切り落とされる)。
            painter.setClipRect(0, band_bottom, lane_w, footer_h)
            # 打音表記帯の地。こちらもスキン素材があれば使う。
            if self._skin_lane_sub is not None:
                # 素材の高さ(26px)ぶんぴったり敷く。1px 空けると譜面レーンとの
                # 間に隙間の線が出て、本家の「地続き」の見え方にならない。
                painter.drawPixmap(QRectF(0, band_bottom, lane_w, footer_h),
                                   self._skin_lane_sub,
                                   QRectF(self._skin_lane_sub.rect()))
            else:
                painter.fillRect(0, band_bottom + 1, lane_w, footer_h - 1, self._color("surface"))
                painter.setPen(QPen(self._color("border"), 2))
                painter.drawLine(0, footer_bottom, lane_w, footer_bottom)
                painter.drawLine(lane_w, band_bottom, lane_w, footer_bottom)
        if self._se_text_enabled and self._note_se:
            painter.setClipRect(0, band_bottom, lane_w, footer_h)
            # 音符の色には合わせない。本家素材の帯(灰色)のときは本家と同じ白。
            # 素材が無いときはテーマの中立色(fg)。
            # 判定枠に重なって叩いた瞬間(t <= now)にラベルは消す - 通り過ぎた
            # 音符には SE 文字を残さない。
            painter.setPen(QColor("#ffffff") if self._skin_lane_sub is not None
                           else self._color("fg"))
            fy = int(band_bottom + footer_h / 2.0)
            # 事前スケール済みスプライトの貼り付け y(帯の上下中央)。ループ内で
            # 毎回計算しないよう1回だけ求める。
            _sh = footer_h * self.SE_SPRITE_SCALE
            se_y = band_bottom + (footer_h - _sh) / 2.0
            for i in range(hi - 1, lo - 1, -1):
                t = self._note_times[i]
                if t <= now:
                    continue
                if reveal_t is not None and t < reveal_t:
                    continue   # リード再生中は開始位置より前の SE 表記も隠す
                label = self._note_se[i]
                if not label:
                    continue
                c = self._note_chars[i]
                big = c in NOTE_BIG
                x = judge_x + (t - now) * self._note_speeds[i]
                # 音符と同じ理由でレーンの外は描かない(上の draw_items の説明)。
                # 文字は音符より小さいので、音符の半径ぶん見ておけば足りる。
                if x < -rb or x > lane_w + rb:
                    continue
                spr = self._se_scaled(label, big, footer_h)
                if spr is not None:
                    # 帯の高さは固定なので倍率も定数。以前は音符1つごとに
                    # QRectF->QRectF の変倍 blit をしていた(可視音符ぶん毎フレーム)。
                    # 事前に縮小したものを等倍で貼るだけにする。
                    # 位置は小数のまま(サブピクセル)。整数へ丸めるとスクロール中に
                    # 文字がカクつき、元の見た目と変わってしまう。拡大縮小だけを
                    # 事前に済ませ、貼る位置の滑らかさは元のままにする。
                    painter.drawPixmap(QPointF(x - spr.width() / 2.0, se_y), spr)
                    continue
                size = self.SE_FONT_SIZE_BIG if big else self.SE_FONT_SIZE_SMALL
                st = self._se_static_text(label, size)
                painter.setFont(self._font(size, True))
                sz = st.size()
                painter.drawStaticText(int(x - sz.width() / 2.0),
                                       int(fy - sz.height() / 2.0), st)
            painter.setClipRect(self.rect())

        # Current measure / total measures ("15/90"), below the judgment
        # ring in the bottom margin - same bisect-over-bar_times approach as
        # seek_relative_measure, so "current measure" always agrees with
        # what PgUp/PgDn/wheel navigation would jump from.
        # 下マージンが無いレイアウト(本家画面)では画面外なので、f文字列と
        # drawText ごと省く。
        if self.BOTTOM_MARGIN > 0:
            if self._bar_times:
                measure_idx = min(len(self._bar_times), bisect.bisect_right(self._bar_times, now))
                measure_text = f"{measure_idx}/{len(self._bar_times)}"
            else:
                measure_text = "-"
            painter.setPen(self._color("fg_dim"))
            painter.setFont(self._font(12, True))
            box_w = 160
            painter.drawText(int(judge_x - box_w / 2), footer_bottom + 2, box_w,
                             self.BOTTOM_MARGIN - 4, Qt.AlignCenter, measure_text)

        # Focus indicator: matches the accent-colored :focus border the QSS
        # theme already gives the text editor (QPlainTextEdit:focus), so
        # whichever of the two panes has keyboard focus - and therefore
        # receives Space/Q/PgUp/PgDn - is visually obvious at a glance.
        # 本家レイアウトでは出さない(本家に無いものなので)。単体のレーン
        # 表示のときだけ、どちらのペインがキー入力を受けるかの目印として残す。
        if self.hasFocus() and not self._hide_lane_combo:
            pen = QPen(self._color("accent"), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(1, 1, w - 2, h - 2)

        # 音源を読み込んでいるあいだは再生できないので、レーンにそう出す。
        # 譜面の上に暗幕を敷いてから字を置く(下に音符が透けていると、
        # 動いていないのか読み込み中なのか分からない)。
        if self._loading:
            painter.setClipRect(0, band_top, lane_w, band_h)
            painter.fillRect(0, band_top, int(lane_w), band_h, QColor(0, 0, 0, 170))
            painter.setPen(QColor(0, 0, 0, 200))
            painter.setFont(self._font(self.LOADING_FONT_SIZE, True))
            painter.drawText(2, band_top + 2, int(lane_w), band_h,
                             Qt.AlignCenter, self.LOADING_TEXT)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(0, band_top, int(lane_w), band_h,
                             Qt.AlignCenter, self.LOADING_TEXT)
            painter.setClipRect(self.rect())

        painter.end()
