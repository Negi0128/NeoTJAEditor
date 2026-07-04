from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from neotja.measure_math import parse_measure_lines, render_converted, valid_targets, wrap_options


class MeasureConvertDialog(QDialog):
    def __init__(self, main_window, initial_text, apply_cb, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.apply_cb = apply_cb
        self.setWindowTitle("ノーツ間隔リサイズ")
        self.resize(580, 680)

        self.parsed = parse_measure_lines(initial_text)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("▸ 変換前"))
        txt_before = QPlainTextEdit(initial_text)
        txt_before.setReadOnly(True)
        txt_before.setFixedHeight(120)
        layout.addWidget(txt_before)

        form = QFormLayout()
        self.cb_target = QComboBox()
        form.addRow("変換後の桁数", self.cb_target)
        self.cb_wrap = QComboBox()
        form.addRow("折り返し文字数", self.cb_wrap)
        layout.addLayout(form)

        layout.addWidget(QLabel("▸ 変換後プレビュー"))
        self.txt_after = QPlainTextEdit()
        layout.addWidget(self.txt_after, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_apply = QPushButton("エディタに適用")
        btn_apply.setObjectName("accentButton")
        btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

        self.cb_target.currentTextChanged.connect(self._update_wrap_options)
        self.cb_wrap.currentTextChanged.connect(self._preview)

        self._update_targets()
        self._update_wrap_options()
        self._preview()

    def _update_targets(self):
        use_ext = self.main_window.config_data.get("resize_ext", False)
        valid = [str(t) for t in valid_targets(self.parsed, use_ext)]
        self.cb_target.blockSignals(True)
        self.cb_target.clear()
        self.cb_target.addItems(valid or ["変換不可"])
        self.cb_target.blockSignals(False)

    def _update_wrap_options(self, *_):
        try:
            tgt = int(self.cb_target.currentText())
        except ValueError:
            return
        cfg = self.main_window.config_data
        vals = wrap_options(tgt)
        if tgt % 12 == 0:
            dflt = str(cfg.get("resize_wrap_12", 24))
        elif tgt % 16 == 0:
            dflt = str(cfg.get("resize_wrap_16", 16))
        else:
            dflt = "改行なし"

        self.cb_wrap.blockSignals(True)
        self.cb_wrap.clear()
        self.cb_wrap.addItems(vals)
        self.cb_wrap.setCurrentText(dflt if dflt in vals else vals[0])
        self.cb_wrap.blockSignals(False)
        self._preview()

    def _preview(self, *_):
        try:
            tgt = int(self.cb_target.currentText())
        except ValueError:
            return
        wrap_val = 0
        if self.cb_wrap.currentText() != "改行なし":
            try:
                wrap_val = int(self.cb_wrap.currentText())
            except ValueError:
                pass
        self.txt_after.setPlainText(render_converted(self.parsed, tgt, wrap_val))

    def _apply(self):
        t = self.txt_after.toPlainText().strip()
        if t:
            self.apply_cb(t)
            self.accept()
