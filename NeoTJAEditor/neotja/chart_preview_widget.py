import bisect
import time as _time

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from neotja import settings as settings_mod
from neotja.theme import COLORS

NOTE_COLOR = {"1": "don", "2": "ka", "3": "don", "4": "ka"}
NOTE_BIG = {"3", "4"}
GOGO_TINT = QColor(255, 90, 90, 55)
DEFAULT_BPM = 120.0


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
    BOTTOM_MARGIN = 24
    WIDGET_HEIGHT = TOP_MARGIN + LANE_HEIGHT + BOTTOM_MARGIN
    RESYNC_THRESHOLD_SEC = 0.05
    HIT_ANIM_DURATION = 0.25   # seconds a note spends flying off after crossing the judgment line
    HIT_FLY_DX = 90.0
    HIT_FLY_DY = 70.0
    PANEL_INSET = 14           # left margin so the combo/course block reads as a floating card, not edge-to-edge
    PANEL_GAP = 24             # gap between the panel's right edge and the judgment ring

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
        self._rolls = []
        self._balloons = []
        self._live_spans = []
        self._bar_times = []
        self._bar_bpms = []
        self._bar_scrolls = []
        self._gogo_regions = []
        self._bpm_changes = [(0.0, DEFAULT_BPM)]
        self._measure_changes = [(0.0, 4, 4)]
        self._scroll_changes = [(0.0, 1.0)]
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
        self._qcolor_cache = {k: QColor(v) for k, v in COLORS.items()}
        self._font_cache = {}

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
        c = self._qcolor_cache.get(key)
        if c is None:
            c = QColor(COLORS.get(key, "#ffffff"))
            self._qcolor_cache[key] = c
        return c

    def _font(self, size: int, bold: bool = False) -> QFont:
        cache_key = (size, bold)
        f = self._font_cache.get(cache_key)
        if f is None:
            f = QFont(self.font().family(), size, QFont.Bold if bold else QFont.Normal)
            self._font_cache[cache_key] = f
        return f

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
        notes/rolls/balloons/gogo_regions/bar_times/bpm_changes/measure_changes/
        scroll_changes/course_key/course_label/course_color/level/available_courses."""
        notes = sorted(data.get("notes") or [])
        self._note_times = [t for t, _, _, _ in notes]
        self._note_chars = [c for _, c, _, _ in notes]
        self._note_bpms = [bpm for _, _, bpm, _ in notes]
        self._note_scrolls = [sc for _, _, _, sc in notes]
        self._rolls = sorted(data.get("rolls") or [], key=lambda r: r[0])
        self._balloons = sorted(data.get("balloons") or [], key=lambda b: b[0])
        # (start, end, hits) view combining both, used only for the live/
        # held combo-count readout - independent of the rolls/balloons lists
        # above since those keep their full per-type tuples for rendering.
        self._live_spans = sorted(
            [(r[0], r[1], r[-1]) for r in self._rolls] + [(b[0], b[1], b[-1]) for b in self._balloons],
            key=lambda s: s[0],
        )
        self._gogo_regions = sorted(data.get("gogo_regions") or [])
        bars = sorted(data.get("bar_times") or [])
        self._bar_times = [t for t, _, _ in bars]
        self._bar_bpms = [bpm for _, bpm, _ in bars]
        self._bar_scrolls = [sc for _, _, sc in bars]
        self._bpm_changes = sorted(data.get("bpm_changes") or [(0.0, DEFAULT_BPM)])
        self._measure_changes = sorted(data.get("measure_changes") or [(0.0, 4, 4)])
        self._scroll_changes = sorted(data.get("scroll_changes") or [(0.0, 1.0)])
        self._bpm_times = [c[0] for c in self._bpm_changes]
        self._measure_times = [c[0] for c in self._measure_changes]
        self._scroll_times = [c[0] for c in self._scroll_changes]
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

    def _adjust_speed(self, delta: float):
        rate = round(max(0.25, min(1.0, self._playback_rate + delta)), 2)
        if self._set_speed_cb:
            self._set_speed_cb(rate)
        else:
            # スライダー未配線(単体使用)でも動くようフォールバック。
            self.set_playback_rate(rate)

    def set_playback_rate(self, rate: float):
        """再生速度倍率(0.25〜1.0)を設定。再生中の時間外挿に使う。"""
        self._playback_rate = max(0.25, min(1.0, rate))

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
        if self._state == "playing":
            return
        if not self._nav_points or not self._seek_seconds_cb:
            return
        new_idx = max(0, min(self._current_idx + direction, len(self._nav_points) - 1))
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

    def _visible_window(self, now, w, judge_x):
        # Higher BPM -> faster on-screen speed -> a smaller time window covers
        # the same pixel range. Sizing the window at the slowest plausible
        # tempo guarantees nothing visible gets excluded; faster songs just
        # end up with a few harmless extra candidates bisected in.
        speed = self.BASE_PIXELS_PER_BEAT * self.WINDOW_REF_BPM / 60.0
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
        for spans in (self._rolls, self._balloons):
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
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(int(x0 - r), int(cy - r), d, d)
        painter.drawRect(int(x0), int(cy - r), max(1, int(x1 - x0)), d)
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

        # カレント/アンカー readout in the top margin (机能1), upper-left so it
        # doesn't collide with the roll/balloon count drawn to the right. Nav
        # index doubles as the measure number: index 0 is the song head
        # (0小節目), index N the N-th bar line. Also drawn before the lane clip
        # below, which would otherwise cut off this top-margin text.
        state_label = {"stopped": "停止", "playing": "再生", "paused": "一時停止"}.get(self._state, "")
        painter.setPen(self._color("fg_dim"))
        painter.setFont(self._font(11, True))
        painter.drawText(
            self.PANEL_INSET, 2, max(0, judge_x - self.PANEL_INSET - 4), band_top - 4,
            Qt.AlignLeft | Qt.AlignVCenter,
            f"現在: {self._current_idx}小節 / アンカー: {self._anchor_idx}小節  [{state_label}]",
        )

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

        # --- bar (measure) lines ---
        lo_bar = bisect.bisect_left(self._bar_times, t_past)
        hi_bar = bisect.bisect_right(self._bar_times, t_future)
        painter.setPen(QPen(self._color("fg_dim"), 2))
        for i in range(lo_bar, hi_bar):
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

        # --- rolls (drawn under notes, like the real game). Sized to match
        # an actual note's diameter (small roll = normal note, big roll =
        # big note) rather than an arbitrary multiplier, and the start point
        # is re-outlined like a normal note (white border) on top of the bar
        # so it's obvious at a glance where the roll begins. ---
        for r_start, r_end, r_char, r_bpm, r_scroll, _r_hits in self._rolls:
            if r_end < t_past or r_start > t_future:
                continue
            speed = self._speed(r_bpm, r_scroll)
            x0 = judge_x + (r_start - now) * speed
            x1 = judge_x + (r_end - now) * speed
            r = self.NOTE_R_BIG if r_char == "6" else self.NOTE_R_SMALL
            # Turns red while actually being hit (now inside the span),
            # yellow otherwise - a clear "this one's live" cue distinct from
            # rolls still approaching or already finished.
            color = self._color("don") if r_start <= now <= r_end else self._color("roll")
            self._draw_roll_bar(painter, x0, x1, mid_y, r, color)

        # --- balloons: a color variant of the small roll (same size, same
        # bar shape), just in the balloon color so it's still distinct. ---
        for b_start, b_end, b_bpm, b_scroll, b_hits in self._balloons:
            if b_end < t_past or b_start > t_future:
                continue
            speed = self._speed(b_bpm, b_scroll)
            x0 = judge_x + (b_start - now) * speed
            x1 = judge_x + (b_end - now) * speed
            self._draw_roll_bar(painter, x0, x1, mid_y, self.NOTE_R_SMALL, self._color("balloon"))

        judge_r = self.NOTE_R_BIG + 5
        judge_r_inner = self.NOTE_R_SMALL
        painter.setPen(QPen(self._color("fg_bright"), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(judge_x - judge_r), int(mid_y - judge_r), judge_r * 2, judge_r * 2)
        painter.drawEllipse(int(judge_x - judge_r_inner), int(mid_y - judge_r_inner), judge_r_inner * 2, judge_r_inner * 2)

        # --- notes: approach normally, then fly off up-and-right (Taiko no
        # Tatsujin style) for HIT_ANIM_DURATION once they cross the judgment
        # line, instead of continuing to scroll past indefinitely. Past
        # notes older than that are simply not drawn anymore, so the note
        # window's lower bound is the animation duration rather than a wide
        # scroll-based lookback.
        note_t_past = now - self.HIT_ANIM_DURATION
        lo = bisect.bisect_left(self._note_times, note_t_past)
        hi = bisect.bisect_right(self._note_times, t_future)
        for i in range(hi - 1, lo - 1, -1):
            t = self._note_times[i]
            c = self._note_chars[i]
            big = c in NOTE_BIG
            r = self.NOTE_R_BIG if big else self.NOTE_R_SMALL
            if t <= now:
                progress = min(1.0, (now - t) / self.HIT_ANIM_DURATION)
                x = judge_x + self.HIT_FLY_DX * progress
                y = mid_y - self.HIT_FLY_DY * progress
                painter.setOpacity(max(0.0, 1.0 - progress))
                self._draw_note(painter, x, y, max(1, int(r * (1.0 - 0.25 * progress))), c, big)
                painter.setOpacity(1.0)
            else:
                x = judge_x + (t - now) * self._speed(self._note_bpms[i], self._note_scrolls[i])
                self._draw_note(painter, x, mid_y, r, c, big)

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
        painter.setPen(self._color("fg_bright"))
        painter.setFont(self._font(22, True))
        painter.drawText(int(panel_x), band_top + 24, int(panel_w), band_h - 28, Qt.AlignCenter, str(combo))

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
        painter.drawText(int(judge_x - box_w / 2), band_bottom + 2, box_w, self.BOTTOM_MARGIN - 4,
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
