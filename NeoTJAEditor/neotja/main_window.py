import os
import re
import shutil
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import Qt, QTimer, QByteArray
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

from neotja import settings as settings_mod
from neotja.constants import APP_NAME, NEW_FILE_TEMPLATE, VERSION
from neotja.editor_widget import TJAEditor
from neotja.theme import COLORS
from neotja.highlighter import TJAHighlighter, compute_highlight_data
from neotja.ai_chart_gen import build_ai_variant_content
from neotja.audio_engine import BpmOffsetDetectWorker, ChartGenWorker, TaikojiroScanWorker
from neotja.preview_dock import PreviewDock, parse_preview_headers
from neotja.ruler_widget import RulerWidget
from neotja.theme import apply_theme
from neotja.tja_analyzer import TJACourseAnalyzer


def find_non_cp932_chars(content: str):
    """TJA は ANSI (cp932) で保存するのが前提だが、絵文字や拡張漢字など
    cp932 に無い文字はそのままでは書けない。以前は errors="replace" で
    無言のうちに置換していて原文が壊れていたため、書く前にここで洗い出す。

    戻り値は [(行番号(1始まり), 文字), ...]。すべて cp932 で表せるなら空リスト。
    まず全体を1回 encode してみて、成功すればそこで打ち切る(圧倒的多数を
    占める通常のファイルでは C 実装の encode 1回分のコストしかかからない)。"""
    try:
        content.encode("cp932")
        return []
    except UnicodeEncodeError:
        pass

    bad = []
    for line_no, line in enumerate(content.split("\n"), start=1):
        try:
            line.encode("cp932")
        except UnicodeEncodeError:
            for ch in line:
                try:
                    ch.encode("cp932")
                except UnicodeEncodeError:
                    bad.append((line_no, ch))
    return bad


class CourseCard(QFrame):
    """Sidebar card showing one course's stats (notes/measures/time/roll/balloon),
    with a collapsible roll-by-roll detail list."""

    def __init__(self, course, parent=None):
        super().__init__(parent)
        self.details_visible = False

        self.setFrameShape(QFrame.Box)
        self.setStyleSheet(f"CourseCard {{ border: 1px solid {course['color']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QLabel(f"  {course['label']}")
        header.setStyleSheet(f"background-color: {course['color']}; color: {COLORS['bg']}; font-weight: bold; padding: 3px 0;")
        outer.addWidget(header)
        self.header = header

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 6, 8, 6)
        body_layout.setSpacing(2)
        outer.addWidget(body)

        self.lbl_time = QLabel()
        self.lbl_notes = QLabel()
        self.lbl_measures = QLabel()
        self.lbl_roll = QLabel()
        for lbl in (self.lbl_time, self.lbl_notes, self.lbl_measures, self.lbl_roll):
            body_layout.addWidget(lbl)

        self.btn_toggle = QPushButton("連打詳細 [開く]")
        self.btn_toggle.setFlat(True)
        self.btn_toggle.clicked.connect(self._toggle_details)
        body_layout.addWidget(self.btn_toggle)

        self.details_frame = QWidget()
        self.details_layout = QVBoxLayout(self.details_frame)
        self.details_layout.setContentsMargins(8, 0, 0, 0)
        body_layout.addWidget(self.details_frame)
        self.details_frame.setVisible(False)

        self.lbl_balloon = QLabel()
        body_layout.addWidget(self.lbl_balloon)

        self.update_course(course)

    def _toggle_details(self):
        self.details_visible = not self.details_visible
        self.details_frame.setVisible(self.details_visible)
        self.btn_toggle.setText("連打詳細 [閉じる]" if self.details_visible else "連打詳細 [開く]")

    def update_course(self, course):
        self.lbl_time.setText(f"時間: {course['time']}")
        self.lbl_notes.setText(f"ノーツ: {course['notes']}")
        self.lbl_measures.setText(f"小節: {course['measures']}")

        rolls = course.get("rolls_info", [])
        total_roll_dur = sum(r["duration"] for r in rolls)
        total_roll_hits = sum(r["hits"] for r in rolls)
        self.lbl_roll.setText(f"連打総計: {total_roll_dur:.2f}秒 ({total_roll_hits}打)")

        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if rolls:
            for i, r in enumerate(rolls, 1):
                self.details_layout.addWidget(QLabel(f"  {i}本目: {r['duration']:.2f}秒 ({r['hits']}打)"))
            self.btn_toggle.setVisible(True)
        else:
            self.btn_toggle.setVisible(False)
            self.details_frame.setVisible(False)
            self.details_visible = False

        balloons = course.get("balloons_info", [])
        total_balloon_hits = sum(b["hits"] for b in balloons)
        self.lbl_balloon.setText(f"風船総計: {len(balloons)}個 ({total_balloon_hits}打)")


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app

        self.config_data = settings_mod.load_settings()
        apply_theme(app, self.config_data.get("theme", "dark"))

        self.analyzer = TJACourseAnalyzer(self.config_data)
        self.current_file = None
        self.courses_info = []
        self._preview_course_override = None
        self._preview_branch_level = "M"
        self._heavy_timer = QTimer(self)
        self._heavy_timer.setSingleShot(True)
        self._heavy_timer.timeout.connect(self._heavy_tasks)
        # 直近に報告した解析エラーの種類。デバウンスタイマで何度も同じ例外が
        # 出るため、同じものはステータスバーに出し直さない(トレースも1回だけ)。
        self._last_heavy_error = None
        # cp932 で保存できない文字を自動保存が見つけたときの直近の報告内容。
        # 自動保存は打鍵のたびに走るので、同じ理由でスキップし続ける間は
        # 警告を出し直さない(モーダルは絶対に出さない)。
        self._last_autosave_encode_warning = None

        self._metronome_timer = QTimer(self)
        self._metronome_timer.setSingleShot(True)
        self._metronome_timer.timeout.connect(self._update_metronome_schedule)

        self.resize(1280, 820)
        # .tja/.txt をウィンドウにドラッグして開けるようにする(dropEvent)。
        self.setAcceptDrops(True)
        self._refresh_title()

        # Set once the update batch has been armed and the exit is already
        # agreed to, so closeEvent doesn't re-ask and veto the shutdown the
        # updater is waiting on (see _run_update_download).
        self._updating = False

        self._build_editor()
        self._build_toolbars()
        self._build_sidebar()
        self._build_central_layout()
        self._build_preview_dock()
        self._build_statusbar()
        self._build_menu()
        self._bind_shortcuts()

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_status)
        self.editor.cursorPositionChanged.connect(lambda: self._metronome_timer.start(150))
        self.editor.checkpointsChanged.connect(self._update_status)
        self.editor.set_note_typed_cb(self._on_note_typed)

        self._rebuild_recent_menu()
        self._restore_window_state()
        self.new_file(confirm=False)

        # A failed update can only be reported now: the batch that applies it
        # runs after the previous process is gone.
        QTimer.singleShot(300, self._report_failed_update)

        if self.config_data.get("check_updates_on_startup", True):
            QTimer.singleShot(1500, lambda: self.check_for_updates(manual=False))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_editor(self):
        self.editor = TJAEditor()
        self.editor.set_mono_font(self.config_data.get("font_family", "Consolas"), self.config_data.get("font_size", 12))
        self.highlighter = TJAHighlighter(self.editor.document())
        self.ruler = RulerWidget(self.editor)

        editor_container = QWidget()
        v = QVBoxLayout(editor_container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.ruler)
        v.addWidget(self.editor)
        self.editor_container = editor_container

    def _toolbar_button(self, toolbar, text, slot, accent=False):
        btn = QPushButton(text)
        if accent:
            btn.setObjectName("accentButton")
        btn.clicked.connect(slot)
        toolbar.addWidget(btn)
        return btn

    def _build_toolbars(self):
        self.toolbar_top = QToolBar("main")
        self.toolbar_top.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar_top)

        self._toolbar_button(self.toolbar_top, "新規", self.new_file_dialog)
        self._toolbar_button(self.toolbar_top, "開く", self.open_file)
        self._toolbar_button(self.toolbar_top, "フォルダを開く", self.open_folder)
        self._toolbar_button(self.toolbar_top, "保存", self.save_file, accent=True)
        self.btn_auto_save = QPushButton()
        self.btn_auto_save.setCheckable(True)
        self.btn_auto_save.setToolTip("構文の色付けが更新されるたびに自動保存します。")
        self.btn_auto_save.toggled.connect(self._on_auto_save_toggled)
        self.btn_auto_save.setChecked(self.config_data.get("auto_save_enabled", False))
        self._sync_auto_save_button(self.btn_auto_save.isChecked())
        self.toolbar_top.addWidget(self.btn_auto_save)
        self._toolbar_button(self.toolbar_top, "元に戻す", self.editor.undo)
        self._toolbar_button(self.toolbar_top, "やり直す", self.editor.redo)
        self._toolbar_button(self.toolbar_top, "譜面画像生成", self.open_image_exporter)
        self._toolbar_button(self.toolbar_top, "ヘルプ", self.open_help)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar_top.addWidget(spacer)
        self.encoding_label = QLabel("ANSI (cp932)")
        self.toolbar_top.addWidget(self.encoding_label)

        self.addToolBarBreak(Qt.TopToolBarArea)

        self.toolbar_bottom = QToolBar("tools")
        self.toolbar_bottom.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar_bottom)
        self._toolbar_button(self.toolbar_bottom, "ハイスピ変換", self.open_scroll_splitter)
        self._toolbar_button(self.toolbar_bottom, "リサイズ", self.open_measure_converter)
        self._toolbar_button(self.toolbar_bottom, "反転", self.reverse_don_ka)
        self._toolbar_button(self.toolbar_bottom, "ストロボ生成", self.open_strobe_tool)

    def _build_sidebar(self):
        self.sb_outer = QWidget()
        sb_layout = QVBoxLayout(self.sb_outer)
        sb_layout.setContentsMargins(0, 0, 0, 0)

        self.sb_title = QLabel("")
        self.sb_title.setWordWrap(True)
        self.sb_title.setContentsMargins(8, 8, 8, 8)
        sb_layout.addWidget(self.sb_title)

        spd_row = QWidget()
        spd_layout = QHBoxLayout(spd_row)
        spd_layout.setContentsMargins(8, 4, 8, 4)
        spd_layout.addWidget(QLabel("連打秒速:"))
        self.roll_speed_spin = QSpinBox()
        self.roll_speed_spin.setRange(1, 100)
        self.roll_speed_spin.setValue(self.config_data.get("roll_speed", 45))
        self.roll_speed_spin.valueChanged.connect(self._on_roll_speed_changed)
        spd_layout.addWidget(self.roll_speed_spin)
        spd_layout.addStretch()
        sb_layout.addWidget(spd_row)

        self.sb_scroll = QScrollArea()
        self.sb_scroll.setWidgetResizable(True)
        self.sb_cards_container = QWidget()
        self.sb_cards_layout = QVBoxLayout(self.sb_cards_container)
        self.sb_cards_layout.addStretch()
        self.sb_scroll.setWidget(self.sb_cards_container)
        sb_layout.addWidget(self.sb_scroll, 1)

        self._course_cards = {}

    def _build_central_layout(self):
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sb_outer)
        splitter.addWidget(self.editor_container)
        splitter.setSizes([260, 1020])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.splitter = splitter   # サイドバー分割比の保存/復元に使う
        self.setCentralWidget(splitter)

    def _build_preview_dock(self):
        self.preview_dock = PreviewDock(
            lambda v: self.replace_header_line("OFFSET", v),
            self,
            seek_cursor_cb=self._cursor_preview_seconds,
            volume_cb=self._save_preview_volume,
            duration_ready_cb=self._update_metronome_schedule,
            expanded_changed_cb=self._on_preview_expanded_changed,
            refresh_preview_cb=self._update_metronome_schedule,
            course_select_cb=self._on_preview_course_selected,
            game_preview_changed_cb=self._on_game_preview_visibility_changed,
            branch_select_cb=self._on_preview_branch_selected,
            audio_backend=self.config_data.get("audio_backend", "mixer"),
            sfx_volume_cb=self._save_sfx_volume,
            waveform_stereo=self.config_data.get("waveform_stereo", True),
            waveform_stereo_cb=self._save_waveform_stereo,
            se_text_enabled=self.config_data.get("se_text_enabled", True),
        )
        self.addDockWidget(Qt.BottomDockWidgetArea, self.preview_dock)
        self.preview_dock.set_volume(self.config_data.get("preview_volume", 0.8))
        self.preview_dock.set_sfx_volume(self.config_data.get("sfx_volume", 0.9))
        self.preview_dock.set_hit_sound_files(
            self.config_data.get("hit_sound_don_path", ""), self.config_data.get("hit_sound_ka_path", ""),
        )
        self._maybe_autodetect_hit_sounds()
        # No close/float/move features: the dock itself always stays docked
        # and visible. Its collapse/expand toggle lives in the status bar
        # (next to the theme switcher), so there's never a state where it
        # vanishes with no obvious way back.
        self.preview_dock.setFeatures(self.preview_dock.DockWidgetFeature(0))

    def _maybe_autodetect_hit_sounds(self):
        """Point the hit sounds at the user's own 太鼓さん次郎 install if they
        have one and haven't chosen files themselves.

        The built-in synth is a fallback, not a match for the real thing, and
        we can't ship 太鼓さん次郎's wavs (no redistribution grant - see
        find_taikojiro_sounds). Detecting the local copy gets the good sound
        without redistributing it. A path the user set by hand always wins;
        a stale one that no longer exists doesn't (it'd silently mean synth).
        """
        for key in ("hit_sound_don_path", "hit_sound_ka_path"):
            p = self.config_data.get(key, "")
            if p and os.path.exists(p):
                return

        def on_found(don, ka):
            self.config_data["hit_sound_don_path"] = don
            self.config_data["hit_sound_ka_path"] = ka
            settings_mod.save_settings(self.config_data)
            self.preview_dock.set_hit_sound_files(don, ka)
            self.statusBar().showMessage("太鼓さん次郎の打音を検出して設定しました", 5000)

        self._taikojiro_scan = TaikojiroScanWorker(self)
        self._taikojiro_scan.found.connect(on_found)
        self._taikojiro_scan.start()

    def _save_preview_volume(self, volume: float):
        self.config_data["preview_volume"] = volume
        settings_mod.save_settings(self.config_data)

    def _save_waveform_stereo(self, stereo: bool):
        self.config_data["waveform_stereo"] = bool(stereo)
        settings_mod.save_settings(self.config_data)

    def _save_sfx_volume(self, volume: float):
        self.config_data["sfx_volume"] = volume
        settings_mod.save_settings(self.config_data)

    # 機能1(ノーツ入力音): エディタでノーツ文字を打鍵した瞬間に対応する
    # 打音を即座に鳴らす。1/3=ドン、2/4=カツ。5/6(連打開始)・7(風船開始)・
    # 9(クスダマ開始)はいずれも本アプリの連打/風船/クスダマ判定音が
    # 「ドンのみ(交互に鳴らさない)」という既存方針(#94)に合わせてドン
    # ティックを鳴らす。8(連打/風船の終端マーカー)は打音そのものを表さない
    # ため無音、0(空白)は _NOTE_SOUND_CHARS の時点で除外済み。
    _NOTE_INPUT_SOUND_KIND = {
        "1": "don", "3": "don", "2": "ka", "4": "ka",
        "5": "don", "6": "don", "7": "don", "9": "don",
    }

    def _on_note_typed(self, char: str, line_no: int):
        """TJAEditor.set_note_typed_cb から、ノーツ文字が実際に1回キー入力
        された直後に呼ばれる(貼り付けやプログラムによる挿入では呼ばれない)。
        設定・コース範囲・F1トグルをすべて満たしたときだけ、現在のバックエンド
        (ミキサー優先/レガシー QSoundEffect フォールバック)経由で打音を
        1回だけ鳴らす。スケジュール済み打音や再生位置には一切触れない。"""
        if not self.config_data.get("note_input_sound", True):
            return
        kind = self._NOTE_INPUT_SOUND_KIND.get(char)
        if kind is None:
            return
        content = self.editor.toPlainText()
        if not self.analyzer.line_in_course_body(content, line_no):
            return
        # 命令文(#MEASURE 4/4, #BPMCHANGE 120, #SCROLL 1 ...)の中の数字では
        # 鳴らさない - 譜面本文としてのノーツ入力だけを対象にする。
        line_text = self.editor.document().findBlockByNumber(line_no - 1).text()
        if line_text.lstrip().startswith("#"):
            return
        hit_sounds = getattr(self.preview_dock, "hit_sounds", None)
        if hit_sounds is not None:
            hit_sounds.play_once(kind)

    def _on_preview_course_selected(self, course_key):
        # course_key is None for "follow the editor cursor" (the default
        # behavior); otherwise it pins the game-style preview to that course
        # regardless of where the cursor is, until picked again.
        self._preview_course_override = course_key
        self._update_metronome_schedule()

    def _on_preview_branch_selected(self, level):
        # Static branch choice for the whole course - see build_preview_timeline's
        # docstring for why this preview doesn't simulate dynamic switching.
        self._preview_branch_level = level
        self._update_metronome_schedule()

    def _cursor_preview_seconds(self):
        """Returns the audio-file seek position (seconds) for the measure
        under the editor cursor, or None if the cursor isn't inside a
        course's #START..#END body."""
        line_no = self.editor.textCursor().blockNumber() + 1
        content = self.editor.toPlainText()
        chart_time = self.analyzer.time_at_cursor(content, line_no)
        if chart_time is None:
            return None
        # OFFSET convention: chart_time = audio_time + OFFSET -> audio_time = chart_time - OFFSET.
        offset = self.preview_dock.spin_offset.value()
        return max(0.0, chart_time - offset)

    def replace_header_line(self, key: str, value: str) -> bool:
        prefix = f"{key}:"
        block = self.editor.document().firstBlock()
        while block.isValid():
            if block.text().startswith(prefix):
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                cursor.insertText(f"{prefix}{value}")
                return True
            block = block.next()
        return False

    def _build_statusbar(self):
        self.btn_theme = QPushButton("テーマ切替")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.statusBar().addPermanentWidget(self.btn_theme)

        self.btn_preview_toggle = QPushButton("▼ プレビュー")
        self.btn_preview_toggle.setCheckable(True)
        self.btn_preview_toggle.setChecked(True)
        self.btn_preview_toggle.toggled.connect(self._on_preview_toggle_clicked)
        self.statusBar().addPermanentWidget(self.btn_preview_toggle)

        self.btn_game_preview_toggle = QPushButton("譜面プレビュー")
        self.btn_game_preview_toggle.setCheckable(True)
        self.btn_game_preview_toggle.toggled.connect(self._on_game_preview_toggle_clicked)
        self.statusBar().addPermanentWidget(self.btn_game_preview_toggle)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)

    def _on_preview_toggle_clicked(self, checked):
        self.preview_dock.set_expanded(checked)
        self.btn_preview_toggle.setText("▼ プレビュー" if checked else "▲ プレビュー")

    def _on_preview_expanded_changed(self, expanded):
        self.btn_preview_toggle.setChecked(expanded)

    def _on_game_preview_toggle_clicked(self, checked):
        self.preview_dock.set_game_preview_visible(checked)

    def _on_game_preview_visibility_changed(self, visible):
        self.btn_game_preview_toggle.setChecked(visible)

    def _on_f1(self):
        """F1: launch the built-in game-style preview (えぬいーさん次郎)
        if it's not open yet; while it's open, F1 instead toggles hit
        sounds so the key never relaunches/duplicates the preview."""
        if not self.preview_dock.is_game_preview_visible():
            self.preview_dock.set_game_preview_visible(True)
            self.btn_game_preview_toggle.setChecked(True)
        else:
            self.preview_dock.toggle_hit_sounds()

    def _build_menu(self):
        mb = self.menuBar()

        fm = mb.addMenu("ファイル")
        fm.addAction("新規作成", self.new_file_dialog)
        fm.addAction("開く", self.open_file)
        self._recent_menu = fm.addMenu("最近使ったファイル")
        self._rebuild_recent_menu()
        fm.addAction("別ウィンドウで開く", self.open_new_window)
        fm.addSeparator()
        fm.addAction("上書き保存  Ctrl+S", self.save_file)
        fm.addAction("名前を付けて保存", self.save_file_as)
        fm.addSeparator()
        fm.addAction("終了", self.close)

        tm = mb.addMenu("ツール")
        tm.addAction("ハイスピ変換", self.open_scroll_splitter)
        tm.addAction("ノーツ間隔リサイズ", self.open_measure_converter)
        tm.addSeparator()
        tm.addAction("あべこべ反転  Ctrl+M", self.reverse_don_ka)
        tm.addSeparator()
        tm.addAction("BPM/OFFSET自動検出(実験的)", self.auto_detect_bpm_offset)
        tm.addAction("AI譜面生成(実験的)", self.open_auto_chart_generator)

        rm = mb.addMenu("起動")
        self._run_actions = {}
        # F1 is reserved for the built-in preview (see _bind_shortcuts /
        # _on_f1), not an external simulator, so its label is fixed rather
        # than sourced from run_config and it isn't refreshed in
        # open_settings() below.
        f1_action = rm.addAction("F1: えぬいーさん次郎(内蔵プレビュー)", self._on_f1)
        self._run_actions["F1"] = f1_action
        for k in ("F2", "F3"):
            action = rm.addAction(f"{k}: {self.config_data['run_config'][k]['name']}", lambda key=k: self.run_simulator(key))
            self._run_actions[k] = action

        sm = mb.addMenu("設定")
        sm.addAction("環境設定...", self.open_settings)

        hm = mb.addMenu("ヘルプ")
        hm.addAction("ヘルプを表示", self.open_help)
        hm.addAction("更新を確認", lambda: self.check_for_updates(manual=True))
        hm.addAction("バージョン情報", self._show_about)

    def _bind_shortcuts(self):
        def ins_space(cmd):
            self.editor.insert_at_cursor(cmd + " ")
            self._force_update()

        def ins_nl(cmd):
            self.editor.insert_at_cursor(cmd + "\n")
            self._force_update()

        bindings_space = {
            "Alt+B": "#BPMCHANGE", "Alt+S": "#SCROLL", "Alt+D": "#DELAY",
            "Alt+R": "#BRANCHSTART", "Alt+U": "#MEASURE",
        }
        for seq, cmd in bindings_space.items():
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda c=cmd: ins_space(c))

        bindings_nl = {
            "Alt+G": "#GOGOSTART", "Ctrl+G": "#GOGOSTART",
            "Alt+O": "#GOGOEND", "Ctrl+O": "#GOGOEND",
            "Alt+L": "#BARLINEON", "Alt+I": "#BARLINEOFF",
        }
        for seq, cmd in bindings_nl.items():
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda c=cmd: ins_nl(c))

        sc_save = QShortcut(QKeySequence("Ctrl+S"), self.editor)
        sc_save.setContext(Qt.WidgetWithChildrenShortcut)
        sc_save.activated.connect(self.save_file)

        sc_cut = QShortcut(QKeySequence("Ctrl+T"), self.editor)
        sc_cut.setContext(Qt.WidgetWithChildrenShortcut)
        sc_cut.activated.connect(self.editor.cut)

        sc_reverse = QShortcut(QKeySequence("Ctrl+M"), self.editor)
        sc_reverse.setContext(Qt.WidgetWithChildrenShortcut)
        sc_reverse.activated.connect(self.reverse_don_ka)

        sc_toggle_cp = QShortcut(QKeySequence("Alt+P"), self.editor)
        sc_toggle_cp.setContext(Qt.WidgetWithChildrenShortcut)
        sc_toggle_cp.activated.connect(self.editor.toggle_checkpoint)

        for seq, direction in (("Alt+Up", "up"), ("Ctrl+Up", "up"), ("Alt+Down", "down"), ("Ctrl+Down", "down")):
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=direction: self.editor.jump_checkpoint(d))

        for k in "1234567890":
            sc = QShortcut(QKeySequence(f"Alt+{k}"), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda key=k: self._insert_custom(key))

        for k in ("F2", "F3"):
            sc = QShortcut(QKeySequence(k), self)
            sc.activated.connect(lambda key=k: self.run_simulator(key))

        # F1 is reserved for the built-in game-style preview: first press
        # launches it, subsequent presses (while it's open) toggle hit
        # sounds instead of relaunching. ApplicationShortcut so it fires
        # whether focus is in the editor or the (separate) preview window.
        sc_f1 = QShortcut(QKeySequence("F1"), self)
        sc_f1.setContext(Qt.ApplicationShortcut)
        sc_f1.activated.connect(self._on_f1)

    def _insert_custom(self, key):
        text = self.config_data["custom_shortcuts"].get(key, "")
        if text:
            self.editor.insert_at_cursor(text)
            self._force_update()

    def _on_roll_speed_changed(self, value):
        self.config_data["roll_speed"] = value
        self._force_update()

    # ------------------------------------------------------------------
    # Debounced analysis pass (mirrors the original's after(400, ...) pattern)
    # ------------------------------------------------------------------
    def _on_text_changed(self):
        # タイトルバー/タスクバーに未保存(*)を出す。dirty 判定はアプリ独自の
        # modified_lines(実際のキー入力で増える)に合わせる。setPlainText や
        # OFFSET の裏書き戻しといったプログラム的な編集では増えないので、
        # 読み込み/保存直後に * が付いてしまうのを防げる。setWindowModified は
        # タイトル内の [*] プレースホルダを * / 空 に切り替える。
        self.setWindowModified(bool(self.editor.modified_lines))
        # On large real-world charts, one heavy pass (full re-highlight +
        # course analysis) can take 100ms+, so a longer debounce keeps it
        # from re-triggering on every short pause while actively typing.
        self._heavy_timer.start(600)

    # ------------------------------------------------------------------
    # タイトル / 最近使ったファイル / ウィンドウ状態 (クイックウィン群)
    # ------------------------------------------------------------------
    def _refresh_title(self):
        """タイトルを現在のファイル名から組み立て直す。末尾の [*] は Qt の
        未保存インジケータで、setWindowModified(True) のとき * になる。"""
        base = f"{APP_NAME}  v{VERSION}"
        if self.current_file:
            base += f"  —  {os.path.basename(self.current_file)}"
        self.setWindowTitle(base + " [*]")

    def _mark_saved(self):
        """保存済み/新規/読込直後に呼ぶ。タイトルの * を消す。"""
        self.setWindowModified(False)

    def _push_recent(self, path):
        if not path:
            return
        path = os.path.abspath(path)
        recent = [p for p in self.config_data.get("recent_files", []) if p != path]
        recent.insert(0, path)
        self.config_data["recent_files"] = recent[:10]
        settings_mod.save_settings(self.config_data)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        recent = [p for p in self.config_data.get("recent_files", []) if os.path.exists(p)]
        # 存在しなくなったパスは掃除して保存し直す。
        if recent != self.config_data.get("recent_files", []):
            self.config_data["recent_files"] = recent
            settings_mod.save_settings(self.config_data)
        if not recent:
            act = menu.addAction("(履歴なし)")
            act.setEnabled(False)
            return
        for p in recent:
            menu.addAction(os.path.basename(p), lambda checked=False, path=p: self._open_recent(path))
        menu.addSeparator()
        menu.addAction("履歴を消去", self._clear_recent)

    def _clear_recent(self):
        self.config_data["recent_files"] = []
        settings_mod.save_settings(self.config_data)
        self._rebuild_recent_menu()

    def _open_recent(self, path):
        if not os.path.exists(path):
            QMessageBox.information(self, "情報", "このファイルは見つかりませんでした。")
            self._rebuild_recent_menu()
            return
        if not self._unsaved_check():
            return
        self._open_path(path)

    def _restore_window_state(self):
        try:
            geo = self.config_data.get("window_geometry", "")
            if geo:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            st = self.config_data.get("splitter_state", "")
            if st and hasattr(self, "splitter"):
                self.splitter.restoreState(QByteArray.fromBase64(st.encode("ascii")))
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _save_window_state(self):
        try:
            self.config_data["window_geometry"] = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
            if hasattr(self, "splitter"):
                self.config_data["splitter_state"] = bytes(
                    self.splitter.saveState().toBase64()).decode("ascii")
            settings_mod.save_settings(self.config_data)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _force_update(self):
        self._heavy_timer.stop()
        self._heavy_tasks()

    def _heavy_tasks(self):
        content = self.editor.toPlainText()
        # 解析/プレビュー更新のどこかで想定外の例外が出ても、自動保存まで
        # 道連れにしない。以前は解析が一度でも例外を投げると、この後ろにある
        # _auto_save_tick() へ到達しなくなり「入力しているのに保存されず、
        # プレビューも止まったまま、エラー表示も無い」という無言の失敗になった。
        # 握りつぶさず、標準エラーへトレースを出しステータスバーにも表示する。
        try:
            self._heavy_analysis(content)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            sig = f"{type(e).__name__}: {e}"
            if sig != self._last_heavy_error:
                self._last_heavy_error = sig
                self.statusBar().showMessage(f"解析中にエラーが発生しました: {sig}", 8000)
        else:
            self._last_heavy_error = None
        self._auto_save_tick()

    def _heavy_analysis(self, content):
        self.courses_info = self.analyzer.parse_courses(content)
        self._refresh_sidebar(content)
        data = compute_highlight_data(content, self.courses_info)
        self.editor.highlight_data = data
        self.editor.invalid_lines = data.invalid_lines
        self._global_warnings = data.global_warnings
        self.highlighter.apply_data(data)
        self.editor.gutter.update()
        self._update_status()
        cursor_line = self.editor.textCursor().blockNumber() + 1
        metronome_clicks = self.analyzer.build_metronome_clicks(content, cursor_line, self.preview_dock.duration_seconds())
        preview_data = self.analyzer.build_preview_timeline(
            content, cursor_line, self._preview_course_override, branch_level=self._preview_branch_level,
        )
        course_stats = self._find_course_stats(preview_data.get("course_key"))
        self.preview_dock.refresh_from_content(content, self.current_file, metronome_clicks, preview_data, course_stats)

    def _find_course_stats(self, course_key):
        return next((c for c in self.courses_info if c["key"] == course_key), None)

    def _update_metronome_schedule(self):
        # Lighter-weight than _heavy_tasks: just re-picks which course's
        # #MEASURE/#BPMCHANGE the metronome/preview should follow when the
        # cursor moves into a different course, without re-running the
        # expensive syntax highlighter over the whole document.
        content = self.editor.toPlainText()
        cursor_line = self.editor.textCursor().blockNumber() + 1
        clicks = self.analyzer.build_metronome_clicks(content, cursor_line, self.preview_dock.duration_seconds())
        self.preview_dock.set_metronome_clicks(clicks)
        preview_data = self.analyzer.build_preview_timeline(
            content, cursor_line, self._preview_course_override, branch_level=self._preview_branch_level,
        )
        self.preview_dock.set_preview_data(preview_data, self._find_course_stats(preview_data.get("course_key")))

    def _refresh_sidebar(self, content):
        lines = content.split('\n')
        title = ""
        subtitle = ""
        for l in lines:
            if l.startswith("TITLE:"):
                title = l[6:].strip()
            if l.startswith("SUBTITLE:"):
                subtitle = re.sub(r"^(--|\+\+)", "", l[9:].strip())
        header = f"  {title}" if title else "  (無題)"
        self.sb_title.setText(f"{header}\n  {subtitle}" if subtitle else header)

        ordered_courses = sorted(
            self.courses_info, key=lambda c: -TJACourseAnalyzer.DIFF_RANK.get(c["key"], -1)
        )
        visible_keys = [c["key"] for c in ordered_courses]
        for course in ordered_courses:
            k = course["key"]
            if k in self._course_cards:
                self._course_cards[k].update_course(course)
            else:
                self._course_cards[k] = CourseCard(course)

        for k in [k for k in self._course_cards if k not in visible_keys]:
            self._course_cards[k].deleteLater()
            del self._course_cards[k]

        # Explicitly reposition every card (new or reused) each time, since a
        # card kept from a previous refresh (e.g. the blank template's single
        # "oni" course loaded at startup) would otherwise stay pinned at
        # whatever index it was first inserted at.
        for i, course in enumerate(ordered_courses):
            self.sb_cards_layout.insertWidget(i, self._course_cards[course["key"]])

    def _update_status(self):
        cursor = self.editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.positionInBlock()
        msg = f"  行 {line}  文字数 {col}"
        # カーソルがコース本文内なら、その小節番号と譜面上の時刻を出す。
        # time_at_cursor/measure_at_cursor は本文外だと None を返す。
        try:
            content = self.editor.toPlainText()
            measure = self.analyzer.measure_at_cursor(content, line)
            if measure is not None:
                sec = self.analyzer.time_at_cursor(content, line)
                pos = f"第{measure}小節"
                if sec is not None:
                    pos += f"  {int(sec) // 60}:{sec % 60:06.3f}"
                msg += f"  │  {pos}"
        except Exception:  # noqa: BLE001
            pass
        msg += "  │  ANSI (CP932)"
        invalid_lines = getattr(self.editor, "invalid_lines", {})
        if line in invalid_lines:
            msg += f"  │  ⚠ 不正文字数 ({invalid_lines[line]})"
        warnings = getattr(self, "_global_warnings", [])
        if warnings:
            msg += "  │  " + "  ".join(warnings)
        self.status_label.setText(msg)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------
    def _unsaved_check(self) -> bool:
        if not self.editor.modified_lines:
            return True
        ans = QMessageBox.question(
            self, "確認", "変更を保存しますか？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if ans == QMessageBox.Yes:
            # 保存が中止された(cp932にできない文字がある等)なら、変更を
            # 捨てる方向へは進めない。
            return bool(self.save_file())
        return ans == QMessageBox.No

    def new_file(self, confirm=True):
        if confirm and not self._unsaved_check():
            return
        self.editor.setPlainText(NEW_FILE_TEMPLATE)
        self.current_file = None
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        self._refresh_title()
        self._mark_saved()
        self._force_update()

    def new_file_dialog(self):
        if not self._unsaved_check():
            return
        from neotja.dialogs.new_project_dialog import NewProjectDialog
        dlg = NewProjectDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.mode == "blank":
            self.new_file(confirm=False)
            return
        self._apply_youtube_project(
            dlg.result_title, dlg.result_subtitle, dlg.result_wave_path, dlg.result_folder,
            dlg.result_bpm, dlg.result_offset, dlg.enable_ai_gen,
        )

    @staticmethod
    def _relocate_wave_file(src: str, dst: str):
        """Copies src to dst, then best-effort removes src - not
        shutil.move(), because on Windows a just-finished QAudioDecoder (see
        BpmOffsetDetectWorker, which analyzes this exact file for BPM/OFFSET
        auto-detect right before this runs) can still hold src's file handle
        open for a moment, and move()'s internal rename/unlink step raises
        PermissionError ("WinError 32: the process cannot access the file")
        in that case - which used to abort project creation entirely before
        the .tja ever got written. Copying first means a lingering lock only
        costs a leftover duplicate .ogg (removed on a later retry, or left
        as harmless clutter) instead of losing the whole new project."""
        shutil.copy2(src, dst)
        for _ in range(5):
            try:
                os.remove(src)
                return
            except OSError:
                time.sleep(0.3)

    def _apply_youtube_project(self, title, subtitle, wave_path, folder, bpm=None, offset=None, enable_ai_gen=False):
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', title).strip() or "untitled"

        # Give every new song its own <TITLE>/ folder, with the .tja and the
        # .ogg both named to match TITLE (rather than the raw video title
        # yt-dlp downloaded it as).
        song_folder = os.path.join(folder, safe_name)
        os.makedirs(song_folder, exist_ok=True)
        wave_name = f"{safe_name}.ogg"
        new_wave_path = os.path.join(song_folder, wave_name)
        if os.path.abspath(wave_path) != os.path.abspath(new_wave_path):
            self._relocate_wave_file(wave_path, new_wave_path)

        content = NEW_FILE_TEMPLATE.replace("TITLE:\n", f"TITLE:{title}\n", 1)
        # SUBTITLE always carries a leading "--" marker (see
        # parse_preview_headers's matching read-side convention) - the
        # actual text, if any, goes right after it, never replacing it.
        content = content.replace("SUBTITLE:--\n", f"SUBTITLE:--{subtitle}\n" if subtitle else "SUBTITLE:--\n", 1)
        content = content.replace("WAVE:\n", f"WAVE:{wave_name}\n", 1)
        # BPM/OFFSET auto-detection (experimental) already ran once in the
        # new-project dialog against the downloaded audio - pre-fill the
        # headers with its result rather than leaving them blank/0.00. Best-
        # effort: a formatting hiccup on this experimental value must not
        # abort file creation before editor.setPlainText(content) below runs
        # (which would otherwise silently leave the editor on its previous,
        # untitled content).
        try:
            if bpm:
                content = content.replace("BPM:\n", f"BPM:{bpm:g}\n", 1)
            if offset is not None:
                content = content.replace("OFFSET:0.00\n", f"OFFSET:{offset:.3f}\n", 1)
        except (TypeError, ValueError):
            pass

        tja_path = os.path.join(song_folder, f"{safe_name}.tja")

        self.editor.setPlainText(content)
        self.current_file = tja_path
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        self._refresh_title()
        self.save_file()
        self._mark_saved()
        self._push_recent(tja_path)
        self._force_update()

        self.preview_dock.expand()

        # Opt-in (see new_project_dialog.py's 実験的機能を使用する section) -
        # runs in the background against the template's only course (Oni)
        # and writes a separate <basename>(AI).tja next to the file just
        # created above, without touching it or the editor.
        if enable_ai_gen:
            headers = parse_preview_headers(content)
            gen_bpm = headers["bpm"] or 120.0
            gen_offset = headers["offset"] or 0.0
            self.status_label.setText("AI譜面生成を実行中(実験的)...")
            worker = ChartGenWorker(new_wave_path, gen_bpm, gen_offset, subdivision=16, density=0.5, parent=self)
            self._new_project_chart_gen_worker = worker

            def on_generated(body, _content=content, _path=tja_path):
                # Silent write, not _save_ai_chart_variant - this runs
                # unattended after project creation, so it must not pop a
                # modal QMessageBox the user never asked to dismiss.
                try:
                    new_path = self._write_ai_variant_file(_content, "Oni", body, _path)
                except UnicodeEncodeError:
                    self.status_label.setText(
                        "AI譜面生成の保存に失敗しました(実験的): "
                        "ANSI (cp932) で表せない文字が含まれています。")
                    return
                except OSError as e:
                    self.status_label.setText(f"AI譜面生成の保存に失敗しました(実験的): {e}")
                    return
                if new_path is None:
                    self.status_label.setText("AI譜面生成: 対象コースが見つかりませんでした(実験的)。")
                    return
                self.status_label.setText(f"AI譜面生成(実験的)による追加ファイルを作成しました: {os.path.basename(new_path)}")

            def on_failed(msg):
                self.status_label.setText(f"AI譜面生成に失敗しました(実験的): {msg}")

            worker.generated.connect(on_generated)
            worker.failed.connect(on_failed)
            worker.start()

    def _begin_loading(self, message: str):
        """重い読み込み処理の間、中央に「読み込み中」オーバーレイを出す。
        setPlainText(構文ハイライト)や _force_update(譜面解析)は GUI スレッド
        で同期実行されて数百 ms 固まるので、その前にこれを見せて processEvents で
        一度だけ描画しておく(処理中はこの表示のまま静止する)。"""
        lbl = getattr(self, "_loading_overlay", None)
        if lbl is None:
            lbl = QLabel(self)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "background-color: rgba(0,0,0,160); color: #ffffff;"
                " font-size: 18px; font-weight: bold;"
            )
            self._loading_overlay = lbl
        lbl.setText(message)
        lbl.setGeometry(self.rect())
        lbl.raise_()
        lbl.show()
        QApplication.processEvents()

    def _end_loading(self):
        lbl = getattr(self, "_loading_overlay", None)
        if lbl is not None:
            lbl.hide()

    def open_file(self):
        if not self._unsaved_check():
            return
        path, _ = QFileDialog.getOpenFileName(self, "開く", "", "TJA Files (*.tja);;Text Files (*.txt)")
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path):
        """指定パスの TJA を読み込んでエディタに反映する。ダイアログ経由の
        open_file、ドラッグ&ドロップ、最近使ったファイルの共通処理。呼び出し側
        で未保存確認(_unsaved_check)を済ませておくこと。"""
        try:
            with open(path, "r", encoding="cp932") as f:
                content = f.read()
        except UnicodeDecodeError:
            # utf-8-sig (not plain utf-8) so a leading BOM (U+FEFF) is stripped.
            # A UTF-8-BOM file otherwise leaves U+FEFF on line 1, and since
            # TITLE: is conventionally the first line, every startswith("TITLE:")
            # check across the app silently fails (blank title, etc.).
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            QMessageBox.information(
                self, "文字コード変換",
                "UTF-8で保存されたファイルを読み込みました。\n次回保存時に自動的にANSI形式で保存されます。",
            )
        except OSError as e:
            QMessageBox.critical(self, "読み込みエラー", f"ファイルを開けませんでした:\n{e}")
            return
        # Belt and braces: strip any stray leading BOM so header parsing always
        # sees a clean first line, whichever decode path ran.
        content = content.lstrip("﻿")

        self._begin_loading("TJAを読み込み中...")
        try:
            self.editor.setPlainText(content)
            self.current_file = path
            self.editor.modified_lines.clear()
            self._refresh_title()
            self._mark_saved()
            self._push_recent(path)
            self._force_update()
        finally:
            self._end_loading()

    def open_folder(self):
        if self.current_file and os.path.exists(self.current_file):
            folder = os.path.dirname(self.current_file)
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        else:
            QMessageBox.information(self, "情報", "ファイルがまだ保存されていません。")

    def save_file(self):
        """上書き保存。cp932 で表せない文字が含まれる場合は書き込まず、何が
        問題かを知らせて利用者に判断してもらう。戻り値は「保存できたか」で、
        _unsaved_check はこれを見て終了/破棄を中止する。"""
        if not self.current_file:
            return self.save_file_as()
        content = self.editor.toPlainText()

        bad = find_non_cp932_chars(content)
        if bad:
            # 以前はここで errors="replace" のまま書いていたため、絵文字などが
            # 無言で「?」に置き換わって原文が失われていた。壊れたテキストは
            # 絶対に書かない。
            if not self._confirm_non_cp932_save(bad):
                return False
            try:
                with open(self.current_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "保存エラー", str(e))
                return False
            self.editor.modified_lines.clear()
            self.editor.gutter.update()
            self._mark_saved()
            self.statusBar().showMessage(
                "UTF-8で保存しました。TJAシミュレータによっては読み込めないことがあります。", 8000)
            return True

        try:
            # ここに来た時点で cp932 で完全に表せることを確認済みなので、
            # errors は strict のままでよい(想定外は握りつぶさず例外にする)。
            with open(self.current_file, "w", encoding="cp932") as f:
                f.write(content)
            self.editor.modified_lines.clear()
            self.editor.gutter.update()
            self._mark_saved()
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))
            return False
        return True

    def _confirm_non_cp932_save(self, bad):
        """cp932 で保存できない文字を一覧で見せ、どうするか尋ねる。
        既定は「保存しない」(データを壊さない側)。UTF-8 で保存してよいと
        明示的に選ばれた場合だけ True を返す。"""
        chars = []
        for _line_no, ch in bad:
            if ch not in chars:
                chars.append(ch)
        detail_lines = []
        for ch in chars[:10]:
            lines = sorted({ln for ln, c in bad if c == ch})
            shown = ", ".join(str(ln) for ln in lines[:5])
            if len(lines) > 5:
                shown += " ほか"
            detail_lines.append(f"  「{ch}」 (U+{ord(ch):04X})  {shown} 行目")
        if len(chars) > 10:
            detail_lines.append(f"  ...ほか {len(chars) - 10} 種類")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("保存できない文字があります")
        box.setText(
            "ANSI (cp932) で表せない文字が含まれているため、このまま保存すると"
            "その文字が失われます。\n\n" + "\n".join(detail_lines) +
            "\n\nこれらの文字を消すか別の文字に置き換えてから保存し直してください。\n"
            "UTF-8で保存することもできますが、TJAシミュレータによっては"
            "読み込めない場合があります。"
        )
        btn_cancel = box.addButton("保存しない", QMessageBox.RejectRole)
        btn_utf8 = box.addButton("UTF-8で保存する", QMessageBox.DestructiveRole)
        box.setDefaultButton(btn_cancel)
        box.setEscapeButton(btn_cancel)
        box.exec()
        return box.clickedButton() is btn_utf8

    def _auto_save_tick(self):
        if not self.config_data.get("auto_save_enabled", False):
            return
        if not self.current_file or not self.editor.modified_lines:
            return

        # 自動保存はデバウンスタイマ上(=入力中)に走るので、モーダルは絶対に
        # 出さない。cp932 で表せない文字があるときは書き込みを見送り、
        # ステータスバーで知らせるだけにする。同じ理由でスキップし続ける間は
        # 毎tick出し直さない。
        bad = find_non_cp932_chars(self.editor.toPlainText())
        if bad:
            chars = []
            for _ln, ch in bad:
                if ch not in chars:
                    chars.append(ch)
            sig = "".join(chars)
            if sig != self._last_autosave_encode_warning:
                self._last_autosave_encode_warning = sig
                shown = " ".join(f"「{c}」" for c in chars[:5])
                more = f" ほか{len(chars) - 5}種類" if len(chars) > 5 else ""
                self.statusBar().showMessage(
                    f"自動保存を見送りました: ANSI (cp932) で保存できない文字があります: {shown}{more}",
                    10000)
            return
        self._last_autosave_encode_warning = None

        if self.save_file():
            self.statusBar().showMessage("自動保存しました", 2000)

    def _on_auto_save_toggled(self, checked):
        self.config_data["auto_save_enabled"] = checked
        settings_mod.save_settings(self.config_data)
        self._sync_auto_save_button(checked)

    def _sync_auto_save_button(self, checked):
        self.btn_auto_save.setText("自動保存: ON" if checked else "自動保存: OFF")
        self.btn_auto_save.setObjectName("accentButton" if checked else "")
        self.btn_auto_save.style().unpolish(self.btn_auto_save)
        self.btn_auto_save.style().polish(self.btn_auto_save)

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", "", "TJA Files (*.tja);;All Files (*.*)")
        if not path:
            return False
        if not path.lower().endswith(".tja") and "." not in os.path.basename(path):
            path += ".tja"
        self.current_file = path
        self._refresh_title()
        ok = self.save_file()
        if ok:
            self._push_recent(path)
        return ok

    def open_new_window(self):
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])])

    # ------------------------------------------------------------------
    # ドラッグ&ドロップで開く
    # ------------------------------------------------------------------
    @staticmethod
    def _dropped_tja_path(event):
        md = event.mimeData()
        if not md.hasUrls():
            return None
        for url in md.urls():
            p = url.toLocalFile()
            if p and p.lower().endswith((".tja", ".txt")):
                return p
        return None

    def dragEnterEvent(self, event):
        if self._dropped_tja_path(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._dropped_tja_path(event)
        if not path:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        if not self._unsaved_check():
            return
        self._open_path(path)

    def closeEvent(self, event):
        # _updating means the unsaved check already ran and the updater batch is
        # armed and waiting on this process to exit - vetoing here would hang it.
        if self._updating or self._unsaved_check():
            # 終了が確定したこの時点で音声デバイスを確定的に閉じる。以前は
            # MixerAudioEngine.close() を誰も呼んでおらず、PortAudio の
            # ストリームがプロセス終了任せになっていた。
            self._save_window_state()
            try:
                self.preview_dock.shutdown_audio()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            # 実行中かもしれない自前ワーカー(BPM/OFFSET検出・更新確認・更新DL・
            # AI譜面生成)を待機所へ退避する。これをしないと、走行中に閉じたとき
            # "QThread: Destroyed while thread is still running" でアプリごと
            # 落ちうる。detach_worker は終了済み/None を安全に無視する。
            from neotja.worker_util import detach_worker
            for attr in ("_bpm_detect_worker", "_update_check_worker",
                         "_update_download_worker", "_new_project_chart_gen_worker"):
                detach_worker(getattr(self, attr, None))
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Simulator launch
    # ------------------------------------------------------------------
    def run_simulator(self, key):
        path = self.config_data["run_config"][key]["path"]
        if path and os.path.exists(path):
            subprocess.Popen([path])
        else:
            QMessageBox.warning(
                self, "警告",
                f"{key} のパスが未設定です。\nメニュー「設定 → 環境設定...」で設定してください。",
            )

    # ------------------------------------------------------------------
    # Text tools
    # ------------------------------------------------------------------
    def _get_selection(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            QMessageBox.warning(self, "確認", "範囲を選択してください。")
            return None, None
        text = cursor.selectedText().replace(" ", "\n")
        return cursor, text

    def reverse_don_ka(self):
        cursor, txt = self._get_selection()
        if txt is None:
            return
        new = txt.translate(str.maketrans("1234", "2143"))
        cursor.insertText(new)
        self._force_update()

    # ------------------------------------------------------------------
    # BPM/OFFSET auto-detect (experimental)
    # ------------------------------------------------------------------
    def auto_detect_bpm_offset(self):
        if not self.current_file:
            QMessageBox.information(self, "確認", "先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            return
        headers = parse_preview_headers(self.editor.toPlainText())
        wave = headers["wave"]
        if not wave:
            QMessageBox.information(self, "確認", "WAVE:に音源ファイルが指定されていません。")
            return
        wave_path = os.path.join(os.path.dirname(self.current_file), wave)
        if not os.path.exists(wave_path):
            QMessageBox.warning(self, "確認", f"音源ファイルが見つかりません: {wave}")
            return
        self.status_label.setText("BPM/OFFSETを自動検出中(実験的)...")
        self._bpm_detect_worker = BpmOffsetDetectWorker(wave_path, self)
        self._bpm_detect_worker.detected.connect(self._on_auto_detect_bpm_offset_ok)
        self._bpm_detect_worker.failed.connect(self._on_auto_detect_bpm_offset_failed)
        self._bpm_detect_worker.start()

    def _on_auto_detect_bpm_offset_ok(self, bpm, offset):
        self.replace_header_line("BPM", f"{bpm:g}")
        self.replace_header_line("OFFSET", f"{offset:.3f}")
        self.status_label.setText(f"BPM/OFFSETを自動検出しました(実験的): BPM {bpm:g} / OFFSET {offset:.3f}")
        self._force_update()

    def _on_auto_detect_bpm_offset_failed(self, msg):
        self.status_label.setText(f"BPM/OFFSET自動検出に失敗しました(実験的): {msg}")

    # ------------------------------------------------------------------
    # AI譜面生成 (experimental) - always writes a separate <basename>(AI).tja
    # rather than touching the currently open file/editor, so a rough or
    # outright bad generation result can never clobber real work.
    # ------------------------------------------------------------------
    def open_auto_chart_generator(self):
        if not self.current_file:
            QMessageBox.information(self, "確認", "先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            return
        content = self.editor.toPlainText()
        headers = parse_preview_headers(content)
        wave = headers["wave"]
        if not wave:
            QMessageBox.information(self, "確認", "WAVE:に音源ファイルが指定されていません。")
            return
        wave_path = os.path.join(os.path.dirname(self.current_file), wave)
        if not os.path.exists(wave_path):
            QMessageBox.warning(self, "確認", f"音源ファイルが見つかりません: {wave}")
            return

        from neotja.dialogs.auto_chart_dialog import AutoChartDialog

        cursor_line = self.editor.textCursor().blockNumber() + 1

        def apply(course_key, generated_body):
            self._save_ai_chart_variant(content, course_key, generated_body)

        AutoChartDialog(self, content, wave_path, cursor_line, apply).exec()

    def _write_ai_variant_file(self, content: str, course_key: str, generated_body: str, base_path: str):
        """Builds and writes the `<basename>(AI).tja` variant - no dialogs,
        no interaction with the live editor. Returns the path written, or
        None if `course_key` doesn't exist in `content`. Shared by both the
        interactive toolbar dialog (_save_ai_chart_variant, below) and the
        silent post-creation step in _apply_youtube_project - the latter
        runs unattended in the background, so it must never pop a modal
        dialog to report its result."""
        course_range = self.analyzer.course_line_range(content, course_key)
        if course_range is None:
            return None
        new_content = build_ai_variant_content(content, course_range, generated_body)
        base, ext = os.path.splitext(base_path)
        new_path = f"{base}(AI){ext}"
        # errors="replace" だと cp932 に無い文字(元ファイルのTITLE等に含まれる
        # 絵文字など)が無言で「?」に化けるので strict のまま書き、
        # 呼び出し側に UnicodeEncodeError として伝える。
        with open(new_path, "w", encoding="cp932") as f:
            f.write(new_content)
        return new_path

    def _save_ai_chart_variant(self, content: str, course_key: str, generated_body: str, base_path: str = None):
        """Interactive save used by the toolbar's AI譜面生成 dialog - confirms
        overwrite and reports success/failure via QMessageBox."""
        base_path = base_path or self.current_file
        base, ext = os.path.splitext(base_path)
        prospective_path = f"{base}(AI){ext}"
        if os.path.exists(prospective_path):
            ans = QMessageBox.question(
                self, "確認", f"既にファイルが存在します。上書きしますか?\n{prospective_path}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        try:
            new_path = self._write_ai_variant_file(content, course_key, generated_body, base_path)
        except UnicodeEncodeError:
            QMessageBox.critical(
                self, "保存エラー",
                "ANSI (cp932) で表せない文字が含まれているため保存できませんでした。\n"
                "TITLE などに絵文字や特殊な文字が入っていないか確認してください。")
            return
        except OSError as e:
            QMessageBox.critical(self, "保存エラー", str(e))
            return
        if new_path is None:
            QMessageBox.warning(self, "エラー", "対象コースが見つかりませんでした。")
            return
        QMessageBox.information(self, "AI譜面生成(実験的)", f"生成しました:\n{new_path}\n\n現在開いているファイルは変更されていません。")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def toggle_theme(self):
        current = self.config_data.get("theme", "dark")
        self.config_data["theme"] = "light" if current == "dark" else "dark"
        settings_mod.save_settings(self.config_data)
        apply_theme(self.app, self.config_data["theme"])
        self.highlighter.rebuild_formats()
        self.highlighter.rehighlight()
        self.editor.gutter.update()
        self.ruler.update()
        self.preview_dock.refresh_theme()

    def _show_about(self):
        QMessageBox.information(self, "バージョン情報", f"{APP_NAME}\nVersion: {VERSION}\n\nRedesigned & Optimized Edition (PySide6)")

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------
    def _report_failed_update(self):
        """Tell the user when the previous run's update didn't actually get
        applied. Without this the app just relaunched on the old version with
        no explanation, which read as "the updater does nothing"."""
        from neotja.updater import pop_update_error

        info = pop_update_error()
        if not info:
            return
        update_exe = ""
        for line in info.splitlines():
            if line.startswith("update_exe="):
                update_exe = line.split("=", 1)[1].strip()

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("更新に失敗しました")
        box.setText(
            f"更新ファイルの適用に失敗したため、v{VERSION} のままです。\n\n"
            "NeoTJAEditor.exe を上書きできませんでした。\n"
            "以下が原因として考えられます:\n"
            "・ウイルス対策ソフトが更新ファイルをブロックしている\n"
            "・exeが Program Files など書き込み権限のない場所にある\n\n"
            "ダウンロード済みの更新ファイルは残してあるので、"
            "手動で上書きすれば更新できます。"
        )
        box.setDetailedText(info)
        if update_exe and os.path.exists(update_exe):
            box.addButton("更新ファイルの場所を開く", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked and clicked.text().startswith("更新ファイル"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(update_exe)])

    def check_for_updates(self, manual=False):
        from neotja.updater import UpdateCheckWorker

        worker = UpdateCheckWorker(self)
        worker.update_available.connect(lambda tag, notes, url: self._prompt_update(tag, notes, url))
        if manual:
            worker.up_to_date.connect(lambda: QMessageBox.information(self, "更新の確認", f"現在のバージョン v{VERSION} は最新です。"))
            worker.failed.connect(lambda msg: QMessageBox.warning(self, "更新の確認", f"更新の確認に失敗しました:\n{msg}"))
        self._update_check_worker = worker
        worker.start()

    def _prompt_update(self, tag, notes, asset_url):
        box = QMessageBox(self)
        box.setWindowTitle("新しいバージョンがあります")
        box.setText(f"新しいバージョン {tag} が利用可能です。(現在: v{VERSION})\n\n更新しますか？")
        if notes:
            box.setDetailedText(notes)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return

        if not getattr(sys, "frozen", False):
            import webbrowser
            from neotja.updater import RELEASES_PAGE_URL
            QMessageBox.information(self, "更新", "ソースから実行中のため自動更新はできません。リリースページを開きます。")
            webbrowser.open(RELEASES_PAGE_URL)
            return

        if not asset_url:
            QMessageBox.warning(self, "更新エラー", "ダウンロード用のファイルが見つかりませんでした。")
            return
        self._run_update_download(asset_url)

    def _run_update_download(self, asset_url):
        from neotja.updater import UpdateDownloadWorker, apply_update

        progress = QProgressDialog("更新をダウンロード中...", "キャンセル", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        worker = UpdateDownloadWorker(asset_url, self)

        def on_progress(pct):
            if pct >= 0:
                progress.setRange(0, 100)
                progress.setValue(pct)
            else:
                progress.setRange(0, 0)

        def on_ok(path):
            progress.close()
            # Settle the unsaved-changes question BEFORE arming the batch:
            # apply_update() starts a script that busy-waits for this PID to
            # exit. If closeEvent then vetoed the exit (Cancel is the default
            # button in _unsaved_check), that script would spin forever and
            # later overwrite+relaunch the app the next time it was closed -
            # which looked exactly like "the update silently does nothing".
            if not self._unsaved_check():
                QMessageBox.information(
                    self, "更新",
                    "更新を中止しました。\n次回起動時に再度お知らせします。",
                )
                return
            self._updating = True
            apply_update(path)
            self.close()

        def on_failed(msg):
            progress.close()
            QMessageBox.warning(self, "更新エラー", f"ダウンロードに失敗しました:\n{msg}")

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(progress.close)
        # Cooperative cancel (a flag checked between chunks), NOT QThread.
        # terminate() - a forced kill mid-write could corrupt interpreter state
        # or leave the fixed dest exe locked, breaking the next update attempt.
        progress.canceled.connect(worker.cancel)
        self._update_download_worker = worker
        worker.start()
        progress.exec()

    # ------------------------------------------------------------------
    # Dialogs wired up in a later pass (Phase 1 dialog port)
    # ------------------------------------------------------------------
    def _not_yet_available(self, name):
        QMessageBox.information(self, name, f"{name} は準備中です。")

    def open_help(self):
        from neotja.dialogs.help_window import HelpWindow
        HelpWindow(self).exec()

    def open_scroll_splitter(self):
        cursor, txt = self._get_selection()
        if txt is None:
            return
        from neotja.dialogs.highspeed_dialog import HighSpeedDialog

        def apply(new_text):
            cursor.insertText(new_text + "\n")
            self._force_update()
        HighSpeedDialog(self, txt, apply).exec()

    def open_measure_converter(self):
        cursor, txt = self._get_selection()
        if txt is None:
            return
        from neotja.dialogs.measure_convert_dialog import MeasureConvertDialog

        def apply(new_text):
            cursor.insertText(new_text + "\n")
            self._force_update()
        MeasureConvertDialog(self, txt, apply).exec()

    def open_image_exporter(self):
        content = self.editor.toPlainText()
        if not self.courses_info:
            QMessageBox.warning(self, "警告", "有効なコースが見つかりません。")
            return
        target_label = self.courses_info[0]["label"]
        from neotja.dialogs.image_preview_dialog import TJAImagePreviewDialog
        TJAImagePreviewDialog(self, content, target_label).exec()

    def open_strobe_tool(self):
        cursor = self.editor.textCursor()
        pos = cursor.position()
        text_before_cursor = self.editor.toPlainText()[:pos]
        lines = text_before_cursor.split("\n")
        current_bpm = "120"
        full_text = self.editor.toPlainText().split("\n")
        for line in full_text:
            if line.startswith("BPM:"):
                current_bpm = line[4:].strip()
                break
        for line in reversed(lines):
            match = re.search(r"#BPMCHANGE\s+([0-9.]+)", line)
            if match:
                current_bpm = match.group(1)
                break

        from neotja.dialogs.strobe_dialog import StrobeGeneratorDialog

        def apply(new_text):
            self.editor.insert_at_cursor(new_text + "\n")
            self._force_update()
        StrobeGeneratorDialog(self, current_bpm, apply).exec()

    def open_settings(self):
        from neotja.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        if dlg.exec():
            settings_mod.save_settings(self.config_data)
            apply_theme(self.app, self.config_data.get("theme", "dark"))
            self.highlighter.rebuild_formats()
            self.highlighter.rehighlight()
            self.editor.set_mono_font(self.config_data.get("font_family", "Consolas"), self.config_data.get("font_size", 12))
            self.preview_dock.set_hit_sound_files(
                self.config_data.get("hit_sound_don_path", ""), self.config_data.get("hit_sound_ka_path", ""),
            )
            self.preview_dock.refresh_theme()
            self.preview_dock.set_se_text_enabled(self.config_data.get("se_text_enabled", True))
            self.roll_speed_spin.setValue(self.config_data.get("roll_speed", 45))
            self.btn_auto_save.blockSignals(True)
            self.btn_auto_save.setChecked(self.config_data.get("auto_save_enabled", False))
            self.btn_auto_save.blockSignals(False)
            self._sync_auto_save_button(self.btn_auto_save.isChecked())
            for k, action in self._run_actions.items():
                if k == "F1":
                    continue  # fixed label - not sourced from run_config
                action.setText(f"{k}: {self.config_data['run_config'][k]['name']}")
            self.editor.gutter.update()
            self.ruler.update()
            self._force_update()
