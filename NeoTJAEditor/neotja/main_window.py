import os
import re
import shutil
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressDialog,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolBar, QVBoxLayout, QWidget,
)

from neotja import settings as settings_mod
from neotja.constants import APP_NAME, NEW_FILE_TEMPLATE, VERSION
from neotja.editor_widget import TJAEditor
from neotja.theme import COLORS
from neotja.highlighter import TJAHighlighter, compute_highlight_data
from neotja.preview_dock import PreviewDock
from neotja.ruler_widget import RulerWidget
from neotja.theme import apply_theme
from neotja.tja_analyzer import TJACourseAnalyzer


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
        self._heavy_timer = QTimer(self)
        self._heavy_timer.setSingleShot(True)
        self._heavy_timer.timeout.connect(self._heavy_tasks)

        self._metronome_timer = QTimer(self)
        self._metronome_timer.setSingleShot(True)
        self._metronome_timer.timeout.connect(self._update_metronome_schedule)

        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
        self.resize(1280, 820)

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

        self.new_file(confirm=False)

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
        self._toolbar_button(self.toolbar_top, "元に戻す", self.editor.undo)
        self._toolbar_button(self.toolbar_top, "やり直す", self.editor.redo)
        self._toolbar_button(self.toolbar_top, "譜面画像生成(試験的)", self.open_image_exporter)
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
        self.setCentralWidget(splitter)

    def _build_preview_dock(self):
        self.preview_dock = PreviewDock(
            lambda v: self.replace_header_line("BPM", v),
            lambda v: self.replace_header_line("OFFSET", v),
            self,
            seek_cursor_cb=self._cursor_preview_seconds,
            volume_cb=self._save_preview_volume,
            duration_ready_cb=self._update_metronome_schedule,
            expanded_changed_cb=self._on_preview_expanded_changed,
        )
        self.addDockWidget(Qt.BottomDockWidgetArea, self.preview_dock)
        self.preview_dock.set_volume(self.config_data.get("preview_volume", 0.8))
        # No close/float/move features: the dock itself always stays docked
        # and visible. Its collapse/expand toggle lives in the status bar
        # (next to the theme switcher), so there's never a state where it
        # vanishes with no obvious way back.
        self.preview_dock.setFeatures(self.preview_dock.DockWidgetFeature(0))

    def _save_preview_volume(self, volume: float):
        self.config_data["preview_volume"] = volume
        settings_mod.save_settings(self.config_data)

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

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)

    def _on_preview_toggle_clicked(self, checked):
        self.preview_dock.set_expanded(checked)
        self.btn_preview_toggle.setText("▼ プレビュー" if checked else "▲ プレビュー")

    def _on_preview_expanded_changed(self, expanded):
        self.btn_preview_toggle.setChecked(expanded)

    def _build_menu(self):
        mb = self.menuBar()

        fm = mb.addMenu("ファイル")
        fm.addAction("新規作成", self.new_file_dialog)
        fm.addAction("開く", self.open_file)
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

        rm = mb.addMenu("起動")
        self._run_actions = {}
        for k in ("F1", "F2", "F3"):
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

        for i, k in enumerate(("F1", "F2", "F3"), start=0):
            sc = QShortcut(QKeySequence(k), self)
            sc.activated.connect(lambda key=k: self.run_simulator(key))

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
        # On large real-world charts, one heavy pass (full re-highlight +
        # course analysis) can take 100ms+, so a longer debounce keeps it
        # from re-triggering on every short pause while actively typing.
        self._heavy_timer.start(600)

    def _force_update(self):
        self._heavy_timer.stop()
        self._heavy_tasks()

    def _heavy_tasks(self):
        content = self.editor.toPlainText()
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
        self.preview_dock.refresh_from_content(content, self.current_file, metronome_clicks)

    def _update_metronome_schedule(self):
        # Lighter-weight than _heavy_tasks: just re-picks which course's
        # #MEASURE/#BPMCHANGE the metronome should follow when the cursor
        # moves into a different course, without re-running the expensive
        # syntax highlighter over the whole document.
        content = self.editor.toPlainText()
        cursor_line = self.editor.textCursor().blockNumber() + 1
        clicks = self.analyzer.build_metronome_clicks(content, cursor_line, self.preview_dock.duration_seconds())
        self.preview_dock.set_metronome_clicks(clicks)

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
        msg = f"  行 {line}  文字数 {col}  │  ANSI (CP932)"
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
            self.save_file()
            return True
        return ans == QMessageBox.No

    def new_file(self, confirm=True):
        if confirm and not self._unsaved_check():
            return
        self.editor.setPlainText(NEW_FILE_TEMPLATE)
        self.current_file = None
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
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
        self._apply_youtube_project(dlg.result_title, dlg.result_subtitle, dlg.result_wave_path, dlg.result_folder)

    def _apply_youtube_project(self, title, subtitle, wave_path, folder):
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', title).strip() or "untitled"

        # Give every new song its own <TITLE>/ folder, with the .tja and the
        # .ogg both named to match TITLE (rather than the raw video title
        # yt-dlp downloaded it as).
        song_folder = os.path.join(folder, safe_name)
        os.makedirs(song_folder, exist_ok=True)
        wave_name = f"{safe_name}.ogg"
        new_wave_path = os.path.join(song_folder, wave_name)
        if os.path.abspath(wave_path) != os.path.abspath(new_wave_path):
            shutil.move(wave_path, new_wave_path)

        content = NEW_FILE_TEMPLATE.replace("TITLE:\n", f"TITLE:{title}\n", 1)
        content = content.replace("SUBTITLE:--\n", f"SUBTITLE:{subtitle}\n" if subtitle else "SUBTITLE:--\n", 1)
        content = content.replace("WAVE:\n", f"WAVE:{wave_name}\n", 1)

        tja_path = os.path.join(song_folder, f"{safe_name}.tja")

        self.editor.setPlainText(content)
        self.current_file = tja_path
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}  —  {os.path.basename(tja_path)}")
        self.save_file()
        self._force_update()

        self.preview_dock.expand()

    def open_file(self):
        if not self._unsaved_check():
            return
        path, _ = QFileDialog.getOpenFileName(self, "開く", "", "TJA Files (*.tja);;Text Files (*.txt)")
        if not path:
            return
        try:
            with open(path, "r", encoding="cp932") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            QMessageBox.information(
                self, "文字コード変換",
                "UTF-8で保存されたファイルを読み込みました。\n次回保存時に自動的にANSI形式で保存されます。",
            )

        self.editor.setPlainText(content)
        self.current_file = path
        self.editor.modified_lines.clear()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}  —  {os.path.basename(path)}")
        self._force_update()

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
        if not self.current_file:
            self.save_file_as()
            return
        try:
            with open(self.current_file, "w", encoding="cp932", errors="replace") as f:
                f.write(self.editor.toPlainText())
            self.editor.modified_lines.clear()
            self.editor.gutter.update()
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", "", "TJA Files (*.tja);;All Files (*.*)")
        if path:
            if not path.lower().endswith(".tja") and "." not in os.path.basename(path):
                path += ".tja"
            self.current_file = path
            self.setWindowTitle(f"{APP_NAME}  v{VERSION}  —  {os.path.basename(path)}")
            self.save_file()

    def open_new_window(self):
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])])

    def closeEvent(self, event):
        if self._unsaved_check():
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

    def _show_about(self):
        QMessageBox.information(self, "バージョン情報", f"{APP_NAME}\nVersion: {VERSION}\n\nRedesigned & Optimized Edition (PySide6)")

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------
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
            apply_update(path)
            self.close()

        def on_failed(msg):
            progress.close()
            QMessageBox.warning(self, "更新エラー", f"ダウンロードに失敗しました:\n{msg}")

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_failed)
        progress.canceled.connect(worker.terminate)
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
            self.roll_speed_spin.setValue(self.config_data.get("roll_speed", 45))
            for k, action in self._run_actions.items():
                action.setText(f"{k}: {self.config_data['run_config'][k]['name']}")
            self.editor.gutter.update()
            self.ruler.update()
            self._force_update()
