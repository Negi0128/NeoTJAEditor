import bisect
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from neotja.theme import COLORS


class WaveformWidget(QWidget):
    """Draws a waveform envelope with a playhead and a BPM/OFFSET-derived
    beat grid overlay, for visually lining up OFFSET against the audio."""

    seekRequested = Signal(float)  # seconds

    MIN_ZOOM = 1.0
    MAX_ZOOM = 200.0

    def __init__(self, parent=None, toggle_play_cb=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._toggle_play_cb = toggle_play_cb

        self.peaks: list = []
        self.duration = 0.0
        self.bpm = None
        self.offset = 0.0
        self._click_audio_times = None  # sorted [(audio_time, is_measure_start), ...] or None
        self.position_sec = 0.0
        self.zoom = 1.0
        self.view_start = 0.0
        self._dragging = False
        self._last_repaint = 0.0

    # ------------------------------------------------------------------
    # Data in
    # ------------------------------------------------------------------
    def set_peaks(self, peaks: list, duration: float):
        self.peaks = peaks
        self.duration = max(0.0, duration)
        self.view_start = 0.0
        self.zoom = 1.0
        self.update()

    def set_beat_grid(self, bpm, offset: float, clicks=None):
        """`clicks`, when given (non-empty), is [(chart_time, is_measure_start), ...]
        as returned by TJACourseAnalyzer.build_metronome_clicks - drawn as-is
        so the grid tracks #MEASURE/#BPMCHANGE instead of assuming a single
        constant tempo for the whole song. Falls back to a plain bpm/offset
        grid when there's no chart data yet (e.g. a brand new file)."""
        self.bpm = bpm
        self.offset = offset
        if clicks:
            # OFFSET convention: chart_time = audio_time + OFFSET.
            self._click_audio_times = sorted((t - offset, is_measure) for t, is_measure in clicks)
        else:
            self._click_audio_times = None
        self.update()

    def set_position(self, seconds: float):
        self.position_sec = seconds
        span = self._visible_span()
        if seconds < self.view_start or seconds > self.view_start + span:
            self.view_start = max(0.0, seconds - span * 0.1)

        # QMediaPlayer can emit positionChanged far more often than a screen
        # refreshes; a full waveform+grid repaint on every tick pegs the GUI
        # thread. 30fps is visually indistinguishable for a playhead line.
        now = time.monotonic()
        if now - self._last_repaint < (1 / 30):
            return
        self._last_repaint = now
        self.update()

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------
    def _visible_span(self) -> float:
        if self.duration <= 0:
            return 1.0
        return self.duration / max(self.zoom, 0.0001)

    def _sec_to_x(self, sec: float) -> int:
        span = self._visible_span()
        if span <= 0:
            return 0
        return int((sec - self.view_start) / span * self.width())

    def _x_to_sec(self, x: float) -> float:
        span = self._visible_span()
        w = max(1, self.width())
        return self.view_start + (x / w) * span

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        old_span = self._visible_span()
        mouse_sec = self._x_to_sec(event.position().x())
        self.zoom = max(self.MIN_ZOOM, min(self.zoom * factor, self.MAX_ZOOM))
        new_span = self._visible_span()
        self.view_start = mouse_sec - (mouse_sec - self.view_start) * (new_span / old_span if old_span else 1)
        self.view_start = max(0.0, min(self.view_start, max(0.0, self.duration - new_span)))
        self.update()

    def mousePressEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.seekRequested.emit(max(0.0, self._x_to_sec(event.position().x())))

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.seekRequested.emit(max(0.0, self._x_to_sec(event.position().x())))

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            if self._toggle_play_cb:
                self._toggle_play_cb()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        mid = h / 2
        painter.fillRect(self.rect(), QColor(COLORS["bg2"]))

        if self.peaks and self.duration > 0:
            n = len(self.peaks)
            col_dur = self.duration / n
            painter.setPen(QColor(COLORS["accent"]))
            for x in range(w):
                sec = self._x_to_sec(x)
                idx = int(sec / col_dur) if col_dur > 0 else -1
                if 0 <= idx < n:
                    mn, mx = self.peaks[idx]
                    y1 = mid - mx * mid * 0.9
                    y2 = mid - mn * mid * 0.9
                    painter.drawLine(x, int(y1), x, int(y2))

        if self.duration > 0 and self._click_audio_times:
            visible_start = self.view_start
            visible_end = self.view_start + self._visible_span()
            times = [t for t, _ in self._click_audio_times]
            lo = bisect.bisect_left(times, visible_start)
            hi = bisect.bisect_right(times, visible_end)
            for t, is_measure in self._click_audio_times[max(0, lo - 1):hi + 1]:
                if t < 0:
                    continue
                x = self._sec_to_x(t)
                painter.setPen(QColor(COLORS["checkpoint"] if is_measure else COLORS["border"]))
                painter.drawLine(x, 0, x, h)
        elif self.bpm and self.bpm > 0 and self.duration > 0:
            beat_interval = 60.0 / self.bpm
            visible_start = self.view_start
            visible_end = self.view_start + self._visible_span()
            n = int((visible_start + self.offset) / beat_interval) - 1
            while True:
                # OFFSET convention: chart_time = audio_time + OFFSET, so beat 0
                # (measure 0 / first beat) sits at audio_time = -OFFSET.
                t = -self.offset + n * beat_interval
                if t > visible_end:
                    break
                if t >= visible_start - beat_interval and t >= 0:
                    x = self._sec_to_x(t)
                    is_measure = (n % 4 == 0)
                    painter.setPen(QColor(COLORS["checkpoint"] if is_measure else COLORS["border"]))
                    painter.drawLine(x, 0, x, h)
                n += 1

        painter.setPen(QColor(COLORS["err"]))
        x = self._sec_to_x(self.position_sec)
        painter.drawLine(x, 0, x, h)
