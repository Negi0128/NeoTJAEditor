from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from fractions import Fraction

from neotja.easing import curve_value

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")


class StrobeGeneratorDialog(QDialog):
    def __init__(self, main_window, initial_bpm, apply_cb, parent=None,
                 restore_measure="4/4", restore_scroll="1.000"):
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        # ストロボの後に戻す拍子。呼び出し側がカーソル手前の #MEASURE を渡す
        # (4/4 決め打ちだと 3/4 等の曲で拍子が化ける)。
        self._restore_measure = restore_measure or "4/4"
        self._restore_scroll = restore_scroll or "1.000"
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
        # 生成する長さ(全音符 = 1 の単位。4/4 の1小節 = 1)。
        total = {
            "1/8小節": Fraction(1, 8), "1/4小節": Fraction(1, 4),
            "1/2小節": Fraction(1, 2),
        }.get(self.cb_length.currentText(), Fraction(1))

        # 1小節をちょうど1フレームにする。#MEASURE a/b の長さは
        # (240 / BPM) × a/b 秒なので、1/FPS 秒にするには a/b = BPM / (240×FPS)。
        # 以前は分子を 1 に決め打ちして「1/整数」になる倍数を探していたため、
        # 240×FPS÷BPM が整数にならない組み合わせ(BPM200 の 144fps、BPM210 は
        # どの FPS でも など)で1小節が1フレームの何分の1かになり、1フレームの
        # 中で SCROLL が何度も切り替わって止まって見えなかった。分数のまま書けば
        # どの BPM でもちょうど1フレームになる(1/整数 になる場合は今までと同じ)。
        try:
            bpm_q = Fraction(self.ed_bpm.text().strip())
        except (ValueError, ZeroDivisionError):
            return
        frame_len = bpm_q / (240 * fps)
        frames = int(total // frame_len)
        # 1フレームで割り切れない端数。最後に短い小節を1つ足して、ストロボ全体を
        # 指定の長さぴったりにする(足さないと、後ろの譜面がずれる)。
        rem = total - frames * frame_len
        lines_count = frames + (1 if rem else 0)

        out = []
        out.append(f"// --- ストロボ開始 (BPM{bpm:g}, {fps}fps) ---")
        out.append(f"#MEASURE {frame_len.numerator}/{frame_len.denominator}")

        for i in range(lines_count):
            if rem and i == lines_count - 1:
                out.append(f"#MEASURE {rem.numerator}/{rem.denominator}")
            t = i / (lines_count - 1) if lines_count > 1 else 0.0
            y = curve_value(t, curve)
            val = f"{s + (e - s) * y:.{p}f}"
            out.append(f"#SCROLL {val}")
            out.append("0,")

        out.append("// --- ストロボ終了 ---")
        out.append(f"#MEASURE {self._restore_measure}")
        out.append(f"#SCROLL {self._restore_scroll}")

        self.txt_after.setPlainText("\n".join(out))

    def _apply(self):
        t = self.txt_after.toPlainText().strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.accept()
