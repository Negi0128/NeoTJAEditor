import re

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from neotja.easing import curve_value
from neotja.measure_math import parse_measure_lines

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")

_CMD = re.compile(r"^\s*#\s*([A-Za-z]+)\s*(.*)$")


def _cmd_of(line):
    m = _CMD.match(str(line))
    return (m.group(1).upper(), m.group(2).strip()) if m else None


def _fmt_scroll(v):
    """開始の初期値の字面。分からなければ 1(TJA の既定)。"""
    if v is None:
        v = 1.0
    t = ("%.3f" % float(v)).rstrip("0").rstrip(".")
    return t or "0"


def _bpm_of(line):
    """#BPMCHANGE の行なら BPM。ほかは None。"""
    c = _cmd_of(line)
    if c is None or c[0] != "BPMCHANGE":
        return None
    try:
        v = float(c[1])
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _meter_of(line):
    """#MEASURE の行なら (分子, 分母)。ほかは None。"""
    c = _cmd_of(line)
    if c is None or c[0] != "MEASURE":
        return None
    parts = str(c[1]).replace(" ", "").split("/")
    if len(parts) != 2:
        return None
    try:
        n, d = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    return (n, d) if n > 0 and d > 0 else None


class HighSpeedDialog(QDialog):
    def __init__(self, main_window, initial_text, apply_cb, parent=None,
                 span=None, base_bpm=None, base_meter=None,
                 base_scroll=None):
        """span を渡すと、その範囲に入っている音符だけに #SCROLL を入れる。

        形は ((小節の番号, 小節内の割合), (小節の番号, 割合))。小節の番号は
        **渡した本文の中での** 0 始まり、割合は 0.0〜1.0。作譜ペインで選んだ
        音符をそのまま渡せるよう、文字数ではなく割合で受ける(小節の分割数と
        画面のグリッドが違っても、同じ所を指せる)。
        """
        super().__init__(parent or main_window)
        self.apply_cb = apply_cb
        self._span = span
        # 範囲の**手前**で効いている値。本文(選んだ小節)だけでは分からない
        # ので、呼ぶ側が渡す。なめらかハイスピが「時間」で刻むのに要る
        # (利用者の指定 2026-10-15)。
        self._base_bpm = float(base_bpm) if base_bpm else 120.0
        self._base_meter = tuple(base_meter or (4, 4))
        self._base_scroll = (float(base_scroll)
                             if base_scroll is not None else None)
        self.setWindowTitle("ハイスピ変換")
        self.resize(580, 780)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("▸ 変換前の譜面データ（編集可）"))
        self.txt_before = QPlainTextEdit(initial_text)
        self.txt_before.setFixedHeight(120)
        layout.addWidget(self.txt_before)

        form = QFormLayout()
        self.cb_mode = QComboBox()
        # 「ノーツ毎」は廃止(利用者の指定 2026-10-15)。なめらかが時間で刻む
        # ようになって、役目が重なったため。
        self.cb_mode.addItems(["なめらかハイスピ", "特定間隔ハイスピ"])
        form.addRow("変換モード", self.cb_mode)

        # 開始は**範囲の手前で効いている値**を初期値にする。そこから
        # 動かさないなら命令を置く意味が無いので、同じ値なら置かない。
        self.ed_start = QLineEdit(_fmt_scroll(self._base_scroll))
        form.addRow("開始 SCROLL", self.ed_start)
        # 終了は空欄で始める(どこまで持っていくかは毎回ちがうため)。
        self.ed_end = QLineEdit("")
        self.ed_end.setPlaceholderText("ここに終わりの値を入れる")
        form.addRow("終了 SCROLL", self.ed_end)

        # 変化カーブの欄は廃止(利用者の指定 2026-10-15)。常に直線。

        self.sp_prec = QSpinBox()
        self.sp_prec.setRange(1, 5)
        self.sp_prec.setValue(3)
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
        # 既定は「最後の音符に合わせる」。「範囲の次」を既定にしていた
        # ところ、終わりの値の1行が余って見える・刻みが 1/N になるのが
        # 分かりにくいという報告があったため戻した(利用者の指定
        # 2026-10-31)。
        self.cb_tail.setCurrentIndex(0)
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
        raw0 = self.txt_before.toPlainText().strip()
        if not self.ed_end.text().strip():
            # 終わりの値がまだ空。何も足さずに元の本文を見せる。
            self.txt_after.setPlainText(raw0)
            return
        try:
            s = float(self.ed_start.text())
            e = float(self.ed_end.text())
            p = int(self.sp_prec.value())
        except ValueError:
            return

        mode = self.cb_mode.currentText()
        raw = raw0
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
        #   最後の音符に合わせる … 最後の音符が e。
        #   範囲の次に合わせる   … **範囲の次**が e。
        tail_next = "次" in self.cb_tail.currentText()

        if "なめらか" in mode:
            # **時間で刻む**(利用者の指定 2026-10-15)。文字数の多い小節が
            # 変化を余計に受け持ってしまうのを直したもの。
            tmap = self._time_map(parsed)
            t0, t_end = self._span_times(tmap)
            at = []
            for mi, i in marks:
                times = tmap[mi][1] if mi < len(tmap) else []
                at.append(times[i] if i < len(times) else t0)
            # 「範囲の次に合わせる」の“次”は、**最後の音符の1つ先**。
            # 範囲の終わり(span)は最後の音符そのものを指しているので、
            # 最後の間隔ぶん伸ばした所を終わりとみなす。等間隔の譜面なら
            # 今までの 1 / 1.25 / 1.5 / 1.75 → 次が 2.0 と同じになる。
            if tail_next:
                if len(at) >= 2:
                    goal = at[-1] + (at[-1] - at[-2])
                elif at and tmap:
                    mi = marks[-1][0]
                    n = max(1, len(tmap[mi][1])) if mi < len(tmap) else 1
                    goal = at[-1] + (tmap[mi][2] / n if mi < len(tmap) else 1.0)
                else:
                    goal = t_end
            else:
                goal = at[-1] if at else t_end
            width = goal - t0

            def ratio(k):
                if k >= total:
                    return 1.0
                if width <= 1e-9:
                    return 0.0
                return max(0.0, min(1.0, (at[k] - t0) / width))
        else:
            div = total if tail_next else max(1, total - 1)

            def ratio(k):
                return (k / div) if div > 0 else 0.0

        def scroll_at(i):
            # 変化は常に直線(カーブの欄は廃止。利用者の指定 2026-10-15)。
            return "#SCROLL %.*f" % (p, s + (e - s) * ratio(i))

        # 範囲の次に合わせるときは、最後の音符の後ろへ終わりの値を1本置く。
        # そこが次の小節の頭なので、そこから e が効く。ただし後ろに譜面が
        # 残っていないなら、何も効かない #SCROLL が末尾にぶら下がるだけ
        # なので置かない(利用者の指摘 2026-10-31)。
        tail_line = (scroll_at(total)
                     if tail_next and self._has_after(parsed, marks[-1])
                     else None)
        lines = self._render_marks(parsed, marks, scroll_at, tail_line)
        self.txt_after.setPlainText("\n".join(self._drop_same(lines, p)))

    @staticmethod
    def _has_after(parsed, last):
        """最後に #SCROLL を置いた所より後ろに、まだ譜面が残っているか。

        残っていなければ「範囲の次」に置くものが無い。
        """
        if not last:
            return False
        mi0, i0 = last
        mi = -1
        for item in parsed:
            if item["type"] != "measure":
                continue
            mi += 1
            if mi < mi0:
                continue
            notes = item["notes"]
            if mi == mi0:
                if notes[i0 + 1:]:
                    return True
            elif notes:
                return True
        return False

    def _drop_same(self, lines, prec):
        """直前と同じ値になる #SCROLL は置かない。

        「開始点の数字が変わらないなら、その命令は意味をなさない」
        (利用者の指定 2026-10-15)。丸めたあとの字面で比べる。範囲の手前で
        効いていた値が分かっていれば、1本目もそれと比べる。
        """
        out = []
        cur = None
        if self._base_scroll is not None:
            cur = "%.*f" % (prec, self._base_scroll)
        n = len(lines)
        for k, line in enumerate(lines):
            c = _cmd_of(line)
            if c is not None and c[0] == "SCROLL":
                # 元からあった #SCROLL のすぐ後ろに新しいものが来るなら、
                # 元のほうは**1音符も効かずに**上書きされる。残さない。
                nxt = _cmd_of(lines[k + 1]) if k + 1 < n else None
                if nxt is not None and nxt[0] == "SCROLL":
                    continue
                if cur is not None and c[1] == cur:
                    continue
                cur = c[1]
            out.append(line)
        return out

    def _time_map(self, parsed):
        """小節ごとの [(頭の時刻, 1文字ぶんの時刻の並び)] を作る。

        戻りは [(小節の頭の時刻, [その小節の各文字の頭の時刻], 小節の長さ)]。
        拍子(#MEASURE)と BPM(#BPMCHANGE)を追いながら積む。小節の途中に
        置かれた #BPMCHANGE は、**その文字から後ろ**だけ刻みが変わる。
        #MEASURE は小節の頭にあるものだけその小節へ当てる(途中のものは
        次の小節から)。単位は秒だが、使うのは比だけ。
        """
        bpm = self._base_bpm
        meter = self._base_meter
        t = 0.0
        out = []
        for item in parsed:
            if item["type"] != "measure":
                # 小節と小節のあいだに置かれた命令(#BPMCHANGE など)は
                # ここに来る。小節の中の命令は item["breaks"]。
                line = item.get("text", "")
                got = _bpm_of(line)
                if got:
                    bpm = got
                got = _meter_of(line)
                if got:
                    meter = got
                continue
            notes = item["notes"]
            n = len(notes)
            breaks = dict()
            for idx, line in item.get("breaks") or []:
                breaks.setdefault(idx, []).append(line)
            # 頭に置かれた #MEASURE はこの小節から効く。
            for line in breaks.get(0, []):
                got = _meter_of(line)
                if got:
                    meter = got
            # 小節まるごとの長さ(秒)。4/4 で 4拍。
            def beat():
                return 60.0 / max(1e-6, bpm)
            full = beat() * 4.0 * meter[0] / float(meter[1])
            start = t
            times = []
            if n <= 0:
                t += full
                out.append((start, times, full))
                continue
            for i in range(n):
                for line in breaks.get(i, []):
                    got = _bpm_of(line)
                    if got:
                        bpm = got
                        full = beat() * 4.0 * meter[0] / float(meter[1])
                times.append(t)
                t += full / n
            # 小節の最後に置かれた命令(位置 n)も読む。
            for line in breaks.get(n, []):
                got = _bpm_of(line)
                if got:
                    bpm = got
                got = _meter_of(line)
                if got:
                    meter = got
            out.append((start, times, t - start))
        return out

    def _span_times(self, tmap):
        """選んだ範囲の (頭の時刻, 終わりの時刻)。範囲が無ければ全体。"""
        if not tmap:
            return 0.0, 0.0
        sp = self._span
        if not sp:
            last = tmap[-1]
            return tmap[0][0], last[0] + last[2]
        (m0, f0), (m1, f1) = sp
        m0 = max(0, min(len(tmap) - 1, int(m0)))
        m1 = max(0, min(len(tmap) - 1, int(m1)))
        t0 = tmap[m0][0] + tmap[m0][2] * max(0.0, min(1.0, float(f0)))
        t1 = tmap[m1][0] + tmap[m1][2] * max(0.0, min(1.0, float(f1)))
        return t0, t1

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
                # 音符が1つも無い小節には何も置かない(利用者の指定
                # 2026-10-15: 小節の頭や音符以外には付けなくてよい)。
                continue
            if "特定間隔" in mode:
                size = max(1, len(notes) // interval)
                idxs = range(0, len(notes), size)
            else:
                # なめらか: **音符の所だけ**。小節の頭・休符・連打の
                # 終端には置かない(利用者の指定 2026-10-15)。
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
