from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from neotja.easing import curve_value

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")


class HighSpeedDialog(QDialog):
    def __init__(self, main_window, initial_text, apply_cb, parent=None):
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        self.setWindowTitle("ハイスピ変換")
        self.resize(580, 780)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("▸ 変換前の譜面データ（編集可）"))
        self.txt_before = QPlainTextEdit(initial_text)
        self.txt_before.setFixedHeight(120)
        layout.addWidget(self.txt_before)

        form = QFormLayout()
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["なめらかハイスピ", "ノーツ毎ハイスピ", "特定間隔ハイスピ"])
        form.addRow("変換モード", self.cb_mode)

        self.ed_start = QLineEdit("1.0")
        form.addRow("開始 SCROLL", self.ed_start)
        self.ed_end = QLineEdit("2.0")
        form.addRow("終了 SCROLL", self.ed_end)

        self.cb_curve = QComboBox()
        self.cb_curve.addItems(list(CURVES))
        form.addRow("変化カーブ", self.cb_curve)

        self.sp_prec = QSpinBox()
        self.sp_prec.setRange(1, 5)
        self.sp_prec.setValue(2)
        form.addRow("小数点以下", self.sp_prec)

        self.row_interval = QWidget()
        interval_layout = QFormLayout(self.row_interval)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        self.ed_interval = QLineEdit("8")
        interval_layout.addRow("分割間隔(分音符)", self.ed_interval)
        form.addRow(self.row_interval)

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

        self.txt_before.textChanged.connect(self._preview)
        self.cb_mode.currentTextChanged.connect(self._on_mode_change)
        for w, sig in (
            (self.ed_start, self.ed_start.textChanged),
            (self.ed_end, self.ed_end.textChanged),
            (self.cb_curve, self.cb_curve.currentTextChanged),
            (self.sp_prec, self.sp_prec.valueChanged),
            (self.ed_interval, self.ed_interval.textChanged),
        ):
            sig.connect(self._preview)

        self._on_mode_change()

    def _on_mode_change(self, *_):
        mode = self.cb_mode.currentText()
        self.row_interval.setVisible("特定間隔" in mode)
        self._preview()

    def _preview(self, *_):
        try:
            s = float(self.ed_start.text())
            e = float(self.ed_end.text())
            p = int(self.sp_prec.value())
        except ValueError:
            return

        mode = self.cb_mode.currentText()
        curve = self.cb_curve.currentText()
        raw = self.txt_before.toPlainText().strip()
        notes_str = "".join(c for c in raw if c in "0123456789,")

        if not notes_str:
            return
        out = []

        if "特定間隔" in mode:
            try:
                interval = int(self.ed_interval.text())
            except ValueError:
                return
            try:
                out_str = self._apply_interval_highspeed(raw, interval, s, e, curve, p)
                self.txt_after.setPlainText(out_str)
            except Exception as ex:
                self.txt_after.setPlainText(f"エラー: {str(ex)}")
            return

        if "なめらか" in mode:
            note_list = [c for c in notes_str if c != ","]
            n = len(note_list)
            if n == 0:
                self.txt_after.setPlainText(notes_str)
                return
            ni = 0
            for c in notes_str:
                if c == ",":
                    if out:
                        out[-1] += ","
                    continue
                t = ni / (n - 1) if n > 1 else 0.0
                y = curve_value(t, curve)
                val = f"{s + (e - s) * y:.{p}f}"
                out.append(f"#SCROLL {val}")
                out.append(c)
                ni += 1

        elif "ノーツ毎" in mode:
            active_notes = [c for c in notes_str if c in "12345679"]
            n = len(active_notes)
            if n == 0:
                self.txt_after.setPlainText(notes_str)
                return
            ni = 0
            buffer = ""
            for c in notes_str:
                if c in "12345679":
                    if buffer:
                        out.append(buffer)
                        buffer = ""
                    t = ni / (n - 1) if n > 1 else 0.0
                    y = curve_value(t, curve)
                    val = f"{s + (e - s) * y:.{p}f}"
                    out.append(f"#SCROLL {val}")
                    buffer += c
                    ni += 1
                else:
                    buffer += c
            if buffer:
                out.append(buffer)

        self.txt_after.setPlainText("\n".join(out))

    def _apply(self):
        t = self.txt_after.toPlainText().strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.accept()

    def _apply_interval_highspeed(self, raw_text, interval, s, e, curve, p):
        if "," not in raw_text:
            raise ValueError("小節の終端（カンマ）が含まれていません。1小節以上を選択してください。")
        measures = raw_text.split(",")
        out = []
        for i, m_str in enumerate(measures):
            if i == len(measures) - 1 and not m_str.strip():
                continue
            notes = "".join(c for c in m_str if c in "0123456789")
            length = len(notes)
            if length == 0:
                out.append(m_str + ",")
                continue
            chunk_size = max(1, length // interval)
            chunks = [notes[j:j + chunk_size] for j in range(0, length, chunk_size)]
            n = len(chunks)
            m_out = []
            for j, chunk in enumerate(chunks):
                t = j / (n - 1) if n > 1 else 0.0
                y = curve_value(t, curve)
                val = f"{s + (e - s) * y:.{p}f}"
                m_out.append(f"#SCROLL {val}")
                m_out.append(chunk)
            out.append("\n".join(m_out) + ",")
        return "\n".join(out)
