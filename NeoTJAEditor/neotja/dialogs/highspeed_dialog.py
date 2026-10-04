from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from neotja.easing import curve_value
from neotja.measure_math import parse_measure_lines

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")


class HighSpeedDialog(QDialog):
    def __init__(self, main_window, initial_text, apply_cb, parent=None,
                 span=None):
        """span を渡すと、その範囲に入っている音符だけに #SCROLL を入れる。

        形は ((小節の番号, 小節内の割合), (小節の番号, 割合))。小節の番号は
        **渡した本文の中での** 0 始まり、割合は 0.0〜1.0。作譜ペインで選んだ
        音符をそのまま渡せるよう、文字数ではなく割合で受ける(小節の分割数と
        画面のグリッドが違っても、同じ所を指せる)。
        """
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        self._span = span
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

        # 終わりの値(例 2.00)を**どこに届かせるか**。
        #
        #   最後の音符に合わせる … 選んだ範囲の最後の音符が終わりの値になる。
        #                         1111, を 1→2 なら 1 / 1.33 / 1.67 / 2.00
        #   範囲の次に合わせる   … 範囲の**直後**が終わりの値になる。つまり
        #                         1 / 1.25 / 1.50 / 1.75 と刻んで、次の小節の
        #                         頭から 2.00 が効く。
        #
        # 「2小節目の頭から 2 にしたい」が多いので後者を足した(利用者の指定
        # 2026-10-04)。以前ここに置いていた「最後の音符の後ろに置く」は
        # **値はそのままで行の位置だけ**動かすもので、狙いが違っていた。
        self.cb_tail = QComboBox()
        self.cb_tail.addItems(["最後の音符に合わせる", "範囲の次に合わせる"])
        self.cb_tail.setToolTip(
            "1111, を 1 → 2 にしたとき\n"
            "  最後の音符に合わせる: 1 / 1.33 / 1.67 / 2.00\n"
            "  範囲の次に合わせる:   1 / 1.25 / 1.50 / 1.75 → 次から 2.00")
        form.addRow("終わりの値", self.cb_tail)

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
            (self.cb_tail, self.cb_tail.currentTextChanged),
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

        try:
            marks = self._marks_for(parsed, mode)
        except ValueError as ex:
            self.txt_after.setPlainText(f"エラー: {str(ex)}")
            return
        if not marks:
            self.txt_after.setPlainText(raw)
            return
        total = len(marks)
        # 終わりの値をどこへ届かせるか(cb_tail の説明を参照)。
        #   最後の音符に合わせる … 刻みは total-1 等分。最後の音符が e。
        #   範囲の次に合わせる   … 刻みは total 等分。**範囲の次**が e。
        tail_next = "次" in self.cb_tail.currentText()
        div = total if tail_next else max(1, total - 1)

        def scroll_at(i):
            t = (i / div) if div > 0 else 0.0
            return f"#SCROLL {s + (e - s) * curve_value(t, curve):.{p}f}"

        # 範囲の次に合わせるときは、最後の音符の後ろへ終わりの値を1本置く。
        # そこが次の小節の頭なので、そこから e が効く。
        tail_line = scroll_at(total) if tail_next else None
        self.txt_after.setPlainText(
            "\n".join(self._render_marks(parsed, marks, scroll_at, tail_line)))

    def _in_span(self, mi, i, length):
        """(小節の番号, 小節内の何文字目) が span の中か。span が無ければ常に真。"""
        sp = self._span
        if not sp:
            return True
        (m0, f0), (m1, f1) = sp
        pos = (mi, (i / length) if length else 0.0)
        eps = 1e-9
        if pos[0] < m0 or (pos[0] == m0 and pos[1] < f0 - eps):
            return False
        if pos[0] > m1 or (pos[0] == m1 and pos[1] > f1 + eps):
            return False
        return True

    def _marks_for(self, parsed, mode):
        """#SCROLL を入れる所 [(小節の番号, 小節内の何文字目)] を順に。

        3つのモードの違いはここだけ。描き出しは _render_marks に一本化した。
        """
        marks = []
        mi = -1
        if "特定間隔" in mode:
            try:
                interval = int(self.ed_interval.text())
            except ValueError:
                raise ValueError("分割間隔は数字で指定してください。")
            if interval <= 0:
                raise ValueError("分割間隔は1以上を指定してください。")
            if not any(m["type"] == "measure" and m["has_comma"] for m in parsed):
                raise ValueError(
                    "小節の終端（カンマ）が含まれていません。1小節以上を選択してください。")
        for item in parsed:
            if item["type"] != "measure":
                continue
            mi += 1
            notes = item["notes"]
            if not notes:
                # 数字が1つも無い小節(「,」だけの行)も1スロットぶんとして
                # 数える。文字が無いと挿す場所が無く、その小節が丸ごと
                # 飛ばされていた("0," と書かないと効かない、という症状)。
                # 「ノーツ毎」では 0 は音符ではないので従来どおり効かない。
                if "なめらか" in mode and self._in_span(mi, 0, 1):
                    marks.append((mi, 0))
                continue
            if "特定間隔" in mode:
                size = max(1, len(notes) // interval)
                idxs = range(0, len(notes), size)
            elif "なめらか" in mode:
                idxs = range(len(notes))
            else:                                  # ノーツ毎
                idxs = [i for i, ch in enumerate(notes) if ch in self._ACTIVE]
            for i in idxs:
                if self._in_span(mi, i, len(notes)):
                    marks.append((mi, i))
        return marks

    @staticmethod
    def _render_marks(parsed, marks, scroll_at, tail_line=None):
        """marks の所へ #SCROLL を入れて組み立て直す。

        tail_line を渡すと、**範囲の最後の音符の後ろ**にその1行を足す。
        「範囲の次に合わせる」で、次の小節の頭から終わりの値を効かせるため。
        """
        order = {pos: k for k, pos in enumerate(marks)}
        last = marks[-1] if marks else None
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

        mi = -1
        for item in parsed:
            if item["type"] == "keep":
                flush()
                out.append(item["text"])
                continue
            mi += 1
            breaks = {}
            for idx, line in item["breaks"]:
                breaks.setdefault(idx, []).append(line)
            notes = item["notes"]
            empty_measure = not notes
            if empty_measure and (mi, 0) in order:
                flush()
                out.append(scroll_at(order[(mi, 0)]))
            for i, ch in enumerate(notes):
                for line in breaks.get(i, ()):
                    flush()
                    out.append(line)
                k = order.get((mi, i))
                if k is not None:
                    flush()
                    out.append(scroll_at(k))
                    buf = ch
                else:
                    buf += ch
                if tail_line is not None and (mi, i) == last:
                    # 範囲の最後の音符の**次**から終わりの値を効かせる。
                    flush()
                    out.append(tail_line)
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
