import bisect
import time

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from neotja import theme
from neotja.theme import COLORS
from neotja.waveform_data import WaveformMips


class WaveformWidget(QWidget):
    """Draws a waveform envelope with a playhead and a BPM/OFFSET-derived
    beat grid overlay, for visually lining up OFFSET against the audio.

    波形は WaveformMips(ミップチェイン)から、その時点の表示スパンと
    ウィジェット幅に合わせたレベルを選んで描く。つまりズームするほど実際に
    細かい波形が出る(以前の固定 2000 列を引き伸ばす方式とは違う)。

    OFFSET調整モード(btn_offset / set_offset_mode)では、ドラッグでビート
    グリッドを波形の上で左右に動かして頭出しを合わせられる。離した時点の値が
    確定値として offsetCommitted で出る(呼び出し側が既存の OFFSET スピンボックス
    へ流す)。Escape でキャンセル、矢印キーで 1ms / Shift+矢印で 10ms の微調整。"""

    seekRequested = Signal(float)     # seconds
    offsetPreview = Signal(float)     # ドラッグ中の暫定値(未確定)
    offsetCommitted = Signal(float)   # 確定値
    stereoToggled = Signal(bool)
    offsetModeToggled = Signal(bool)

    MIN_ZOOM = 1.0
    MAX_ZOOM = 1000.0

    # 矢印キーの微調整量(秒)。Shift 併用で 10 倍。
    NUDGE_STEP = 0.001
    NUDGE_STEP_COARSE = 0.010

    def __init__(self, parent=None, toggle_play_cb=None, force_dark=False):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._toggle_play_cb = toggle_play_cb
        # ゲーム窓(えぬいーさん次郎)の波形は常にダーク基調。force_dark の
        # ときは live COLORS ではなく固定のダークパレットを色源にする。
        self._force_dark = force_dark
        self._pal = theme.THEMES["dark"] if force_dark else COLORS

        self.mips = None
        self.duration = 0.0
        self.bpm = None
        self.offset = 0.0
        self._clicks_raw = None          # [(chart_time, is_measure_start), ...] or None
        self._click_audio_times = None   # sorted [(audio_time, is_measure_start), ...] or None
        self.position_sec = 0.0
        self.zoom = 1.0
        self.view_start = 0.0
        self._dragging = False
        self._last_repaint = 0.0

        self.stereo_view = True
        self.offset_mode = False
        # OFFSET ドラッグ中の状態: (start_x, base_offset) or None
        self._offset_drag = None
        self._readout = ""

        # テーマ変更で作り直すキャッシュ(theme.GENERATION と比較)。
        self._theme_gen = -1
        self._pens = {}
        self._lane_color = None
        # 波形本体を描くための numpy バッファ(下の _draw_lane 参照)。
        self._lane_buf = None
        self._lane_rows = None

        self.btn_stereo = QPushButton("ステレオ", self)
        self.btn_stereo.setCheckable(True)
        self.btn_stereo.setChecked(True)
        self.btn_stereo.setFocusPolicy(Qt.NoFocus)
        self.btn_stereo.setToolTip("L/R 個別表示と 合成(モノラル)表示を切り替えます。")
        self.btn_stereo.toggled.connect(self._on_stereo_button)

        self.btn_offset = QPushButton("OFFSET調整", self)
        self.btn_offset.setCheckable(True)
        self.btn_offset.setFocusPolicy(Qt.NoFocus)
        self.btn_offset.setToolTip(
            "ONの間、波形の上でビートグリッドを左右にドラッグして OFFSET を合わせます。\n"
            "Escape で取り消し、矢印キーで ±1ms(Shift で ±10ms)。")
        self.btn_offset.toggled.connect(self.set_offset_mode)

        self._place_buttons()

    # ------------------------------------------------------------------
    # Data in
    # ------------------------------------------------------------------
    def set_mips(self, mips: WaveformMips):
        """両方の波形インスタンス(ドック側とゲーム窓側)で同じ WaveformMips を
        共有する前提。ここでは参照を持つだけでコピーはしない。"""
        self.mips = mips
        self.duration = max(0.0, mips.duration if mips else 0.0)
        self.view_start = 0.0
        self.zoom = 1.0
        self._update_stereo_button_state()
        self.update()

    def set_beat_grid(self, bpm, offset: float, clicks=None):
        """`clicks`, when given (non-empty), is [(chart_time, is_measure_start), ...]
        as returned by TJACourseAnalyzer.build_metronome_clicks - drawn as-is
        so the grid tracks #MEASURE/#BPMCHANGE instead of assuming a single
        constant tempo for the whole song. Falls back to a plain bpm/offset
        grid when there's no chart data yet (e.g. a brand new file)."""
        self.bpm = bpm
        self._clicks_raw = list(clicks) if clicks else None
        self._apply_offset_local(offset)

    def _apply_offset_local(self, offset: float):
        """OFFSET だけを差し替えてグリッドを引き直す(ヘッダには書かない)。
        OFFSET convention: chart_time = audio_time + OFFSET."""
        self.offset = offset
        if self._clicks_raw:
            self._click_audio_times = sorted(
                (t - offset, is_measure) for t, is_measure in self._clicks_raw)
        else:
            self._click_audio_times = None
        self.update()

    def set_stereo_view(self, stereo: bool):
        stereo = bool(stereo)
        if self.btn_stereo.isChecked() != stereo:
            self.btn_stereo.blockSignals(True)
            self.btn_stereo.setChecked(stereo)
            self.btn_stereo.blockSignals(False)
        self.stereo_view = stereo
        self.btn_stereo.setText("ステレオ" if stereo else "合成")
        self._place_buttons()   # ラベル幅が変わるので再フィット
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

    def refresh_theme(self):
        self._theme_gen = -1
        self.update()

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------
    def _visible_span(self) -> float:
        if self.duration <= 0:
            return 1.0
        return self.duration / max(self.zoom, 0.0001)

    def _seconds_per_pixel(self) -> float:
        return self._visible_span() / max(1, self.width())

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
    # Layout
    # ------------------------------------------------------------------
    def _place_buttons(self):
        # Size from the font's own metrics rather than hardcoded pixels: the
        # labels are Japanese and swap between ステレオ/合成, so a fixed width
        # clips the text on some fonts/DPI settings.
        for btn in (self.btn_stereo, self.btn_offset):
            hint = btn.sizeHint()
            btn.resize(hint.width() + 8, max(22, hint.height()))
        w = self.width()
        self.btn_offset.move(w - self.btn_offset.width() - 6, 6)
        self.btn_stereo.move(w - self.btn_offset.width() - self.btn_stereo.width() - 12, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_buttons()

    def _update_stereo_button_state(self):
        stereo_source = bool(self.mips and self.mips.n_channels >= 2)
        self.btn_stereo.setEnabled(stereo_source)

    # ------------------------------------------------------------------
    # OFFSET adjust mode
    # ------------------------------------------------------------------
    def _on_stereo_button(self, checked: bool):
        self.set_stereo_view(checked)
        self.stereoToggled.emit(checked)

    def set_offset_mode(self, on: bool):
        on = bool(on)
        if self.btn_offset.isChecked() != on:
            self.btn_offset.blockSignals(True)
            self.btn_offset.setChecked(on)
            self.btn_offset.blockSignals(False)
        if self.offset_mode == on:
            return
        self.offset_mode = on
        if not on:
            self.cancel_offset_drag()
            self._readout = ""
        else:
            self._readout = f"OFFSET: {self.offset:+.3f}"
        self.setCursor(Qt.SizeHorCursor if on else Qt.ArrowCursor)
        self.offsetModeToggled.emit(on)
        self.update()

    # 以下3つはテストから直接呼べるよう、マウスイベントから分離してある。
    def begin_offset_drag(self, x: float):
        self._offset_drag = (float(x), self.offset)
        self._readout = f"OFFSET: {self.offset:+.3f} (Δ+0.000)"
        self.update()

    def update_offset_drag(self, x: float) -> float:
        if self._offset_drag is None:
            return self.offset
        start_x, base = self._offset_drag
        # OFFSET convention: audio_time = chart_time - OFFSET なので、グリッドを
        # 右へ動かす(+px)には OFFSET を減らす。ピクセル→秒は現在のズーム基準。
        delta = (float(x) - start_x) * self._seconds_per_pixel()
        value = base - delta
        self._apply_offset_local(value)
        self._readout = f"OFFSET: {value:+.3f} (Δ{value - base:+.3f})"
        self.offsetPreview.emit(value)
        self.update()
        return value

    def end_offset_drag(self) -> float:
        if self._offset_drag is None:
            return self.offset
        base = self._offset_drag[1]
        value = self.offset
        self._offset_drag = None
        self._readout = f"OFFSET: {value:+.3f} (Δ{value - base:+.3f})"
        self.offsetCommitted.emit(value)
        self.update()
        return value

    def cancel_offset_drag(self) -> bool:
        """Escape: 確定せずドラッグ開始時の OFFSET に戻す。"""
        if self._offset_drag is None:
            return False
        base = self._offset_drag[1]
        self._offset_drag = None
        self._apply_offset_local(base)
        self._readout = f"OFFSET: {base:+.3f} (取消)"
        self.offsetPreview.emit(base)
        self.update()
        return True

    def nudge_offset(self, delta: float) -> float:
        value = self.offset + delta
        self._apply_offset_local(value)
        self._readout = f"OFFSET: {value:+.3f} (Δ{delta:+.3f})"
        self.offsetCommitted.emit(value)
        self.update()
        return value

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
        if event.button() != Qt.LeftButton:
            return
        if self.offset_mode:
            self.begin_offset_drag(event.position().x())
            return
        self._dragging = True
        self.seekRequested.emit(max(0.0, self._x_to_sec(event.position().x())))

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if self._offset_drag is not None:
            self.update_offset_drag(event.position().x())
            return
        if self._dragging:
            self.seekRequested.emit(max(0.0, self._x_to_sec(event.position().x())))

    def mouseReleaseEvent(self, event):
        if self._offset_drag is not None:
            self.end_offset_drag()
        self._dragging = False

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape and self.cancel_offset_drag():
            return
        if self.offset_mode and key in (Qt.Key_Left, Qt.Key_Right):
            step = self.NUDGE_STEP_COARSE if (event.modifiers() & Qt.ShiftModifier) else self.NUDGE_STEP
            # 右矢印 = グリッドを右へ = OFFSET を減らす(ドックの +/- ボタンの
            # 並びと同じ空間的な対応)。
            self.nudge_offset(-step if key == Qt.Key_Right else step)
            return
        if key == Qt.Key_Space:
            if self._toggle_play_cb:
                self._toggle_play_cb()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def _check_theme(self):
        if self._theme_gen != theme.GENERATION:
            self._pens.clear()
            self._lane_color = None
            self._theme_gen = theme.GENERATION

    def _pen(self, key: str, width: int = 1) -> QPen:
        self._check_theme()
        cache_key = (key, width)
        pen = self._pens.get(cache_key)
        if pen is None:
            pen = QPen(QColor(self._pal[key]))
            pen.setWidth(width)
            self._pens[cache_key] = pen
        return pen

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(self.rect(), QColor(self._pal["bg2"]))

        t0 = self.view_start
        t1 = t0 + self._visible_span()

        mips = self.mips
        stereo = bool(mips and mips.n_channels >= 2 and self.stereo_view)
        if mips and not mips.is_empty() and w > 0:
            if stereo:
                # 2レーンは同じ高さにしておく(バッファを使い回すため)。
                lane_h = h // 2
                self._draw_lane(painter, 0, 0, lane_h, t0, t1, w, "L")
                self._draw_lane(painter, 1, h - lane_h, lane_h, t0, t1, w, "R")
                painter.setPen(self._pen("border"))
                painter.drawLine(0, lane_h, w, lane_h)
            else:
                self._draw_lane(painter, WaveformMips.MIX, 0, h, t0, t1, w, None)

        self._draw_grid(painter, w, h, t0, t1)

        painter.setPen(self._pen("err", 2 if self.offset_mode else 1))
        x = self._sec_to_x(self.position_sec)
        painter.drawLine(x, 0, x, h)

        if self.offset_mode:
            # モードが有効なのが一目で分かるよう、枠を強調色で囲って読み値を出す。
            painter.setPen(self._pen("accent2", 2))
            painter.drawRect(1, 1, w - 2, h - 2)
            if self._readout:
                painter.setPen(QColor(self._pal["bg"]))
                painter.fillRect(6, h - 24, 240, 18, QColor(self._pal["accent2"]))
                painter.drawText(10, h - 10, self._readout)

    def _lane_buffer(self, w: int, h: int):
        buf = self._lane_buf
        if buf is None or buf.shape != (h, w):
            buf = np.zeros((h, w), dtype=np.uint32)
            self._lane_buf = buf
            self._lane_rows = np.arange(h, dtype=np.float32).reshape(-1, 1)
        return buf, self._lane_rows

    def _draw_lane(self, painter, channel, y_top: int, lane_h: int,
                   t0: float, t1: float, w: int, label):
        """1レーン分の波形を描く。

        幅ぶんの列を1本ずつ drawLine したり 2*w 点のポリゴンを組んだりすると、
        QPointF/QLineF の生成コスト(1600px で 3ms超)が支配的になる。ここでは
        列エンベロープから塗りつぶしマスクを numpy で一気に作り、その ARGB
        バッファを QImage として1回 drawImage する(実測 1/4 の時間)。"""
        w = int(w)
        lane_h = int(lane_h)
        if w < 1 or lane_h < 2:
            return
        self._check_theme()
        if self._lane_color is None:
            self._lane_color = np.uint32(QColor(self._pal["accent"]).rgba())

        _mins, maxs = self.mips.peaks(channel, t0, t1, w)
        mid = (lane_h - 1) / 2.0
        half = mid * 0.9
        top = mid - maxs * half
        bottom = mid + maxs * half

        buf, rows = self._lane_buffer(w, lane_h)
        mask = (rows >= top) & (rows <= bottom)
        np.multiply(mask, self._lane_color, out=buf, dtype=np.uint32, casting="unsafe")
        # buf は self が保持しているので drawImage が終わるまで生きている。
        img = QImage(buf.data, w, lane_h, QImage.Format_ARGB32_Premultiplied)
        painter.drawImage(0, int(y_top), img)

        if label:
            painter.setPen(self._pen("fg_dim"))
            painter.drawText(6, int(y_top) + 14, label)

    def _draw_grid(self, painter, w: int, h: int, visible_start: float, visible_end: float):
        if self.duration <= 0:
            return
        measure_pen = self._pen("checkpoint", 2 if self.offset_mode else 1)
        beat_pen = self._pen("border", 2 if self.offset_mode else 1)
        if self._click_audio_times:
            times = [t for t, _ in self._click_audio_times]
            lo = bisect.bisect_left(times, visible_start)
            hi = bisect.bisect_right(times, visible_end)
            for t, is_measure in self._click_audio_times[max(0, lo - 1):hi + 1]:
                if t < 0:
                    continue
                x = self._sec_to_x(t)
                painter.setPen(measure_pen if is_measure else beat_pen)
                painter.drawLine(x, 0, x, h)
        elif self.bpm and self.bpm > 0:
            beat_interval = 60.0 / self.bpm
            n = int((visible_start + self.offset) / beat_interval) - 1
            while True:
                # OFFSET convention: chart_time = audio_time + OFFSET, so beat 0
                # (measure 0 / first beat) sits at audio_time = -OFFSET.
                t = -self.offset + n * beat_interval
                if t > visible_end:
                    break
                if t >= visible_start - beat_interval and t >= 0:
                    x = self._sec_to_x(t)
                    painter.setPen(measure_pen if (n % 4 == 0) else beat_pen)
                    painter.drawLine(x, 0, x, h)
                n += 1
