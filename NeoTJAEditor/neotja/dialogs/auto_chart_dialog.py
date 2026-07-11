from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSlider, QVBoxLayout,
)

from neotja.audio_engine import ChartGenWorker
from neotja.preview_dock import parse_preview_headers
from neotja.tja_analyzer import TJACourseAnalyzer
from neotja.theme import COLORS


class AutoChartDialog(QDialog):
    """(実験的) 音声のオンセット検出から譜面の下書きを自動生成するダイアログ。
    measure_convert_dialog.py と同じ「設定→生成→プレビュー→適用」の構成だが、
    「適用」はエディタには触れず、常に別ファイル(呼び出し側が
    `<ファイル名>(AI).tja` として保存)を作る点が異なる - 既存の作業内容を
    上書きするリスクを避けるための設計。"""

    def __init__(self, main_window, content: str, wave_path: str, cursor_line: int, apply_cb, parent=None):
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        self.wave_path = wave_path
        self._worker = None
        self._generated_body = None
        self.setWindowTitle("AI譜面生成(実験的)")
        self.resize(560, 620)

        analyzer = TJACourseAnalyzer(main_window.config_data)
        headers = parse_preview_headers(content)
        self.bpm = headers["bpm"] or 120.0
        self.offset = headers["offset"] or 0.0

        preview_data = analyzer.build_preview_timeline(content, cursor_line=cursor_line)
        self._available_courses = preview_data.get("available_courses") or []
        default_course = preview_data.get("course_key")

        layout = QVBoxLayout(self)

        warn = QLabel(
            "(実験的) 元のTJAファイルは変更されません。生成結果は\n"
            "「<ファイル名>(AI).tja」として別ファイルに保存されます。"
        )
        warn.setStyleSheet(f"color: {COLORS['don']}; font-weight: bold;")
        layout.addWidget(warn)

        form = QFormLayout()
        self.cb_course = QComboBox()
        for c in self._available_courses:
            self.cb_course.addItem(c["label"], c["key"])
        if default_course:
            idx = self.cb_course.findData(default_course)
            if idx >= 0:
                self.cb_course.setCurrentIndex(idx)
        form.addRow("対象コース", self.cb_course)

        self.cb_subdivision = QComboBox()
        self.cb_subdivision.addItem("8分", 8)
        self.cb_subdivision.addItem("16分", 16)
        self.cb_subdivision.addItem("32分", 32)
        self.cb_subdivision.setCurrentIndex(1)
        form.addRow("細分化", self.cb_subdivision)

        density_row = QHBoxLayout()
        self.slider_density = QSlider(Qt.Horizontal)
        self.slider_density.setRange(1, 10)
        self.slider_density.setValue(5)
        self.lbl_density = QLabel("5")
        self.slider_density.valueChanged.connect(lambda v: self.lbl_density.setText(str(v)))
        density_row.addWidget(self.slider_density, 1)
        density_row.addWidget(self.lbl_density)
        form.addRow("密度(疎←→密)", density_row)

        form.addRow("BPM / OFFSET", QLabel(f"{self.bpm:g} / {self.offset:.3f} (ヘッダの値を使用)"))
        layout.addLayout(form)

        self.btn_generate = QPushButton("生成")
        self.btn_generate.clicked.connect(self._on_generate)
        layout.addWidget(self.btn_generate)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        layout.addWidget(QLabel("▸ プレビュー"))
        self.txt_preview = QPlainTextEdit()
        self.txt_preview.setReadOnly(True)
        layout.addWidget(self.txt_preview, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("別ファイルとして保存")
        self.btn_apply.setObjectName("accentButton")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_apply)
        layout.addLayout(btn_row)

        if not self._available_courses:
            self.btn_generate.setEnabled(False)
            self.status_label.setText("コースが見つかりません。")

    def _on_generate(self):
        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("読み込み中...")
        self.btn_apply.setEnabled(False)
        self.status_label.setText("音声を解析中(実験的)...")
        self.txt_preview.setPlainText("")

        subdivision = self.cb_subdivision.currentData()
        density = self.slider_density.value() / 10.0
        self._worker = ChartGenWorker(self.wave_path, self.bpm, self.offset, subdivision=subdivision, density=density)
        self._worker.generated.connect(self._on_generated)
        self._worker.failed.connect(self._on_generate_failed)
        self._worker.start()

    def _on_generated(self, body: str):
        self._generated_body = body
        self.btn_generate.setText("生成")
        self.btn_generate.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.status_label.setText("生成しました(実験的 - 精度は粗めです)。")
        self.txt_preview.setPlainText(body)

    def _on_generate_failed(self, msg: str):
        self.btn_generate.setText("生成")
        self.btn_generate.setEnabled(True)
        self.status_label.setText(f"生成に失敗しました: {msg}")

    def _apply(self):
        if not self._generated_body:
            return
        course_key = self.cb_course.currentData()
        self.apply_cb(course_key, self._generated_body)
        self.accept()
