import bisect
import time

import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
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
        # 作譜モード用: 波形の下の帯に描く譜面データ。
        # _notes_raw は [(chart_time, char)]、_note_audio は OFFSET 適用後の
        # sorted [(audio_time, char)]。未設定(None)なら何も描かない。
        self._notes_raw = None
        self._note_audio = None
        # 描画ループ用の前計算(_apply_offset_local で構築)。
        self._note_time_list = None
        self._measure_time_list = None
        # 音符の丸を毎フレーム drawEllipse(アンチエイリアス+ペン)で描くと、
        # 1フレーム50個×120fps で描画時間の3割を占めていた。色/半径ごとに
        # 1回だけ描いた QPixmap を貼るだけにする(レーン側と同じ手法)。
        self._note_pix_cache = {}
        # 連打/風船/くす玉のスパン。raw は [(start_chart, end_chart, kind)]
        # kind は 'roll'(小)/'roll_big'(大)/'balloon'/'kusudama'。audio は
        # OFFSET 適用後の [(start_audio, end_audio, kind)]。
        self._spans_raw = None
        self._span_audio = None
        # 作譜モード用: 命令注釈。_cmd_raw=[(chart_time, text, color)]、
        # _gogo_raw=[(start_chart, end_chart)]。audio 版は OFFSET 適用後。
        self._cmd_raw = None
        self._cmd_audio = None
        self._gogo_raw = None
        self._gogo_audio = None
        # チェックポイント(音源時刻の列)。作譜モードの命令レーンに CHECK POINT
        # として表示する。chart_preview から set_checkpoints で届く。
        self._checkpoint_audio = None
        self.position_sec = 0.0
        self.zoom = 1.0
        self.view_start = 0.0
        self._dragging = False
        self._last_repaint = 0.0
        # 作譜モードの波形用: 固定幅の窓を再生位置に追従スクロールさせる。
        # None なら従来の「曲全体をズーム」表示。float(秒)なら常にその秒数ぶん
        # だけを表示し、再生位置が窓内の FOLLOW_FRAC の位置に来るよう view_start
        # を動かす(スクロールする楽譜のような見え方)。ホイールで窓幅を変える。
        self._follow_window = None

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

    def set_notes(self, notes):
        """作譜モードの波形に重ねる音符列を設定する。`notes` は
        build_preview_timeline() の "notes"（[(chart_time, char, bpm, scroll,
        se)]）と同じ形。None/空で消える。OFFSET は既存の offset を使って
        _apply_offset_local で audio 時刻に変換する。"""
        self._notes_raw = [(n[0], n[1]) for n in notes] if notes else None
        self._apply_offset_local(self.offset)

    def set_spans(self, rolls, balloons, kusudamas):
        """作譜モードの波形に描く連打/風船/くす玉のスパンを設定する。各引数は
        build_preview_timeline() の "rolls"([(s,e,char,bpm,scroll,hits)])/
        "balloons"/"kusudamas"([(s,e,bpm,scroll,hits)])と同じ形。"""
        raw = []
        for r in (rolls or []):
            raw.append((r[0], r[1], "roll_big" if len(r) > 2 and r[2] == "6" else "roll"))
        for b in (balloons or []):
            raw.append((b[0], b[1], "balloon"))
        for k in (kusudamas or []):
            raw.append((k[0], k[1], "kusudama"))
        self._spans_raw = raw or None
        self._apply_offset_local(self.offset)

    def set_checkpoints(self, times):
        """作譜モードの命令レーンに表示するチェックポイント(音源時刻の列)。"""
        self._checkpoint_audio = sorted(float(t) for t in (times or [])) or None
        self.update()

    def set_commands(self, bpm_changes, scroll_changes, measure_changes, gogo_regions):
        """作譜モードの波形に描く命令注釈を設定する。各変化点(先頭=基準値は
        除く)を色つきラベルに、GOGO区間を帯にする。引数は build_preview_timeline
        の bpm_changes[(t,bpm)]/scroll_changes[(t,scroll)]/measure_changes[
        (t,num,den)]/gogo_regions[(s,e)] と同じ形。"""
        raw = []
        for t, bpm in (bpm_changes or [])[1:]:
            raw.append((t, f"BPM{bpm:g}", "#3aa0ff"))
        for t, sc in (scroll_changes or [])[1:]:
            raw.append((t, f"HS{sc:g}", "#ff6b6b"))
        for t, num, den in (measure_changes or [])[1:]:
            raw.append((t, f"{int(num)}/{int(den)}", "#e0c060"))
        self._cmd_raw = raw or None
        self._gogo_raw = list(gogo_regions or []) or None
        self._apply_offset_local(self.offset)

    def _apply_offset_local(self, offset: float):
        """OFFSET だけを差し替えてグリッドを引き直す(ヘッダには書かない)。
        OFFSET convention: chart_time = audio_time + OFFSET."""
        self.offset = offset
        if self._clicks_raw:
            self._click_audio_times = sorted(
                (t - offset, is_measure) for t, is_measure in self._clicks_raw)
        else:
            self._click_audio_times = None
        if self._notes_raw:
            self._note_audio = sorted((t - offset, c) for t, c in self._notes_raw)
        else:
            self._note_audio = None
        # 描画ループ用の前計算(作譜モードは120fpsで再描画されるので、毎フレーム
        # リスト内包で作り直すと数千件の割当が毎秒十数万回になる)。
        #  - _note_time_list: 音符時刻だけの列(bisect 用)
        #  - _measure_time_list: 小節線の時刻だけの列(白い小節線の bisect 用)
        self._note_time_list = [t for t, _c in self._note_audio] if self._note_audio else None
        if self._click_audio_times:
            self._measure_time_list = [t for t, is_m in self._click_audio_times if is_m]
        else:
            self._measure_time_list = None
        if self._spans_raw:
            self._span_audio = sorted((s - offset, e - offset, k) for s, e, k in self._spans_raw)
        else:
            self._span_audio = None
        if self._cmd_raw:
            self._cmd_audio = sorted((t - offset, txt, col) for t, txt, col in self._cmd_raw)
        else:
            self._cmd_audio = None
        if self._gogo_raw:
            self._gogo_audio = sorted((s - offset, e - offset) for s, e in self._gogo_raw)
        else:
            self._gogo_audio = None
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

    FOLLOW_FRAC = 0.3  # 追従時、再生位置を窓の左から何割の位置に置くか

    def set_follow_window(self, seconds):
        """作譜モードの波形を「固定幅の窓 + 再生位置追従」表示にする。
        seconds に正の値を渡すとその秒数ぶんだけを表示して再生位置に追従、
        None/0 で従来のズーム表示に戻す。"""
        self._follow_window = float(seconds) if seconds and seconds > 0 else None
        self.update()

    def set_position(self, seconds: float):
        self.position_sec = seconds
        span = self._visible_span()
        if self._follow_window:
            # 再生位置を窓内の一定割合の位置に保つようスクロール(端はクランプ)。
            vs = seconds - span * self.FOLLOW_FRAC
            self.view_start = max(0.0, min(vs, max(0.0, self.duration - span)))
        elif seconds < self.view_start or seconds > self.view_start + span:
            self.view_start = max(0.0, seconds - span * 0.1)

        # QMediaPlayer can emit positionChanged far more often than a screen
        # refreshes; a full waveform+grid repaint on every tick pegs the GUI
        # thread. 30fps is visually indistinguishable for a playhead line.
        now = time.monotonic()
        if now - self._last_repaint < (1 / 30):
            return
        self._last_repaint = now
        self.update()

    def set_position_smooth(self, seconds: float):
        """set_position の 30fps 間引きなし版。作譜モードの波形をレーンの
        120fps クロック(chart_preview の毎フレーム外挿)で駆動して滑らかに
        追従スクロールさせるための入口。非表示のときは update() が no-op に
        なるので、隠れているモードでは実質コストゼロ。"""
        self.position_sec = seconds
        span = self._visible_span()
        if self._follow_window:
            vs = seconds - span * self.FOLLOW_FRAC
            self.view_start = max(0.0, min(vs, max(0.0, self.duration - span)))
        elif seconds < self.view_start or seconds > self.view_start + span:
            self.view_start = max(0.0, seconds - span * 0.1)
        self.update()

    def refresh_theme(self):
        self._theme_gen = -1
        self.update()

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------
    def _visible_span(self) -> float:
        if self._follow_window:
            # 固定幅の窓。曲がそれより短ければ曲全体。
            if self.duration > 0:
                return min(self._follow_window, self.duration)
            return self._follow_window
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
    # ホイール1目盛りで動かす量(表示幅に対する割合)。
    SCROLL_FRAC = 0.15

    def wheelEvent(self, event):
        """ホイール = 移動、Alt+ホイール = 拡大縮小。

        以前はホイールがそのまま拡大縮小だったが、波形は「見たい所へ動かす」
        操作のほうが圧倒的に多いので、素のホイールを移動に割り当てる。"""
        up = event.angleDelta().y() > 0
        if not (event.modifiers() & Qt.AltModifier):
            span = self._visible_span()
            step = span * self.SCROLL_FRAC * (-1 if up else 1)
            if self._follow_window:
                # 追従モードの view_start は再生位置から決まるので、動かすのは
                # 再生位置そのもの(= シーク)。
                self.seekRequested.emit(
                    max(0.0, min(self.position_sec + step,
                                 self.duration if self.duration > 0 else self.position_sec + step)))
            else:
                self.view_start = max(0.0, min(self.view_start + step,
                                               max(0.0, self.duration - span)))
                self.update()
            event.accept()
            return

        factor = 1.25 if up else 1 / 1.25
        if self._follow_window:
            # 追従モードでは窓幅そのものを増減(上スクロールで拡大表示=窓を狭く)。
            self._follow_window = max(1.0, min(self._follow_window / factor, 60.0))
            self.set_position(self.position_sec)  # 追従位置を取り直して即反映
            self.update()
            event.accept()
            return
        old_span = self._visible_span()
        mouse_sec = self._x_to_sec(event.position().x())
        self.zoom = max(self.MIN_ZOOM, min(self.zoom * factor, self.MAX_ZOOM))
        new_span = self._visible_span()
        self.view_start = mouse_sec - (mouse_sec - self.view_start) * (new_span / old_span if old_span else 1)
        self.view_start = max(0.0, min(self.view_start, max(0.0, self.duration - new_span)))
        self.update()
        event.accept()

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

    NOTE_STRIP_H = 40  # 音符を波形の下に置く帯(譜面)の高さ
    # 命令ラベルを譜面の下に置く帯の高さ。BPM/SCROLL/拍子が同時刻付近に並ぶと
    # 最大3段に積むうえ、最下段に CHECK POINT ラベルも出すので、それらが
    # 見切れないだけの高さを確保する。
    CMD_STRIP_H = 56

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(self.rect(), QColor(self._pal["bg2"]))

        t0 = self.view_start
        t1 = t0 + self._visible_span()

        # 上から順に「波形 / 譜面(音符) / 命令」の3段。譜面/命令帯は該当データが
        # あるときだけ確保する(ドック側の波形は音符を渡さないので従来どおり)。
        note_strip = self.NOTE_STRIP_H if (self._note_audio or self._span_audio) else 0
        cmd_strip = self.CMD_STRIP_H if (self._cmd_audio or self._checkpoint_audio) else 0
        wh = h - note_strip - cmd_strip
        note_top = wh
        note_bottom = wh + note_strip

        mips = self.mips
        stereo = bool(mips and mips.n_channels >= 2 and self.stereo_view)
        if mips and not mips.is_empty() and w > 0:
            if stereo:
                # 2レーンは同じ高さにしておく(バッファを使い回すため)。
                lane_h = wh // 2
                self._draw_lane(painter, 0, 0, lane_h, t0, t1, w, "L")
                self._draw_lane(painter, 1, wh - lane_h, lane_h, t0, t1, w, "R")
                painter.setPen(self._pen("border"))
                painter.drawLine(0, lane_h, w, lane_h)
            else:
                self._draw_lane(painter, WaveformMips.MIX, 0, wh, t0, t1, w, None)

        # GOGO は波形域の上に薄く重ねる(音符帯側にも下で別途重ねる)。
        self._fill_gogo(painter, 0, wh, t0, t1)
        self._draw_grid(painter, w, wh, t0, t1)

        if note_strip:
            # 譜面帯: 背景 → GOGO帯 → 白い小節線 → 音符/スパン。
            painter.fillRect(0, note_top, w, note_strip, QColor(self._pal["bg"]))
            painter.setPen(self._pen("border"))
            painter.drawLine(0, note_top, w, note_top)
            self._fill_gogo(painter, note_top, note_strip, t0, t1)
            self._draw_measure_lines(painter, note_top, note_strip, t0, t1)
            self._draw_notes(painter, w, t0, t1, note_top + note_strip // 2)

        if cmd_strip:
            # 命令帯: 譜面のさらに下。ラベル + その時刻へ伸びる縦線。
            painter.fillRect(0, note_bottom, w, cmd_strip, QColor(self._pal["bg2"]))
            painter.setPen(self._pen("border"))
            painter.drawLine(0, note_bottom, w, note_bottom)
            self._draw_checkpoints(painter, note_top, note_bottom, cmd_strip, t0, t1)
            self._draw_command_labels(painter, w, note_top, note_bottom, cmd_strip, t0, t1)

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

    def _note_pixmap(self, fill: QColor, r: int) -> QPixmap:
        """半径 r・色 fill の音符の丸(クリーム色のフチ付き)を1回だけ描いて
        キャッシュする。2倍で描いて縮小するのでフチが滑らか。"""
        key = (fill.rgba(), r)
        pix = self._note_pix_cache.get(key)
        if pix is None:
            ss = 2
            d = r * 2 * ss
            img = QImage(d + 2 * ss, d + 2 * ss, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor("#fbf3e0"), 2 * ss))
            p.setBrush(QBrush(fill))
            p.drawEllipse(ss, ss, d, d)
            p.end()
            side = r * 2 + 2
            pix = QPixmap.fromImage(
                img.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._note_pix_cache[key] = pix
        return pix

    def _draw_notes(self, painter, w: int, t0: float, t1: float, cy: int):
        """作譜モード: 波形の下の帯に譜面を描く。連打/風船/くす玉のスパン(バー)
        を先に、その上に音符(ドン=赤 / カ=青、大音符は大きめ)を円で描く。
        cy は帯の中心 y。"""
        ring = QColor("#fbf3e0")
        painter.setRenderHint(QPainter.Antialiasing, True)

        # --- 連打/風船/くす玉のスパン(バー + 頭) ---
        sa = self._span_audio
        if sa:
            span_col = {"roll": QColor("#fcdb38"), "roll_big": QColor("#fcdb38"),
                        "balloon": QColor("#ff9f43"), "kusudama": QColor("#ff9f43")}
            for s, e, kind in sa:
                if e < t0 or s > t1:
                    continue
                xs = self._sec_to_x(s)
                xe = self._sec_to_x(e)
                col = span_col.get(kind, QColor("#fcdb38"))
                th = 20 if kind == "roll_big" else 14
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(col))
                painter.drawRoundedRect(min(xs, xe), cy - th // 2, max(2, abs(xe - xs)), th, th // 2, th // 2)
                # 頭(始点)を丸で強調。風船/くす玉はそれと分かる色に。
                hr = 10 if kind in ("balloon", "kusudama") else (11 if kind == "roll_big" else 9)
                painter.setPen(QPen(ring, 2))
                painter.setBrush(QBrush(col))
                painter.drawEllipse(QPoint(xs, cy), hr, hr)

        # --- 音符(1〜4) ---
        na = self._note_audio
        if na:
            # 時刻列は前計算済み(_apply_offset_local)。以前はここで毎フレーム
            # リスト内包で作り直しており、120fps×数千音符ぶんの割当になっていた。
            times = self._note_time_list or []
            lo = max(0, bisect.bisect_left(times, t0) - 1)
            hi = bisect.bisect_right(times, t1) + 1
            don = QColor(self._pal["don"])
            ka = QColor(self._pal["ka"])
            # 事前描画したピクスマップを貼るだけ(アンチエイリアスの
            # drawEllipse+ペンを毎フレーム音符数ぶん実行しない)。
            spr = {
                (False, True): self._note_pixmap(don, 8),
                (False, False): self._note_pixmap(ka, 8),
                (True, True): self._note_pixmap(don, 11),
                (True, False): self._note_pixmap(ka, 11),
            }
            for t, c in na[lo:hi]:
                x = self._sec_to_x(t)
                pix = spr[(c in ("3", "4"), c in ("1", "3"))]
                painter.drawPixmap(x - pix.width() // 2, cy - pix.height() // 2, pix)
        painter.setBrush(Qt.NoBrush)

    def _fill_gogo(self, painter, y0: int, height: int, t0: float, t1: float):
        """GOGO区間を [y0, y0+height] に薄い赤で重ねる。波形域・譜面帯の両方に
        別々に重ねられるよう、対象の y 範囲を引数で受ける。"""
        ga = self._gogo_audio
        if not ga or height <= 0:
            return
        gogo_col = QColor(255, 120, 120, 46)
        for s, e in ga:
            if e < t0 or s > t1:
                continue
            xs = self._sec_to_x(s)
            xe = self._sec_to_x(e)
            painter.fillRect(xs, y0, max(1, xe - xs), height, gogo_col)

    def _draw_measure_lines(self, painter, y0: int, height: int, t0: float, t1: float):
        """譜面帯 [y0, y0+height] に白い小節線を引く(音符を小節ごとに区切る)。
        小節位置はグリッド用クリック(_click_audio_times)の is_measure から。"""
        # 小節線の時刻だけを前計算した列を bisect で切り出す(以前は全クリックを
        # 毎フレーム線形走査していた。長い曲では数千件×120fps になる)。
        mt = self._measure_time_list
        if not mt:
            return
        lo = bisect.bisect_left(mt, t0)
        hi = bisect.bisect_right(mt, t1)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        for i in range(lo, hi):
            x = self._sec_to_x(mt[i])
            painter.drawLine(x, y0, x, y0 + height)

    def _draw_command_labels(self, painter, w: int, note_top: int, note_bottom: int,
                             cmd_h: int, t0: float, t1: float):
        """命令(BPM/SCROLL/拍子の変化)を譜面の下の帯にラベルで描く。ラベルは
        重なると段違いにずらし(最大3段)、その時刻へ向けて譜面帯を貫く細い縦線を
        引いて、どの音符位置の命令かが分かるようにする。"""
        ca = self._cmd_audio
        if not ca:
            return
        painter.setFont(QFont(self.font().family(), 7, QFont.Bold))
        fm = painter.fontMetrics()
        line_h = fm.height() + 1
        # 段(階段状の重なり回避)は「その命令の左側にある近い命令」だけで決まる。
        # 以前は可視分だけを毎フレーム並べ直して段を計算していたため、命令が左端で
        # 消えるたびに段が組み替わってチラついていた。ここでは可視範囲より少し前
        # (2秒)からの命令を通して段を計算し、可視分だけ描く。スクロールしても
        # 段が変わらず、位置もそのまま流れる。
        times = [c[0] for c in ca]
        lo = max(0, bisect.bisect_left(times, t0 - 2.0))
        hi = bisect.bisect_right(times, t1)
        last_end = [-1e9, -1e9, -1e9]     # 3段までの右端 x
        for i in range(lo, hi):
            t, txt, col = ca[i]
            x = self._sec_to_x(t)
            tw = fm.horizontalAdvance(txt)
            level = 0
            while level < 2 and x < last_end[level] + 4:
                level += 1
            last_end[level] = x + 4 + tw
            if t < t0 or t > t1:
                continue   # 段の計算だけして描画はしない(左外の命令で段を安定化)
            ly = note_bottom + 2 + level * line_h
            qcol = QColor(col)
            painter.setPen(QPen(qcol, 1))
            painter.drawLine(x, note_top, x, ly)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRect(x + 1, ly, tw + 4, fm.height())
            painter.setPen(qcol)
            painter.drawText(x + 3, ly + fm.ascent(), txt)

    def _draw_checkpoints(self, painter, note_top: int, note_bottom: int,
                          cmd_h: int, t0: float, t1: float):
        """作譜モードの命令レーンにチェックポイントを黄色で表示する。譜面帯〜命令帯
        を貫く黄色の縦線 + 「CHECK POINT」ラベル。"""
        cp = self._checkpoint_audio
        if not cp:
            return
        yellow = QColor("#ffd166")
        painter.setFont(QFont(self.font().family(), 7, QFont.Bold))
        fm = painter.fontMetrics()
        label = "CHECK POINT"
        tw = fm.horizontalAdvance(label)
        ly = note_bottom + cmd_h - fm.height() - 1   # 命令帯の下段に置く
        for t in cp:
            if t < t0 or t > t1:
                continue
            x = self._sec_to_x(t)
            painter.setPen(QPen(yellow, 2))
            painter.drawLine(x, note_top, x, note_bottom + cmd_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 180))
            painter.drawRect(x + 1, ly, tw + 4, fm.height())
            painter.setPen(yellow)
            painter.drawText(x + 3, ly + fm.ascent(), label)

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
