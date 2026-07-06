import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDockWidget, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from neotja.audio_engine import AudioEngine, MetronomeEngine, WaveformDecodeWorker
from neotja.bpm_tap import BpmTapper
from neotja.waveform_widget import WaveformWidget


def parse_preview_headers(content: str) -> dict:
    title = ""
    wave = ""
    bpm = None
    offset = 0.0
    for l in content.split("\n"):
        if l.startswith("TITLE:"):
            title = l[6:].strip()
        elif l.startswith("WAVE:"):
            wave = l[5:].strip()
        elif l.startswith("BPM:"):
            try:
                bpm = float(l[4:].strip())
            except ValueError:
                pass
        elif l.startswith("OFFSET:"):
            try:
                offset = float(l[7:].strip())
            except ValueError:
                pass
    return {"title": title, "wave": wave, "bpm": bpm, "offset": offset}


def _fmt_time(ms: int) -> str:
    total = max(0, ms) // 1000
    m, s = divmod(total, 60)
    return f"{m}:{s:02}"


class PreviewDock(QDockWidget):
    """Dockable panel: play the song referenced by WAVE:, tap-measure BPM, and
    line up OFFSET against a waveform with a beat-grid overlay. Writes results
    back into the editor's BPM:/OFFSET: header lines via apply callbacks."""

    def __init__(self, apply_bpm_cb, apply_offset_cb, parent=None, seek_cursor_cb=None, volume_cb=None,
                 duration_ready_cb=None, expanded_changed_cb=None):
        super().__init__("音源プレビュー", parent)
        self.apply_bpm_cb = apply_bpm_cb
        self.apply_offset_cb = apply_offset_cb
        self.seek_cursor_cb = seek_cursor_cb
        self.volume_cb = volume_cb
        self.expanded_changed_cb = expanded_changed_cb
        self.duration_ready_cb = duration_ready_cb

        self.audio = AudioEngine(self)
        self.audio.positionChanged.connect(self._on_position_changed)
        self.audio.durationChanged.connect(self._on_duration_changed)
        self.audio.playingChanged.connect(self._on_playing_changed)
        self.audio.mediaStatusChanged.connect(self._on_media_status_changed)

        self.metronome = MetronomeEngine(self)
        self.audio.positionChanged.connect(self.metronome.on_position_changed)

        self.tapper = BpmTapper()

        self._wave_dir = None
        self._current_wave_path = None
        self._decode_worker = None
        self._editor_bpm = None
        self._editor_offset = 0.0
        self._editor_metronome_clicks = []
        self._duration_ms = 0

        self._build_ui()

    def _build_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        self._content_widget = content

        self.title_label = QLabel("(WAVEファイルなし)")
        layout.addWidget(self.title_label)

        self.waveform = WaveformWidget()
        self.waveform.seekRequested.connect(self._on_seek_requested)
        layout.addWidget(self.waveform, 1)

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(lambda ms: self.audio.seek(ms))
        layout.addWidget(self.seek_slider)

        transport_row = QHBoxLayout()
        self.btn_play = QPushButton("再生")
        self.btn_play.setObjectName("accentButton")
        self.btn_play.clicked.connect(self.audio.toggle_play_pause)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_seek_cursor = QPushButton("カーソル位置から再生")
        self.btn_seek_cursor.clicked.connect(self._on_seek_cursor)
        self.btn_metronome = QPushButton("メトロノーム")
        self.btn_metronome.setCheckable(True)
        self.btn_metronome.toggled.connect(self._on_metronome_toggled)
        self.time_label = QLabel("0:00 / 0:00")
        transport_row.addWidget(self.btn_play)
        transport_row.addWidget(self.btn_stop)
        transport_row.addWidget(self.btn_seek_cursor)
        transport_row.addWidget(self.btn_metronome)
        transport_row.addWidget(self.time_label)
        transport_row.addStretch()
        layout.addLayout(transport_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("音量:"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_row.addWidget(self.volume_slider)
        self.lbl_volume = QLabel("")
        volume_row.addWidget(self.lbl_volume)
        volume_row.addStretch()
        layout.addLayout(volume_row)

        bpm_row = QHBoxLayout()
        bpm_row.addWidget(QLabel("BPM:"))
        self.lbl_editor_bpm = QLabel("-")
        bpm_row.addWidget(self.lbl_editor_bpm)
        self.btn_tap = QPushButton("タップ")
        self.btn_tap.clicked.connect(self._on_tap)
        bpm_row.addWidget(self.btn_tap)
        self.lbl_tap_bpm = QLabel("--")
        bpm_row.addWidget(self.lbl_tap_bpm)
        self.btn_apply_bpm = QPushButton("BPMに反映")
        self.btn_apply_bpm.clicked.connect(self._on_apply_bpm)
        bpm_row.addWidget(self.btn_apply_bpm)
        bpm_row.addStretch()
        layout.addLayout(bpm_row)

        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("OFFSET:"))
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(-60.0, 60.0)
        self.spin_offset.setDecimals(3)
        self.spin_offset.setSingleStep(0.001)
        self.spin_offset.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin_offset.valueChanged.connect(self._on_offset_value_changed)
        # OFFSET convention: audio_time = chart_time - OFFSET, so increasing
        # OFFSET shifts the beat grid/notes earlier (left) and decreasing it
        # shifts them later (right). Placing +/- this way means pressing the
        # left-side button moves things left and the right-side button moves
        # things right, matching that spatial expectation.
        for label, delta in (("+0.1", 0.1), ("+0.01", 0.01), ("+0.001", 0.001)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, d=delta: self.spin_offset.setValue(self.spin_offset.value() + d))
            offset_row.addWidget(btn)
        offset_row.addWidget(self.spin_offset)
        for label, delta in (("-0.001", -0.001), ("-0.01", -0.01), ("-0.1", -0.1)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, d=delta: self.spin_offset.setValue(self.spin_offset.value() + d))
            offset_row.addWidget(btn)
        self.btn_apply_offset = QPushButton("OFFSETに反映")
        self.btn_apply_offset.clicked.connect(self._on_apply_offset)
        offset_row.addWidget(self.btn_apply_offset)
        offset_row.addStretch()
        layout.addLayout(offset_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.setWidget(content)

    def set_expanded(self, expanded: bool):
        self._content_widget.setVisible(expanded)

    def is_expanded(self) -> bool:
        return self._content_widget.isVisible()

    def expand(self):
        self.set_expanded(True)
        if self.expanded_changed_cb:
            self.expanded_changed_cb(True)

    # ------------------------------------------------------------------
    # Sync from editor content
    # ------------------------------------------------------------------
    def refresh_from_content(self, content: str, current_file, metronome_clicks=None):
        headers = parse_preview_headers(content)
        self._editor_bpm = headers["bpm"]
        self._editor_offset = headers["offset"]
        self._editor_metronome_clicks = metronome_clicks or []
        self.lbl_editor_bpm.setText(f"{headers['bpm']:g}" if headers["bpm"] else "-")
        self.title_label.setText(headers["title"] or "(無題)")

        self.waveform.set_beat_grid(headers["bpm"], self.spin_offset.value(), self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())

        wave = headers["wave"]
        if not current_file or not wave:
            self._current_wave_path = None
            self.status_label.setText("先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            self.btn_play.setEnabled(False)
            return

        wave_path = os.path.join(os.path.dirname(current_file), wave)
        if wave_path == self._current_wave_path:
            return  # same song already loaded; don't reset in-progress OFFSET tweaks

        self._current_wave_path = wave_path
        self.spin_offset.blockSignals(True)
        self.spin_offset.setValue(headers["offset"])
        self.spin_offset.blockSignals(False)
        self.waveform.set_beat_grid(headers["bpm"], headers["offset"], self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, headers["offset"])

        if not os.path.exists(wave_path):
            self.status_label.setText(f"音源ファイルが見つかりません: {wave}")
            self.btn_play.setEnabled(False)
            return

        self.status_label.setText("音源を読み込み中...")
        self.btn_play.setEnabled(False)
        self.audio.load(wave_path)
        self._start_waveform_decode(wave_path)

    def _start_waveform_decode(self, wave_path):
        worker = WaveformDecodeWorker(wave_path)
        worker.path = wave_path
        worker.decoded.connect(lambda peaks, dur, p=wave_path: self._on_decoded(p, peaks, dur))
        worker.failed.connect(lambda msg, p=wave_path: self._on_decode_failed(p, msg))
        self._decode_worker = worker
        worker.start()

    def _on_decoded(self, path, peaks, duration):
        if path != self._current_wave_path:
            return
        self.waveform.set_peaks(peaks, duration)
        self.status_label.setText("")

    def _on_decode_failed(self, path, msg):
        if path != self._current_wave_path:
            return
        self.status_label.setText(f"波形の読み込みに失敗しました: {msg}")

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _on_position_changed(self, ms):
        self.waveform.set_position(ms / 1000.0)
        self.time_label.setText(f"{_fmt_time(ms)} / {_fmt_time(self._duration_ms)}")
        if not self.seek_slider.isSliderDown():
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(ms)
            self.seek_slider.blockSignals(False)

    def _on_duration_changed(self, ms):
        self._duration_ms = ms
        self.seek_slider.setRange(0, max(0, ms))
        if self.duration_ready_cb:
            self.duration_ready_cb()

    def duration_seconds(self) -> float:
        return self._duration_ms / 1000.0

    def _on_playing_changed(self, playing):
        self.btn_play.setText("一時停止" if playing else "再生")

    def _on_media_status_changed(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        Status = QMediaPlayer.MediaStatus
        if status in (Status.LoadingMedia, Status.NoMedia):
            self.btn_play.setEnabled(False)
            self.status_label.setText("音源を読み込み中...")
        elif status == Status.InvalidMedia:
            self.btn_play.setEnabled(False)
            self.status_label.setText("音源を再生できません(未対応の形式など)。")
        elif status in (Status.LoadedMedia, Status.BufferedMedia, Status.EndOfMedia):
            self.btn_play.setEnabled(True)
            if self.status_label.text() == "音源を読み込み中...":
                self.status_label.setText("")

    def _on_stop(self):
        self.audio.stop()
        self.audio.seek(0)

    def _on_seek_requested(self, seconds):
        self.audio.seek(int(seconds * 1000))

    def seek_to_seconds(self, seconds: float):
        self.audio.seek(max(0, int(seconds * 1000)))
        self.audio.play()

    def _on_seek_cursor(self):
        if self.seek_cursor_cb is None:
            return
        seconds = self.seek_cursor_cb()
        if seconds is None:
            self.status_label.setText("カーソルが譜面データ(#START〜#END)の中にありません。")
            return
        self.seek_to_seconds(seconds)

    def _on_metronome_toggled(self, checked):
        self.metronome.set_enabled(checked)
        self.btn_metronome.setObjectName("accentButton" if checked else "")
        self.btn_metronome.style().unpolish(self.btn_metronome)
        self.btn_metronome.style().polish(self.btn_metronome)

    def set_metronome_clicks(self, clicks):
        self._editor_metronome_clicks = clicks or []
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())
        self.waveform.set_beat_grid(self._editor_bpm, self.spin_offset.value(), self._editor_metronome_clicks)

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------
    def set_volume(self, volume: float):
        """Sets the initial volume (0.0-1.0) without triggering the save
        callback, e.g. when restoring the value saved in settings.json."""
        self.audio.set_volume(volume)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(round(volume * 100))
        self.volume_slider.blockSignals(False)
        self.lbl_volume.setText(f"{round(volume * 100)}%")

    def _on_volume_changed(self, value):
        volume = value / 100.0
        self.audio.set_volume(volume)
        self.lbl_volume.setText(f"{value}%")
        if self.volume_cb:
            self.volume_cb(volume)

    # ------------------------------------------------------------------
    # BPM tap
    # ------------------------------------------------------------------
    def _on_tap(self):
        bpm = self.tapper.tap()
        self.lbl_tap_bpm.setText(f"{bpm:.1f}" if bpm else "--")

    def _on_apply_bpm(self):
        bpm_str = self.lbl_tap_bpm.text()
        if bpm_str and bpm_str != "--":
            self.apply_bpm_cb(bpm_str)

    # ------------------------------------------------------------------
    # OFFSET adjust
    # ------------------------------------------------------------------
    def _on_offset_value_changed(self, value):
        self.waveform.set_beat_grid(self._editor_bpm, value, self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, value)

    def _on_apply_offset(self):
        self.apply_offset_cb(f"{self.spin_offset.value():.3f}")
