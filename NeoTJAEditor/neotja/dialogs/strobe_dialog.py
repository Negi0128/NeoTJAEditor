from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from neotja.easing import curve_value

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")


class StrobeGeneratorDialog(QDialog):
    def __init__(self, main_window, initial_bpm, apply_cb, parent=None):
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        self.setWindowTitle("ストロボ生成")
        self.resize(600, 700)

        form = QFormLayout()

        self.cb_fps = QComboBox()
        self.cb_fps.addItems(["60", "120", "144", "240"])
        self.cb_fps.setCurrentText("120")
        form.addRow("再生シミュレーターFPS", self.cb_fps)

        self.ed_bpm = QLineEdit(str(initial_bpm))
        form.addRow("基準BPM", self.ed_bpm)

        self.cb_length = QComboBox()
        self.cb_length.addItems(["1/8小節", "1/4小節", "1/2小節", "1小節"])
        self.cb_length.setCurrentText("1小節")
        form.addRow("生成長さ", self.cb_length)

        self.ed_start = QLineEdit("90.0")
        form.addRow("開始 SCROLL", self.ed_start)
        self.ed_end = QLineEdit("90.0")
        form.addRow("終了 SCROLL", self.ed_end)

        self.cb_curve = QComboBox()
        self.cb_curve.addItems(list(CURVES))
        form.addRow("変化カーブ", self.cb_curve)

        self.sp_prec = QSpinBox()
        self.sp_prec.setRange(1, 5)
        self.sp_prec.setValue(3)
        form.addRow("小数点以下", self.sp_prec)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("▸ プレビュー"))

        self.txt_after = QPlainTextEdit()
        layout.addWidget(self.txt_after, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_apply = QPushButton("エディタに挿入")
        btn_apply.setObjectName("accentButton")
        btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

        for w, sig in (
            (self.cb_fps, self.cb_fps.currentTextChanged),
            (self.ed_bpm, self.ed_bpm.textChanged),
            (self.cb_length, self.cb_length.currentTextChanged),
            (self.ed_start, self.ed_start.textChanged),
            (self.ed_end, self.ed_end.textChanged),
            (self.cb_curve, self.cb_curve.currentTextChanged),
            (self.sp_prec, self.sp_prec.valueChanged),
        ):
            sig.connect(self._preview)

        self._preview()

    def _preview(self, *_):
        try:
            fps = int(self.cb_fps.currentText())
            bpm = float(self.ed_bpm.text())
            if bpm <= 0:
                return
            s = float(self.ed_start.text())
            e = float(self.ed_end.text())
            p = int(self.sp_prec.value())
        except ValueError:
            return

        curve = self.cb_curve.currentText()
        length_str = self.cb_length.currentText()
        if length_str == "1/8小節":
            fraction = 1 / 8
        elif length_str == "1/4小節":
            fraction = 1 / 4
        elif length_str == "1/2小節":
            fraction = 1 / 2
        else:
            fraction = 1.0

        measure_den = 0
        for n in range(1, 10000):
            x = (n * 240 * fps) / bpm
            if abs(x - round(x)) < 1e-6:
                measure_den = int(round(x))
                break

        if measure_den == 0:
            self.txt_after.setPlainText("エラー: 適切なMEASUREが算出できません。")
            return

        lines_count = int(measure_den * fraction)
        if lines_count <= 0:
            lines_count = 1

        out = []
        out.append(f"// --- ストロボ開始 (BPM{bpm:g}, {fps}fps) ---")
        out.append(f"#MEASURE 1/{measure_den}")

        for i in range(lines_count):
            t = i / (lines_count - 1) if lines_count > 1 else 0.0
            y = curve_value(t, curve)
            val = f"{s + (e - s) * y:.{p}f}"
            out.append(f"#SCROLL {val}")
            out.append("0,")

        out.append("// --- ストロボ終了 ---")
        out.append("#MEASURE 4/4")
        out.append("#SCROLL 1.000")

        self.txt_after.setPlainText("\n".join(out))

    def _apply(self):
        t = self.txt_after.toPlainText().strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.accept()
