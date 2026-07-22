import bisect
import time as _time

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QStaticText,
)
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

    BASE_PIXELS_PER_BEAT = 189.0
    WINDOW_REF_BPM = 60.0  # lower bound used only to size the visible-time window (see _visible_window)
    JUDGE_X = 200.0            # fixed pixel offset - not a ratio of widget width, so it never moves on resize
    LOOKAHEAD_BEATS = 3.75     # 15 sixteenth notes: show up to the 15th, not the 16th, of a 4/4 measure ahead
    LANE_WIDTH = JUDGE_X + LOOKAHEAD_BEATS * BASE_PIXELS_PER_BEAT  # fixed total box width, independent of the widget/window size
    NOTE_R_SMALL = 28
    NOTE_R_BIG = 38
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
        self._note_times = []
        self._note_chars = []
        self._note_bpms = []
        self._note_scrolls = []
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
        # 下部パネルのモード循環(Tab / トグルボタン)と速度変更([ ] キー)を
        # ゲーム窓側へ通知するコールバック(フェーズ3)。
        self._cycle_bottom_mode_cb = cycle_bottom_mode_cb
        self._set_speed_cb = set_speed_cb
        self._offset = 0.0
        self._pos_sec = 0.0
        self._pos_wall = _time.monotonic()
        self._playing = False
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
        self._se_static_family = None

        self._timer = QTimer(self)
        # PreciseTimer is required on Windows to actually tick faster than the
        # ~15.6 ms default timer granularity - without it, sub-16 ms intervals
        # get rounded back up and the preview is capped near 60 fps.
        self._timer.setTimerType(Qt.PreciseTimer)
        self._apply_timer_interval()
        self._timer.timeout.connect(self._on_tick)

        self._sprites_small, self._sprites_big = self._load_sprites()

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
        hz = max(60.0, min(hz, 144.0))
        # Floor (not round) the interval so we tick at least as fast as the
        # refresh - round() would give 17 ms at 60 Hz (~59 fps), just under
        # the target; int() gives 16 ms (~62 fps).
        self._timer.setInterval(max(1, int(1000.0 / hz)))

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

    def _recent_hit(self, now: float):
        """直近に判定線を通過した音符の (経過秒, 文字, コンボ番号) を返す。
        判定エフェクト(しぶき・「良」・コンボ演出)はこれだけから描ける。
        まだ1つも叩いていなければ None。"""
        i = bisect.bisect_right(self._note_times, now) - 1
        if i < 0:
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

    def _load_sprites(self):
        """Reuses the same notes.png sheet as the image-export feature, if
        the user has one configured, so the preview matches the exported
        chart image's note art instead of maintaining a second asset."""
        small, big = {}, {}
        try:
            from PIL import Image
            from neotja.tja_image_export import load_sprites
            pil_sprites = load_sprites(settings_mod.notes_png_path())
        except Exception:
            return small, big
        for c in ("1", "2"):
            if c in pil_sprites:
                d = self.NOTE_R_SMALL * 2
                small[c] = _pil_to_qpixmap(pil_sprites[c].resize((d, d), Image.Resampling.LANCZOS))
        for c in ("3", "4"):
            if c in pil_sprites:
                d = self.NOTE_R_BIG * 2
                big[c] = _pil_to_qpixmap(pil_sprites[c].resize((d, d), Image.Resampling.LANCZOS))
        return small, big

    def set_preview_data(self, data: dict):
        """`data` is the dict returned by TJACourseAnalyzer.build_preview_timeline:
        notes/rolls/balloons/kusudamas/gogo_regions/bar_times/bpm_changes/
        measure_changes/scroll_changes/course_key/course_label/course_color/
        level/available_courses."""
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
        self._course_key = data.get("course_key")
        self._course_label = data.get("course_label") or ""
        self._course_color = data.get("course_color") or COLORS["fg_bright"]
        self._course_level = data.get("level")
        self._available_courses = data.get("available_courses") or []
        self._has_branches = bool(data.get("has_branches"))
        self._branch_level = data.get("branch_level") or "M"
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
        # に消費されてしまうので、event() レベルで横取りして下部パネルのモード
        # 循環(情報→作譜→非表示)に割り当てる(フェーズ3)。Space/Q/PgUp/PgDn
        # とは非衝突。
        if e.type() == QEvent.KeyPress and e.key() == Qt.Key_Tab:
            if self._cycle_bottom_mode_cb:
                self._cycle_bottom_mode_cb()
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Space:
            self.toggle_play()
            return
        if key == Qt.Key_Q:
            self.return_to_anchor()
            return
        if key == Qt.Key_PageUp:
            self.seek_relative_measure(1)
            return
        if key == Qt.Key_PageDown:
            self.seek_relative_measure(-1)
            return
        # Home/End: 0小節目(曲頭)/最終小節へ一気に移動。PgUp/PgDn と同じく
        # 再生中は無効。
        if key == Qt.Key_Home:
            self.seek_to_first_measure()
            return
        if key == Qt.Key_End:
            self.seek_to_last_measure()
            return
        # 再生速度の微調整(作譜モード。0.05刻み、0.25〜1.0でクランプ)。実際の
        # レート適用はスライダー経由(set_speed_cb → スライダー値変更 → audio /
        # chart_preview 双方に反映)なので、ここでは目標倍率を算出して通知する。
        if key == Qt.Key_BracketLeft:
            self._adjust_speed(-0.05)
            return
        if key == Qt.Key_BracketRight:
            self._adjust_speed(0.05)
            return
        super().keyPressEvent(event)

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
        """Called when the game-preview window is (re)opened: park カレント/
        アンカー at the song head (0小節目/曲頭), rewind the audio to 0, and
        show the stopped state."""
        self._current_idx = 0
        self._anchor_idx = 0
        self._anchor_set = False
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
        """Space: start playing from カレント (stopped) / pause in place
        (playing) / snap to the head of the measure we're paused in, then
        resume (paused)."""
        if not self._nav_points:
            return
        if self._state == "playing":
            if self._pause_cb:
                self._pause_cb()
            return
        # stopped or paused -> (re)start playback.
        if self._state == "paused":
            # Snap カレント to the measure head at/just before where we
            # actually paused, so resuming always starts on a bar line.
            self._current_idx = self._nav_idx_at_or_before(self._pos_sec)
        # The first play of a session pins the anchor to カレント; later
        # resumes leave the anchor where it was (Q's target) untouched.
        if not self._anchor_set:
            self._anchor_idx = self._current_idx
            self._anchor_set = True
        target = self._nav_points[self._current_idx]
        self._animating = False
        self._pos_sec = target
        self._pos_wall = _time.monotonic()
        if self._seek_seconds_cb:
            self._seek_seconds_cb(target)
        if self._play_cb:
            self._play_cb()

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
        # PgUp/PgDn/wheel: step カレント one nav point, but only while
        # stopped/paused - navigation is deliberately inert during playback.
        self._seek_to_nav_idx(self._current_idx + direction)

    def seek_to_first_measure(self):
        """Home: jump カレント to 0小節目(曲頭)."""
        self._seek_to_nav_idx(0)

    def seek_to_last_measure(self):
        """End: jump カレント to the last measure."""
        self._seek_to_nav_idx(len(self._nav_points) - 1)

    def _seek_to_nav_idx(self, idx: int):
        if self._state == "playing":
            return
        if not self._nav_points or not self._seek_seconds_cb:
            return
        new_idx = max(0, min(idx, len(self._nav_points) - 1))
        self._current_idx = new_idx
        # Moving while paused re-pins the anchor (so Q comes back here); moving
        # while stopped leaves the not-yet-set anchor alone (see toggle_play).
        if self._state == "paused":
            self._anchor_idx = self._current_idx
            self._anchor_set = True
        target = self._nav_points[new_idx]
        self._start_scroll_anim(target)
        self._seek_seconds_cb(target)

    def set_offset(self, offset: float):
        self._offset = offset
        self._rebuild_nav_points()
        self.update()
        self._push_realtime_info()

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
        s = scroll if scroll is not None else 1.0
        return self.BASE_PIXELS_PER_BEAT * b / 60.0 * s

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
        self._balloon_draw = [
            (b[0], b[1], self._speed(b[2], b[3]), self._speed_at(b[1]))
            for b in self._balloons
        ]
        self._kusudama_draw = [
            (k[0], k[1], self._speed(k[2], k[3]), self._speed_at(k[1]))
            for k in self._kusudamas
        ]

    def _rebuild_min_vis_speed(self):
        """Slowest positive on-screen speed among the chart's notes and bars,
        capped at the 60-BPM reference. Drives the visible-time window so even
        very slow charts (low BPM or #SCROLL < 1) slide their notes in from the
        right edge instead of popping in mid-lane. Rebuilt once per chart edit.
        Non-positive speeds (#SCROLL <= 0 gimmicks) are ignored - those notes
        don't approach from the right edge, and the span cull handles them by
        pixel extent anyway."""
        ref = self.BASE_PIXELS_PER_BEAT * self.WINDOW_REF_BPM / 60.0
        slowest = ref
        for bpm, sc in zip(self._note_bpms, self._note_scrolls):
            s = self._speed(bpm, sc)
            if 0.0 < s < slowest:
                slowest = s
        for bpm, sc in zip(self._bar_bpms, self._bar_scrolls):
            s = self._speed(bpm, sc)
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

    LIVE_COUNT_HOLD_SEC = 1.0

    def _live_span_count(self, now):
        # self._live_spans: [(start, end, hits)] combining rolls+balloons -
        # returns the interpolated tap count for whichever span (if any)
        # currently contains `now`, so it counts up live while in progress
        # like the real game. Once a span ends, its final count keeps
        # showing for LIVE_COUNT_HOLD_SEC (so a quick glance can still catch
        # it) unless the next span starts sooner, in which case that one
        # takes over immediately.
        for start, end, hits in self._live_spans:
            if start <= now <= end:
                if end <= start:
                    return hits
                return int(hits * (now - start) / (end - start))
        best_end = None
        best_hits = None
        for start, end, hits in self._live_spans:
            if end <= now and (best_end is None or end > best_end):
                best_end, best_hits = end, hits
        if best_end is not None and now - best_end <= self.LIVE_COUNT_HOLD_SEC:
            return best_hits
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
            painter.drawPixmap(int(x - r), int(y - r), sprite)
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
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

        # Live roll/balloon tap count, upper-left of the judgment ring, in
        # the margin above the lane box - reuses _live_span_count as-is
        # (in-progress live number, held for LIVE_COUNT_HOLD_SEC after the
        # span ends), only appears while a roll/balloon is actually active
        # (or just finished). Must be drawn before the lane clip below is
        # applied - that clip is restricted to the band (y >= band_top), so
        # this top-margin text would be entirely clipped out otherwise.
        live_count = self._live_span_count(now)
        if live_count is not None:
            painter.setPen(self._color("roll"))
            painter.setFont(self._font(22, True))
            box_w = 160
            box_x = max(0, judge_x - box_w)
            painter.drawText(int(box_x), 2, box_w, band_top - 4, Qt.AlignRight | Qt.AlignVCenter, str(live_count))

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

        painter.fillRect(0, band_top, lane_w, band_h, self._color("surface"))

        # Real Taiko no Tatsujin flashes the whole play field, triggered the
        # instant a gogo region's start/end crosses the judgment line - not a
        # colored block that travels across the lane like a note would.
        if any(g0 <= now <= g1 for g0, g1 in self._gogo_regions):
            painter.fillRect(self.rect(), GOGO_TINT)

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
        painter.setPen(QPen(self._color("fg_dim"), 2))
        for i in range(lo_bar, hi_bar):
            if not self._bar_visible[i]:
                continue
            x = judge_x + (self._bar_times[i] - now) * self._speed(self._bar_bpms[i], self._bar_scrolls[i])
            painter.drawLine(int(x), band_top, int(x), band_bottom)

        # Anchor measure line (机能1): the bar the current Space-play started
        # from and where Q returns to - drawn over the plain bar lines in the
        # accent color so it's obvious at a glance where "home" is. Its
        # audio-time nav point is converted back to chart time to place it on
        # the same scrolling axis as everything else.
        if self._nav_points and self._anchor_idx < len(self._nav_points):
            a_chart = self._nav_points[self._anchor_idx] + self._offset
            if t_past <= a_chart <= t_future:
                a_bpm = self._bpm_changes[self._idx_at(self._bpm_times, a_chart)][1]
                a_scroll = self._scroll_changes[self._idx_at(self._scroll_times, a_chart)][1]
                ax = judge_x + (a_chart - now) * self._speed(a_bpm, a_scroll)
                painter.setPen(QPen(self._color("accent"), 3))
                painter.drawLine(int(ax), band_top, int(ax), band_bottom)

        # --- judgment ring (drawn BEFORE notes/rolls so they pass over it,
        # like notes crossing the drum face in the real game). ---
        judge_r = self.NOTE_R_BIG + 5
        judge_r_inner = self.NOTE_R_SMALL
        painter.setPen(QPen(self._color("fg_bright"), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(judge_x - judge_r), int(mid_y - judge_r), judge_r * 2, judge_r * 2)
        painter.drawEllipse(int(judge_x - judge_r_inner), int(mid_y - judge_r_inner), judge_r_inner * 2, judge_r_inner * 2)

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
        if gogo_env > 0.0:
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
        for b_start, b_end, sp0, sp1 in self._balloon_draw:
            x0 = judge_x + (b_start - now) * sp0
            x1 = judge_x + (b_end - now) * sp1
            if (x0 < -rs and x1 < -rs) or (x0 > lane_w + rs and x1 > lane_w + rs):
                continue
            draw_items.append((b_start, "balloon", (x0, x1)))
        for k_start, k_end, sp0, sp1 in self._kusudama_draw:
            x0 = judge_x + (k_start - now) * sp0
            x1 = judge_x + (k_end - now) * sp1
            if (x0 < -rs and x1 < -rs) or (x0 > lane_w + rs and x1 > lane_w + rs):
                continue
            draw_items.append((k_start, "kusudama", (x0, x1)))
        for i in range(lo, hi):
            draw_items.append((self._note_times[i], "note", i))
        # Latest first -> earliest drawn last -> earliest ends up on top.
        draw_items.sort(key=lambda d: d[0], reverse=True)
        for t0, kind, payload in draw_items:
            if kind == "roll":
                x0, x1, r, r_start, r_end = payload
                # Red while being hit (now inside the span), yellow otherwise.
                color = self._color("don") if r_start <= now <= r_end else self._color("roll")
                self._draw_roll_bar(painter, x0, x1, mid_y, r, color)
            elif kind == "balloon":
                x0, x1 = payload
                self._draw_roll_bar(painter, x0, x1, mid_y, rs, self._color("balloon"))
            elif kind == "kusudama":
                x0, x1 = payload
                self._draw_roll_bar(painter, x0, x1, mid_y, rs, self._color("kusudama"))
            else:  # note - approach, then fly off after crossing the line.
                i = payload
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
                    x = judge_x + (t - now) * self._speed(self._note_bpms[i], self._note_scrolls[i])
                    self._draw_note(painter, x, mid_y, r, c, big)

        # --- 叩いた瞬間の判定エフェクト (本家風) --------------------------
        # 直近ヒット音符からの経過時間だけで、判定枠から広がるしぶきと「良」の
        # ポップを描く。判定枠のすぐ上・レーンクリップ内なので他の演出の上に
        # 重なって出る。全ノーツ自動ヒットのため判定は常に「良」。
        hit = self._recent_hit(now)
        if hit is not None:
            h_elapsed, h_char, _h_combo = hit
            h_big = h_char in NOTE_BIG
            h_base = self.NOTE_R_BIG if h_big else self.NOTE_R_SMALL
            # ヒットしぶき: 判定枠から外へ広がって消える閃光リング + 内側フラッシュ。
            if 0.0 <= h_elapsed < self.HIT_BURST_DURATION:
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
            if 0.0 <= h_elapsed < self.JUDGE_POP_DURATION:
                jp = h_elapsed / self.JUDGE_POP_DURATION      # 0..1
                rise = 13.0 * (1.0 - (1.0 - jp) ** 2)         # ease-out で上昇(控えめ)
                painter.setClipRect(self.rect())
                painter.setOpacity(max(0.0, 1.0 - jp))
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
            painter.drawText(int(panel_x), band_top + 24, int(panel_w), num_h, Qt.AlignCenter, str(combo))

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
            painter.fillRect(0, band_bottom + 1, lane_w, footer_h - 1, self._color("surface"))
            painter.setPen(QPen(self._color("border"), 2))
            painter.drawLine(0, footer_bottom, lane_w, footer_bottom)
            painter.drawLine(lane_w, band_bottom, lane_w, footer_bottom)
        if self._se_text_enabled and self._note_se:
            painter.setClipRect(0, band_bottom + 1, lane_w, footer_h - 1)
            # 音符の色には合わせず、地色に対して読みやすい中立色(fg)で描く。
            # 判定枠に重なって叩いた瞬間(t <= now)にラベルは消す - 通り過ぎた
            # 音符には SE 文字を残さない。
            painter.setPen(self._color("fg"))
            fy = int(band_bottom + footer_h / 2.0)
            for i in range(hi - 1, lo - 1, -1):
                t = self._note_times[i]
                if t <= now:
                    continue
                label = self._note_se[i]
                if not label:
                    continue
                c = self._note_chars[i]
                big = c in NOTE_BIG
                size = self.SE_FONT_SIZE_BIG if big else self.SE_FONT_SIZE_SMALL
                st = self._se_static_text(label, size)
                x = judge_x + (t - now) * self._speed(self._note_bpms[i], self._note_scrolls[i])
                painter.setFont(self._font(size, True))
                sz = st.size()
                painter.drawStaticText(int(x - sz.width() / 2.0),
                                       int(fy - sz.height() / 2.0), st)
            painter.setClipRect(self.rect())

        # Current measure / total measures ("15/90"), below the judgment
        # ring in the bottom margin - same bisect-over-bar_times approach as
        # seek_relative_measure, so "current measure" always agrees with
        # what PgUp/PgDn/wheel navigation would jump from.
        if self._bar_times:
            measure_idx = min(len(self._bar_times), bisect.bisect_right(self._bar_times, now))
            measure_text = f"{measure_idx}/{len(self._bar_times)}"
        else:
            measure_text = "-"
        painter.setPen(self._color("fg_dim"))
        painter.setFont(self._font(12, True))
        box_w = 160
        painter.drawText(int(judge_x - box_w / 2), footer_bottom + 2, box_w, self.BOTTOM_MARGIN - 4,
                          Qt.AlignCenter, measure_text)

        # Focus indicator: matches the accent-colored :focus border the QSS
        # theme already gives the text editor (QPlainTextEdit:focus), so
        # whichever of the two panes has keyboard focus - and therefore
        # receives Space/Q/PgUp/PgDn - is visually obvious at a glance.
        if self.hasFocus():
            pen = QPen(self._color("accent"), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(1, 1, w - 2, h - 2)

        painter.end()
