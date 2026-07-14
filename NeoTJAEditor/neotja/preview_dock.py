import math
import os

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDockWidget, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from neotja.audio_engine import AudioEngine, HitSoundEngine, MetronomeEngine, WaveformDecodeWorker
from neotja.bpm_tap import BpmTapper
from neotja.chart_preview_widget import ChartPreviewWidget
from neotja.theme import COLORS
from neotja.waveform_widget import WaveformWidget


class ChartInfoBar(QWidget):
    """Panel shown under the game-preview lane: transport buttons (mouse
    equivalents of the lane's Space/Q/PgUp/PgDn shortcuts), song title/
    subtitle, then stat cards - BPM/SCROLL/MEASURE (updated live off the
    playback position via set_realtime_info), roll/balloon totals plus a
    live cumulative tap count, and note count/don-ka ratio/total time.
    Cards mixing static (per-course, set once per edit) and live (per-frame)
    values keep the two halves of their text in separate labels rather than
    rebuilding one string from both, so per-frame updates don't need to know
    the static half."""

    BRANCH_LABELS = {"N": "普通", "E": "玄人", "M": "達人"}

    def __init__(self, parent=None, toggle_play_cb=None, return_anchor_cb=None,
                 seek_prev_cb=None, seek_next_cb=None, cycle_course_cb=None, cycle_branch_cb=None):
        super().__init__(parent)
        self.setFixedHeight(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        button_row = QHBoxLayout()
        btn_play = QPushButton("再生/一時停止 (Space)")
        # Q now returns to the アンカー (the measure the current play started
        # from), not the editor cursor - see ChartPreviewWidget.return_to_anchor.
        btn_cursor = QPushButton("アンカーへ (Q)")
        btn_prev = QPushButton("◀ 前の小節 (PgDn)")
        btn_next = QPushButton("次の小節 ▶ (PgUp)")
        self.btn_course = QPushButton("コース: -")
        self.btn_branch = QPushButton("分岐: -")
        self.btn_branch.setVisible(False)  # only shown for courses that actually have #BRANCHSTART
        if toggle_play_cb:
            btn_play.clicked.connect(toggle_play_cb)
        if return_anchor_cb:
            btn_cursor.clicked.connect(return_anchor_cb)
        if seek_prev_cb:
            btn_prev.clicked.connect(lambda: seek_prev_cb(-1))
        if seek_next_cb:
            btn_next.clicked.connect(lambda: seek_next_cb(1))
        if cycle_course_cb:
            self.btn_course.clicked.connect(cycle_course_cb)
        if cycle_branch_cb:
            self.btn_branch.clicked.connect(cycle_branch_cb)
        for btn in (btn_prev, btn_cursor, btn_play, self.btn_course, self.btn_branch, btn_next):
            # QPushButton normally takes keyboard focus on click, and a
            # focused QAbstractButton intercepts Space to click itself -
            # e.g. after clicking "next measure", Space would trigger that
            # button again instead of reaching ChartPreviewWidget's
            # keyPressEvent (play/pause). NoFocus keeps these mouse-only, so
            # keyboard focus - and Space/Q/PgUp/PgDn - always stays on the
            # lane.
            btn.setFocusPolicy(Qt.NoFocus)
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        self.lbl_title = QLabel("-")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        title_font = self.lbl_title.font()
        title_font.setBold(True)
        title_font.setPointSize(13)
        self.lbl_title.setFont(title_font)
        layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel("")
        self.lbl_subtitle.setAlignment(Qt.AlignCenter)
        self.lbl_subtitle.setStyleSheet(f"color: {COLORS['fg_dim']};")
        layout.addWidget(self.lbl_subtitle)

        # Cards are tinted by category so the panel reads at a glance instead
        # of everything looking the same: BPM/SCROLL/MEASURE in green (ok
        # color - distinct from the don/ka red/blue used below so tempo info
        # doesn't read as "note-related"), roll/balloon stats in roll-yellow,
        # time in neutral white, and note-count/men/fuchi in neutral/don-red/
        # ka-blue respectively (don=men, ka=fuchi - the same red/blue used
        # for don/ka notes in the lane itself).
        self.card_bpm, self.lbl_bpm = self._make_card("BPM", COLORS["ok"])
        self.card_scroll, self.lbl_scroll = self._make_card("SCROLL", COLORS["ok"])
        self.card_measure, self.lbl_measure = self._make_card("MEASURE", COLORS["ok"])
        layout.addLayout(self._row(self.card_bpm, self.card_scroll, self.card_measure))

        self.card_roll, self.lbl_roll = self._make_card("連打(風船個数)", COLORS["roll"])
        self.card_roll_est, self.lbl_roll_est = self._make_card("推定連打数(風船打数)", COLORS["roll"])
        self.card_time, self.lbl_time = self._make_card("総時間", COLORS["fg_bright"])
        layout.addLayout(self._row(self.card_roll, self.card_roll_est, self.card_time))

        self.card_notes, self.lbl_notes = self._make_card("ノーツ数", COLORS["fg_bright"])
        self.card_men, self.lbl_men = self._make_card("面", COLORS["don"])
        self.card_fuchi, self.lbl_fuchi = self._make_card("縁", COLORS["ka"])
        layout.addLayout(self._row(self.card_notes, self.card_men, self.card_fuchi))
        layout.addStretch()

    @staticmethod
    def _make_card(label_text: str, color: str = None):
        border_color = color or COLORS["border"]
        value_color = color or COLORS["fg_bright"]
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['surface']}; border: 1px solid {border_color};"
            f" border-radius: 6px; }}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        lbl_header = QLabel(label_text)
        lbl_header.setAlignment(Qt.AlignCenter)
        lbl_header.setStyleSheet(f"color: {COLORS['fg_dim']}; font-size: 10px; border: none;")
        lbl_value = QLabel("-")
        lbl_value.setAlignment(Qt.AlignCenter)
        lbl_value.setStyleSheet(f"border: none; color: {value_color};")
        value_font = lbl_value.font()
        value_font.setBold(True)
        value_font.setPointSize(18)
        lbl_value.setFont(value_font)
        v.addWidget(lbl_header)
        v.addWidget(lbl_value)
        return frame, lbl_value

    @staticmethod
    def _row(*cards):
        row = QHBoxLayout()
        for card in cards:
            row.addWidget(card, 1)
        return row

    def set_static_info(self, title: str, subtitle: str, course_stats: dict):
        self.lbl_title.setText(title or "(無題)")
        self.lbl_subtitle.setText(subtitle or "")
        self.lbl_subtitle.setVisible(bool(subtitle))
        if not course_stats:
            self.lbl_notes.setText("-")
            self.lbl_men.setText("-")
            self.lbl_fuchi.setText("-")
            self.lbl_time.setText("-")
            self.lbl_roll.setText("-")
            return
        don = course_stats.get("don_count", 0)
        ka = course_stats.get("ka_count", 0)
        total = don + ka
        if total > 0:
            self.lbl_men.setText(f"{don} ({don / total * 100:.0f}%)")
            self.lbl_fuchi.setText(f"{ka} ({ka / total * 100:.0f}%)")
        else:
            self.lbl_men.setText("-")
            self.lbl_fuchi.setText("-")
        self.lbl_notes.setText(str(course_stats.get("notes", 0)))
        self.lbl_time.setText(course_stats.get("time", "-"))

        rolls_info = course_stats.get("rolls_info") or []
        balloons_info = course_stats.get("balloons_info") or []
        roll_seconds = sum(r["duration"] for r in rolls_info)
        self.lbl_roll.setText(f"{roll_seconds:.2f}秒({len(balloons_info)}個)")

    def set_course_info(self, label: str, color: str, level):
        text = f"{label or '-'} ★{level}" if level is not None else (label or "-")
        self.btn_course.setText(f"コース: {text}")
        if color:
            self.btn_course.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_branch_info(self, level: str, has_branches: bool):
        self.btn_branch.setVisible(has_branches)
        if has_branches:
            self.btn_branch.setText(f"分岐: {self.BRANCH_LABELS.get(level, level)}")

    @staticmethod
    def _trunc(value: float, decimals: int) -> float:
        factor = 10 ** decimals
        return math.trunc(value * factor) / factor

    def set_realtime_info(self, bpm, scroll, num, den, cumulative_hits):
        # BPM: shown as specified, just truncated (not rounded) past 4
        # decimals, with no padding - "150" stays "150", not "150.0000".
        bpm_str = f"{self._trunc(bpm, 4):.4f}".rstrip("0").rstrip(".")
        self.lbl_bpm.setText(bpm_str or "0")
        # SCROLL: always exactly 3 decimals (truncated, not rounded) so it
        # reads as a fixed-width "1.000"-style value regardless of how much
        # precision the chart specifies.
        self.lbl_scroll.setText(f"{self._trunc(scroll, 3):.3f}")
        self.lbl_measure.setText(f"{num}/{den}")
        self.lbl_roll_est.setText(str(cumulative_hits))


class GamePreviewWindow(QWidget):
    """Standalone, non-modal window hosting the game-style chart preview.

    The preview's lane is a fixed pixel size (see ChartPreviewWidget) so it
    reads consistently regardless of tempo/song, which doesn't play well
    with being squeezed into a dock that shares width with the main editor -
    a dedicated window sidesteps that entirely. The window itself is a fixed
    size matching the lane (no resize handles), rather than free-floating
    dead space around a fixed-size lane looking unbalanced.

    Also auto-pauses playback when the window stops being the active one
    (alt-tab away, click back to the main editor, etc.) so the song doesn't
    keep playing - and hit sounds firing - in the background unattended."""

    closed = Signal()

    def __init__(self, chart_preview: ChartPreviewWidget, bottom_widget: QWidget, parent=None, pause_cb=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("えぬいーさん次郎")
        self._pause_cb = pause_cb
        chart_preview.setFixedSize(int(ChartPreviewWidget.LANE_WIDTH), ChartPreviewWidget.WIDGET_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(chart_preview)
        layout.addWidget(bottom_widget)
        # bottom_widget は3モードの QStackedWidget。ページごとに高さが違うと
        # モード切替のたびに窓がガタつくので、呼び出し側でスタックを最も高い
        # ページ高さに固定済み。その固定高さ(=minimumHeight)を使って窓サイズも
        # 一定に保つ。
        self.setFixedSize(
            int(ChartPreviewWidget.LANE_WIDTH),
            ChartPreviewWidget.WIDGET_HEIGHT + bottom_widget.minimumHeight(),
        )

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() and self._pause_cb:
            self._pause_cb()


def _roll_tick_notes(spans, bpm_index):
    """Expands roll/balloon spans - (start, end, ..., hits) tuples, as
    returned in build_preview_timeline()'s "rolls"/"balloons" - into evenly
    spaced virtual note events across the span, so HitSoundEngine's normal
    per-note schedule also produces the rapid drumroll sound during a roll/
    balloon instead of staying silent between its head and tail. Always don
    ("men") hits, not alternating don/ka - a real taiko roll is struck
    face-only regardless of hand. `bpm_index` differs between the two span
    shapes (rolls carry char before bpm, balloons don't), so the caller
    passes which column holds it."""
    ticks = []
    for span in spans:
        start, end, hits = span[0], span[1], span[-1]
        bpm = span[bpm_index]
        if hits <= 0:
            continue
        interval = (end - start) / hits
        for i in range(hits):
            ticks.append((start + i * interval, "1", bpm))
    return ticks


def parse_preview_headers(content: str) -> dict:
    title = ""
    subtitle = ""
    wave = ""
    bpm = None
    offset = 0.0
    for l in content.split("\n"):
        if l.startswith("TITLE:"):
            title = l[6:].strip()
        elif l.startswith("SUBTITLE:"):
            subtitle = l[9:].strip()
            # Convention: SUBTITLE always carries a leading "--" marker, with
            # the actual subtitle text (if any) right after it - not a
            # separator to strip only when the whole value is "--".
            if subtitle.startswith("--"):
                subtitle = subtitle[2:].strip()
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
    return {"title": title, "subtitle": subtitle, "wave": wave, "bpm": bpm, "offset": offset}


def _fmt_time(ms: int) -> str:
    total = max(0, ms) // 1000
    m, s = divmod(total, 60)
    return f"{m}:{s:02}"


class PreviewDock(QDockWidget):
    """Dockable panel: play the song referenced by WAVE:, tap-measure BPM, and
    line up OFFSET against a waveform with a beat-grid overlay. Writes the
    OFFSET result back into the editor's OFFSET: header line automatically as
    it's adjusted (see _on_offset_value_changed)."""

    def __init__(self, apply_offset_cb, parent=None, seek_cursor_cb=None, volume_cb=None,
                 duration_ready_cb=None, expanded_changed_cb=None, refresh_preview_cb=None,
                 course_select_cb=None, game_preview_changed_cb=None, branch_select_cb=None):
        super().__init__("音源プレビュー", parent)
        self.apply_offset_cb = apply_offset_cb
        self.seek_cursor_cb = seek_cursor_cb
        self.volume_cb = volume_cb
        self.expanded_changed_cb = expanded_changed_cb
        self.duration_ready_cb = duration_ready_cb
        self.refresh_preview_cb = refresh_preview_cb
        self.course_select_cb = course_select_cb
        self.branch_select_cb = branch_select_cb
        self.game_preview_changed_cb = game_preview_changed_cb

        self.audio = AudioEngine(self)
        self.audio.positionChanged.connect(self._on_position_changed)
        self.audio.durationChanged.connect(self._on_duration_changed)
        self.audio.playingChanged.connect(self._on_playing_changed)
        self.audio.mediaStatusChanged.connect(self._on_media_status_changed)

        self.metronome = MetronomeEngine(self)
        self.audio.positionChanged.connect(self.metronome.on_position_changed)

        # Not wired to audio.positionChanged: that signal fires irregularly
        # enough that hit-sound timing driven by it feels bursty/uneven.
        # Instead ChartPreviewWidget calls hit_sounds.check_and_play() from
        # its own smoothly-interpolated tick (see _build_ui below).
        self.hit_sounds = HitSoundEngine(self)

        self.tapper = BpmTapper()

        self._wave_dir = None
        self._current_wave_path = None
        self._decode_worker = None
        self._editor_bpm = None
        self._editor_offset = 0.0
        self._editor_subtitle = ""
        self._editor_metronome_clicks = []
        self._editor_notes = []
        self._duration_ms = 0

        self._build_ui()

    def _build_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        self._content_widget = content

        self.title_label = QLabel("(WAVEファイルなし)")
        layout.addWidget(self.title_label)

        self.waveform = WaveformWidget(toggle_play_cb=self.audio.toggle_play_pause)
        self.waveform.seekRequested.connect(self._on_seek_requested)
        layout.addWidget(self.waveform, 1)

        self.chart_preview = ChartPreviewWidget(
            course_select_cb=self.course_select_cb,
            seek_seconds_cb=lambda sec: self.audio.seek(max(0, int(sec * 1000))),
            # Explicit play/pause (not just toggle) so the widget's own player
            # model can drive precise start-from-anchor / pause-in-place
            # transitions (机能1).
            play_cb=self.audio.play,
            pause_cb=self.audio.pause,
            hit_sound_engine=self.hit_sounds,
            branch_select_cb=self.branch_select_cb,
            # フェーズ3: Tab で下部パネルのモード循環、[ ] で再生速度微調整。
            cycle_bottom_mode_cb=self.cycle_bottom_mode,
            set_speed_cb=self._on_speed_from_key,
        )
        # Info-bar transport buttons mirror the lane's Space/Q shortcuts, so
        # route them through the widget's player model rather than the raw
        # audio engine.
        self.info_bar = ChartInfoBar(
            toggle_play_cb=self.chart_preview.toggle_play,
            return_anchor_cb=self.chart_preview.return_to_anchor,
            seek_prev_cb=self.chart_preview.seek_relative_measure,
            seek_next_cb=self.chart_preview.seek_relative_measure,
            cycle_course_cb=self.chart_preview.cycle_course,
            cycle_branch_cb=self.chart_preview.cycle_branch,
        )
        self.chart_preview.set_info_update_cb(self.info_bar.set_realtime_info)

        # 下部パネルを3モードの QStackedWidget に(フェーズ3):
        #   index 0 = 情報モード(既存 ChartInfoBar)
        #   index 1 = 作譜モード(波形 + 再生速度スライダー)
        #   index 2 = 非表示モード(空白 = レーンのみ見える)
        self._sakufu_page = self._build_sakufu_page()
        self.bottom_stack = QStackedWidget()
        self.bottom_stack.addWidget(self.info_bar)      # 0 情報
        self.bottom_stack.addWidget(self._sakufu_page)  # 1 作譜
        self.bottom_stack.addWidget(QWidget())          # 2 非表示(空白)
        # ページ高さが異なるとモード切替で窓がガタつくので、最も高いページに
        # スタックの高さを固定する(info_bar は setFixedHeight(300) 済み)。
        bottom_h = max(self.info_bar.minimumHeight(), self._sakufu_page.sizeHint().height())
        self.bottom_stack.setFixedHeight(bottom_h)

        self.game_preview_window = GamePreviewWindow(
            self.chart_preview, self.bottom_stack, parent=self, pause_cb=self.audio.pause,
        )
        self.game_preview_window.closed.connect(self._on_game_preview_closed)

        # モード切替トグルボタン(キーと併用)。レーン右上隅に浮かせ、どのモード
        # でも常に見えるようにする。現在モード名を短く表示。フォーカスは奪わない
        # (Space/Tab/PgUp/PgDn はレーンに保持)。
        self._mode_names = ["情報", "作譜", "非表示"]
        self.mode_button = QPushButton(self._mode_names[0], self.chart_preview)
        self.mode_button.setFocusPolicy(Qt.NoFocus)
        self.mode_button.setToolTip("下部パネルの表示切替(Tab)")
        self.mode_button.resize(96, 26)
        self.mode_button.move(int(ChartPreviewWidget.LANE_WIDTH) - 96 - 8, 6)
        self.mode_button.clicked.connect(self.cycle_bottom_mode)

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
        self.btn_hit_sounds = QPushButton("打音")
        self.btn_hit_sounds.setCheckable(True)
        self.btn_hit_sounds.setChecked(True)
        self.btn_hit_sounds.setToolTip("ゲーム風プレビューでノーツが判定ラインに重なった瞬間にドン/カツの音を鳴らします。")
        self.btn_hit_sounds.toggled.connect(self._on_hit_sounds_toggled)
        self._on_hit_sounds_toggled(True)
        self.time_label = QLabel("0:00 / 0:00")
        transport_row.addWidget(self.btn_play)
        transport_row.addWidget(self.btn_stop)
        transport_row.addWidget(self.btn_seek_cursor)
        transport_row.addWidget(self.btn_metronome)
        transport_row.addWidget(self.btn_hit_sounds)
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
        offset_row.addStretch()
        layout.addLayout(offset_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.setWidget(content)

    def set_game_preview_visible(self, visible: bool):
        if visible:
            # Opening the window parks カレント/アンカー at the song head and
            # rewinds the audio to 0 (机能1), so it always starts from a known
            # stopped state regardless of where the audio was left.
            self.chart_preview.reset_to_start()
            self.game_preview_window.show()
            self.game_preview_window.raise_()
            self.game_preview_window.activateWindow()
            # activateWindow() only makes the window active at the OS level;
            # it doesn't hand keyboard focus to a specific child, so without
            # this, Space/Q/PgUp/PgDn silently do nothing until the user
            # clicks inside the lane once.
            self.chart_preview.setFocus(Qt.OtherFocusReason)
        else:
            self.game_preview_window.hide()

    def is_game_preview_visible(self) -> bool:
        return self.game_preview_window.isVisible()

    def _on_game_preview_closed(self):
        # The window's own close (X) button hides it via closeEvent; let the
        # status-bar toggle button know so its checked state stays in sync.
        if self.game_preview_changed_cb:
            self.game_preview_changed_cb(False)

    # ------------------------------------------------------------------
    # Bottom-panel mode switching + playback speed (フェーズ3)
    # ------------------------------------------------------------------
    def _build_sakufu_page(self) -> QWidget:
        """作譜モードのページ: 波形表示(ドック側 self.waveform と同じ配線の
        もう1つの WaveformWidget)と再生速度スライダー(0.25〜1.0)。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        # ゲーム窓の作譜ページ用の波形。ドックの self.waveform と同様に audio の
        # 再生位置へ同期し、クリック/ドラッグで seek する(seekRequested→seek)。
        self.game_waveform = WaveformWidget(toggle_play_cb=self.audio.toggle_play_pause)
        self.game_waveform.seekRequested.connect(self._on_seek_requested)
        self.game_waveform.setFixedHeight(150)
        v.addWidget(self.game_waveform)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("再生速度:"))
        self.speed_slider = QSlider(Qt.Horizontal)
        # 25〜100 の整数レンジ → /100 で 0.25〜1.00 倍。
        self.speed_slider.setRange(25, 100)
        self.speed_slider.setValue(100)
        # Space/Tab/[ ] をレーンに残すためスライダーはフォーカスを取らない。
        self.speed_slider.setFocusPolicy(Qt.NoFocus)
        self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        speed_row.addWidget(self.speed_slider, 1)
        self.lbl_speed = QLabel("×1.00")
        speed_row.addWidget(self.lbl_speed)
        v.addLayout(speed_row)
        v.addStretch()
        return page

    def cycle_bottom_mode(self):
        """情報(0)→作譜(1)→非表示(2)→情報… と循環。Tab キー(chart_preview)と
        モードトグルボタンの両方から呼ばれる。"""
        idx = (self.bottom_stack.currentIndex() + 1) % 3
        self.bottom_stack.setCurrentIndex(idx)
        self.mode_button.setText(self._mode_names[idx])

    def _on_speed_slider_changed(self, value: int):
        # スライダーが速度の単一ソース。ここから audio と chart_preview の両方の
        # レートを更新する。
        rate = value / 100.0
        self.lbl_speed.setText(f"×{rate:.2f}")
        self.audio.set_playback_rate(rate)
        self.chart_preview.set_playback_rate(rate)

    def _on_speed_from_key(self, rate: float):
        # chart_preview の [ ] キーから来る目標倍率。スライダー値を動かすと
        # valueChanged 経由で audio/chart_preview に反映される(スライダーと同期)。
        self.speed_slider.setValue(int(round(rate * 100)))

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
    def refresh_from_content(self, content: str, current_file, metronome_clicks=None, preview_data=None, course_stats=None):
        headers = parse_preview_headers(content)
        self._editor_bpm = headers["bpm"]
        self._editor_offset = headers["offset"]
        self._editor_subtitle = headers["subtitle"]
        self._editor_metronome_clicks = metronome_clicks or []
        self.lbl_editor_bpm.setText(f"{headers['bpm']:g}" if headers["bpm"] else "-")
        self.title_label.setText(headers["title"] or "(無題)")

        if preview_data is not None:
            self._editor_notes = [(t, c, bpm) for t, c, bpm, _sc in preview_data.get("notes", [])]
            self._editor_notes += _roll_tick_notes(preview_data.get("rolls", []), bpm_index=3)
            self._editor_notes += _roll_tick_notes(preview_data.get("balloons", []), bpm_index=2)

        self.waveform.set_beat_grid(headers["bpm"], self.spin_offset.value(), self._editor_metronome_clicks)
        self.game_waveform.set_beat_grid(headers["bpm"], self.spin_offset.value(), self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())
        self.hit_sounds.set_schedule(self._editor_notes, self.spin_offset.value())
        self.chart_preview.set_offset(self.spin_offset.value())
        if preview_data is not None:
            self.chart_preview.set_preview_data(preview_data)
            self.info_bar.set_course_info(
                preview_data.get("course_label"), preview_data.get("course_color"), preview_data.get("level"),
            )
            self.info_bar.set_branch_info(preview_data.get("branch_level"), preview_data.get("has_branches"))
        self.info_bar.set_static_info(headers["title"], headers["subtitle"], course_stats)

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
        self.game_waveform.set_beat_grid(headers["bpm"], headers["offset"], self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, headers["offset"])
        self.hit_sounds.set_schedule(self._editor_notes, headers["offset"])
        self.chart_preview.set_offset(headers["offset"])

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
        self.game_waveform.set_peaks(peaks, duration)
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
        self.game_waveform.set_position(ms / 1000.0)
        self.chart_preview.set_playback(ms / 1000.0, self.audio.is_playing())
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
        self.chart_preview.set_playback(self.audio.position() / 1000.0, playing)
        if not playing and self.refresh_preview_cb:
            # Reload from the current editor content (not whatever was last
            # cached) so edits made while playing/paused show up immediately
            # once playback stops, instead of waiting for the next cursor
            # move or edit to trigger a refresh.
            self.refresh_preview_cb()

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

    def _on_hit_sounds_toggled(self, checked):
        self.hit_sounds.set_enabled(checked)
        self.btn_hit_sounds.setObjectName("accentButton" if checked else "")
        self.btn_hit_sounds.style().unpolish(self.btn_hit_sounds)
        self.btn_hit_sounds.style().polish(self.btn_hit_sounds)

    def toggle_hit_sounds(self):
        """Flips the 打音(hit sounds) button; routed through toggled so the
        existing _on_hit_sounds_toggled handler updates enabled state and
        appearance in one place. Used by MainWindow's F1 shortcut."""
        self.btn_hit_sounds.setChecked(not self.btn_hit_sounds.isChecked())

    def set_metronome_clicks(self, clicks):
        self._editor_metronome_clicks = clicks or []
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())
        self.waveform.set_beat_grid(self._editor_bpm, self.spin_offset.value(), self._editor_metronome_clicks)
        self.game_waveform.set_beat_grid(self._editor_bpm, self.spin_offset.value(), self._editor_metronome_clicks)

    def set_preview_data(self, data, course_stats=None):
        data = data or {}
        self._editor_notes = [(t, c, bpm) for t, c, bpm, _sc in data.get("notes", [])]
        self._editor_notes += _roll_tick_notes(data.get("rolls", []), bpm_index=3)
        self._editor_notes += _roll_tick_notes(data.get("balloons", []), bpm_index=2)
        self.hit_sounds.set_schedule(self._editor_notes, self.spin_offset.value())
        self.chart_preview.set_preview_data(data)
        self.info_bar.set_course_info(data.get("course_label"), data.get("course_color"), data.get("level"))
        self.info_bar.set_branch_info(data.get("branch_level"), data.get("has_branches"))
        self.info_bar.set_static_info(self.title_label.text(), self._editor_subtitle, course_stats)

    def set_hit_sound_files(self, don_path: str, ka_path: str):
        self.hit_sounds.set_sound_files(don_path, ka_path)

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

    # ------------------------------------------------------------------
    # OFFSET adjust
    # ------------------------------------------------------------------
    def _on_offset_value_changed(self, value):
        self.waveform.set_beat_grid(self._editor_bpm, value, self._editor_metronome_clicks)
        self.game_waveform.set_beat_grid(self._editor_bpm, value, self._editor_metronome_clicks)
        self.metronome.set_schedule(self._editor_metronome_clicks, value)
        self.hit_sounds.set_schedule(self._editor_notes, value)
        self.chart_preview.set_offset(value)
        # Auto-synced into the TJA's own OFFSET: line as the user adjusts it
        # (not just on button click) - this only fires for user-driven
        # changes since the "load a new/same wave" paths above set the
        # spinbox with blockSignals(True).
        self._on_apply_offset()

    def _on_apply_offset(self):
        self.apply_offset_cb(f"{self.spin_offset.value():.3f}")
