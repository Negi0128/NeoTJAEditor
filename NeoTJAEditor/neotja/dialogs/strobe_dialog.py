import math

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from fractions import Fraction

from neotja import theme as theme_mod
from neotja.easing import curve_value

CURVES = ("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)")

# 小節の刻み方(利用者の指定 2026-11-15)。
# TNDE(120fps)では 1小節 = 1フレームにすると線がぶれ、1ms 前後で刻むと止まって
# 見えた。分母の大きい分数(119/20000 など)は TNDE の fps を落とすので、
# TNDE 向けの2つは 1/N の形だけを使う。
MODE_DRAW = "描画優先"
MODE_PERF = "パフォーマンス"
MODE_THEORY = "理論値"
MODES = (MODE_DRAW, MODE_PERF, MODE_THEORY)
MODE_NOTES = {
    MODE_DRAW: "小節を約1msの細かさで刻みます。TNDEで動作を想定して計算されます。",
    MODE_PERF: "小節を1〜1.28msの細かさで刻み、描画優先より小節線を減らします。"
               "若干見た目が異なりますが、その分小節線の数が少なくなります。"
               "TNDEで動作を想定して計算されます。",
    MODE_THEORY: "1小節を1フレームの長さにします。NeoTJAEditor では完全に止まりますが、"
                 "シミュレーターによっては線がぶれて見えることがあります。",
}


def _same_scroll(a, b):
    """2つの #SCROLL の値が同じか。字づらが違っても数として同じなら同じ
    ("1" と "1.000" など)。読めない物は違う扱いにする(書いておく)。"""
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def draw_divisor(bpm):
    """描画優先の N。1/N 小節の長さ 240000/(BPM×N) ms が 1ms に一番近い N。"""
    return max(1, math.floor(Fraction(240000) / bpm + Fraction(1, 2)))


def perf_divisor(bpm):
    """パフォーマンスの N。長さが 1.28ms 以下になる一番小さい N。
    1ms 未満になってしまうときは描画優先の N にする。"""
    # 240000/(BPM×N) <= 32/25 ⇔ N >= 187500/BPM
    n = max(1, math.ceil(Fraction(187500) / bpm))
    if Fraction(240000) / (bpm * n) >= 1:
        return n
    return draw_divisor(bpm)


def strobe_unit(mode, bpm, fps):
    """1本ぶんの小節の長さ(全音符 = 1)。bpm は Fraction。"""
    if mode == MODE_THEORY:
        # 1小節をちょうど1フレームにする。#MEASURE a/b の長さは
        # (240 / BPM) × a/b 秒なので、1/FPS 秒にするには a/b = BPM / (240×FPS)。
        return bpm / (240 * fps)
    if mode == MODE_PERF:
        return Fraction(1, perf_divisor(bpm))
    return Fraction(1, draw_divisor(bpm))


def build_strobe(mode, bpm, fps, total, start, end, curve, prec,
                 restore_measure="4/4", restore_scroll="1.000"):
    """ストロボの字を作る。bpm と total は Fraction。"""
    unit = strobe_unit(mode, bpm, fps)
    count = int(total // unit)
    # 1本の長さで割り切れない端数。最後に短い小節を1つ足して、ストロボ全体を
    # 指定の長さぴったりにする(足さないと、後ろの譜面がずれる)。
    rem = total - count * unit
    lines_count = count + (1 if rem else 0)

    out = ["// --- ストロボ開始 ---",
           f"#MEASURE {unit.numerator}/{unit.denominator}"]

    # **同じ値が続くときは書かない**(利用者の指定 2026-11-05
    # 「SCROLLの値を固定するストロボを作るとき、同じ値が大量に並ぶ」)。
    # #SCROLL は書いた所から効き続けるので、落としても見え方は同じ。
    last = None
    for i in range(lines_count):
        if rem and i == lines_count - 1:
            out.append(f"#MEASURE {rem.numerator}/{rem.denominator}")
        t = i / (lines_count - 1) if lines_count > 1 else 0.0
        y = curve_value(t, curve)
        val = f"{start + (end - start) * y:.{prec}f}"
        if val != last:
            out.append(f"#SCROLL {val}")
            last = val
        out.append("0,")

    out.append("// --- ストロボ終了 ---")
    out.append(f"#MEASURE {restore_measure}")
    # 元へ戻す1本も、最後に書いた値と同じなら要らない。
    if not _same_scroll(last, restore_scroll):
        out.append(f"#SCROLL {restore_scroll}")
    return "\n".join(out)


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

        self.cb_mode = QComboBox()
        self.cb_mode.addItems(list(MODES))
        self.cb_mode.setCurrentText(MODE_DRAW)
        form.addRow("方式", self.cb_mode)
        self.lbl_mode = QLabel("")
        self.lbl_mode.setWordWrap(True)
        self.lbl_mode.setStyleSheet(
            "color: %s; font-size: 11px;" % theme_mod.COLORS.get("fg_dim", "#6c757d"))
        form.addRow(self.lbl_mode)

        self.cb_fps = QComboBox()
        self.cb_fps.addItems(["60", "120", "144", "240"])
        self.cb_fps.setCurrentText("120")
        self.cb_fps.setToolTip("理論値のときだけ使います")
        form.addRow("再生シミュレーターFPS", self.cb_fps)

        self.ed_bpm = QLineEdit(str(initial_bpm))
        form.addRow("基準BPM", self.ed_bpm)

        self.cb_length = QComboBox()
        # 1小節より長いストロボも作れるように 2〜8小節を、短いほうに 1/16小節を
        # 足した(利用者の指定 2026-11-14)。
        self.cb_length.addItems(
            ["1/16小節", "1/8小節", "1/4小節", "1/2小節"]
            + ["%d小節" % n for n in range(1, 9)])
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
        # 値を入れてすぐ Enter で挿入できるように(利用者の要望
        # 2026-11-07。ハイスピ変換と同じ)。
        from neotja.dialogs import set_default_button
        set_default_button(self, btn_apply)
        for ed in (self.ed_bpm, self.ed_start, self.ed_end):
            ed.returnPressed.connect(self._apply)

        self.cb_mode.currentTextChanged.connect(self._mode_changed)
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

        self._mode_changed()

    def _mode_changed(self, *_):
        mode = self.cb_mode.currentText()
        self.lbl_mode.setText(MODE_NOTES.get(mode, ""))
        # FPS を使うのは理論値だけ。
        self.cb_fps.setEnabled(mode == MODE_THEORY)
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

        # 生成する長さ(全音符 = 1 の単位。4/4 の1小節 = 1)。
        # 項目の字(「1/16小節」「3小節」など)から読む。
        try:
            total = Fraction(self.cb_length.currentText().replace("小節", ""))
        except (ValueError, ZeroDivisionError):
            total = Fraction(1)
        if total <= 0:
            total = Fraction(1)

        # BPM は欄の字のまま分数で使う(173.5 なども誤差なし)。
        try:
            bpm_q = Fraction(self.ed_bpm.text().strip())
        except (ValueError, ZeroDivisionError):
            return

        self.txt_after.setPlainText(build_strobe(
            self.cb_mode.currentText(), bpm_q, fps, total, s, e,
            self.cb_curve.currentText(), p,
            self._restore_measure, self._restore_scroll))

    def _apply(self):
        t = self.txt_after.toPlainText().strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.accept()
