from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from neotja.easing import curve_value
from neotja.measure_math import parse_measure_lines

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

    # ノーツ毎モードで #SCROLL を差し込む対象の文字(休符 0 と終端 8 は除く)。
    _ACTIVE = "12345679"

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
        # 命令行(#BPMCHANGE 等)やコメントを落とさずに小節へ分ける。以前は
        # raw から数字とカンマだけを拾っていたため、`#BPMCHANGE 180` の 180 が
        # そのまま音符3個に化け、命令行自体も消えていた。
        parsed = parse_measure_lines(raw)
        if not any(m["type"] == "measure" and m["notes"] for m in parsed):
            return

        if "特定間隔" in mode:
            try:
                interval = int(self.ed_interval.text())
            except ValueError:
                return
            try:
                self.txt_after.setPlainText(
                    self._render_interval(parsed, interval, s, e, curve, p))
            except Exception as ex:  # noqa: BLE001
                self.txt_after.setPlainText(f"エラー: {str(ex)}")
            return

        if "なめらか" in mode:
            def want(_ch):
                return True
        else:   # ノーツ毎
            def want(ch):
                return ch in self._ACTIVE

        # 数字が1つも無い小節(「,」だけの行)も1スロットぶんとして数える。
        # 文字が無いと下の _render で #SCROLL を挿す場所が無く、その小節が
        # 丸ごと飛ばされていた("0," と書かないと効かない、という症状)。
        # 仮想の1文字を "0" と見なすので、「なめらか」では効き、「ノーツ毎」
        # では(0 は音符ではないので)従来どおり効かない。
        total = sum(1 for m in parsed if m["type"] == "measure"
                    for ch in (m["notes"] or "0") if want(ch))
        if total == 0:
            self.txt_after.setPlainText(raw)
            return

        def scroll_at(i):
            t = i / (total - 1) if total > 1 else 0.0
            return f"#SCROLL {s + (e - s) * curve_value(t, curve):.{p}f}"

        self.txt_after.setPlainText("\n".join(self._render(parsed, want, scroll_at)))

    @staticmethod
    def _render(parsed, want, scroll_at):
        """want(ch) が真のスロットの直前に #SCROLL を入れて組み立てる。
        小節の途中にあった命令行は元の位置のまま残す。"""
        out = []
        buf = ""

        def flush():
            nonlocal buf
            if buf:
                out.append(buf)
                buf = ""

        def append_tail(tail):
            """カンマ/コメントは最後の**音符行**に付ける(命令行には付けない)。"""
            nonlocal buf
            if buf:
                buf += tail
                return
            for j in range(len(out) - 1, -1, -1):
                sj = out[j].strip()
                if sj and not sj.startswith("#") and not sj.startswith("//"):
                    out[j] += tail
                    return
            out.append(tail)

        ni = 0
        for item in parsed:
            if item["type"] == "keep":
                flush()
                out.append(item["text"])
                continue
            breaks = {}
            for idx, line in item["breaks"]:
                breaks.setdefault(idx, []).append(line)
            notes = item["notes"]
            # 数字ゼロの小節は「仮想の1スロット」として扱う。文字は出さず、
            # #SCROLL とカンマだけを自分の行に置く(前の小節の行にカンマを
            # 足すと "0,," になって小節が1つ消えてしまう)。
            empty_measure = not notes
            if empty_measure and want("0"):
                flush()
                out.append(scroll_at(ni))
                ni += 1
            for i, ch in enumerate(notes):
                for line in breaks.get(i, ()):
                    flush()
                    out.append(line)
                if want(ch):
                    flush()
                    out.append(scroll_at(ni))
                    ni += 1
                    buf = ch
                else:
                    buf += ch
            for idx, line in item["breaks"]:
                if idx >= len(notes):
                    flush()
                    out.append(line)
            tail = ("," if item["has_comma"] else "")
            if item["comment"]:
                tail += (" " if tail else "") + item["comment"]
            if tail:
                if empty_measure:
                    flush()
                    out.append(tail)
                else:
                    append_tail(tail)
            flush()
        return out

    @staticmethod
    def _render_interval(parsed, interval, s, e, curve, p):
        """小節を interval 個の塊に割り、塊ごとに #SCROLL を入れる。"""
        if interval <= 0:
            raise ValueError("分割間隔は1以上を指定してください。")
        if not any(m["type"] == "measure" and m["has_comma"] for m in parsed):
            raise ValueError("小節の終端（カンマ）が含まれていません。1小節以上を選択してください。")
        # 塊の境目だけ #SCROLL を入れたいので、want は「塊の先頭かどうか」。
        # 小節ごとに塊の大きさが変わるので、対象スロットを先に集めておく。
        marks = set()
        total = 0
        for mi, item in enumerate(parsed):
            if item["type"] != "measure" or not item["notes"]:
                continue
            length = len(item["notes"])
            size = max(1, length // interval)
            for i in range(0, length, size):
                marks.add((mi, i))
                total += 1

        def scroll_at(i):
            t = i / (total - 1) if total > 1 else 0.0
            return f"#SCROLL {s + (e - s) * curve_value(t, curve):.{p}f}"

        # _render は want(ch) しか見ないので、位置で判定できるよう包み直す。
        out = []
        buf = ""
        ni = 0
        for mi, item in enumerate(parsed):
            if item["type"] == "keep":
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(item["text"])
                continue
            breaks = {}
            for idx, line in item["breaks"]:
                breaks.setdefault(idx, []).append(line)
            for i, ch in enumerate(item["notes"]):
                for line in breaks.get(i, ()):
                    if buf:
                        out.append(buf)
                        buf = ""
                    out.append(line)
                if (mi, i) in marks:
                    if buf:
                        out.append(buf)
                        buf = ""
                    out.append(scroll_at(ni))
                    ni += 1
                buf += ch
            for idx, line in item["breaks"]:
                if idx >= len(item["notes"]):
                    if buf:
                        out.append(buf)
                        buf = ""
                    out.append(line)
            tail = ("," if item["has_comma"] else "")
            if item["comment"]:
                tail += (" " if tail else "") + item["comment"]
            if tail:
                if buf:
                    buf += tail
                else:
                    for j in range(len(out) - 1, -1, -1):
                        sj = out[j].strip()
                        if sj and not sj.startswith("#") and not sj.startswith("//"):
                            out[j] += tail
                            break
                    else:
                        out.append(tail)
            if buf:
                out.append(buf)
                buf = ""
        return "\n".join(out)

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
