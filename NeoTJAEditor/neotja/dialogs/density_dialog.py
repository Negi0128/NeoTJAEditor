"""密度グラフ(利用者の希望 2026-11-04)。

tja-tools (https://whmhammer.github.io/tja-tools/) にある Density Graph と
同じ読み方 — **小節ごとの棒**を、赤(ドン)の上に青(カッ)を積む。縦は打/秒。

グラフ1枚のために matplotlib を足したくないので(起動が重くなるし exe も
太る)、QPainter で自前に描く。線と帯しか無いので素直に書ける。
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

#: ドン / カッ の色。tja-tools の Density Graph に合わせる。
DON_COLOR = QColor(226, 32, 32)
KA_COLOR = QColor(40, 64, 224)
#: ゴーゴータイムの下地。
GOGO_COLOR = QColor(255, 200, 60, 38)

#: 棒と棒のあいだ。小節が多いと0にする(詰めて描く)。
BAR_GAP = 1.0
#: 目盛りの刻みの候補。最大値がどれに収まるかで選ぶ。
TICK_STEPS = (1, 2, 5, 10, 20, 25, 50, 100)


def measure_density(data):
    """プレビュー用の譜面データ → 小節ごとの [{..}]。

    data は TJACourseAnalyzer.build_preview_timeline の返り値。小節の切れ目は
    "bar_times"(小節1つにつき1件)、音符は "notes" から取る。**連打と風船は
    数えない** — 平均密度と同じ数え方に揃える(食い違うと読む側が混乱する)。

    戻りは [{"start", "dur", "don", "ka", "gogo"}] で、小節の並び順。
    """
    bar_times = data.get("bar_times") or []
    bars = [float(b[0]) for b in bar_times]
    if not bars:
        return []
    # 最後の小節の終わり。bar_times は「始まり」しか持たないので、その小節の
    # BPM と拍子から長さを出す。拍子は #MEASURE の並びから、その時刻までに
    # 効いているものを拾う。
    last_bpm = float(bar_times[-1][1]) or 120.0
    meter = 1.0
    for t, num, den in (data.get("measure_changes") or []):
        if float(t) <= bars[-1] + 1e-9 and den:
            meter = float(num) / float(den)
    end = bars[-1] + (240.0 * meter / last_bpm if last_bpm > 0 else 0.0)
    edges = bars + [end]

    out = [{"start": edges[i], "dur": max(0.0, edges[i + 1] - edges[i]),
            "don": 0, "ka": 0, "gogo": False} for i in range(len(bars))]

    # 音符を小節へ振り分ける。notes は時刻順なので、前から順に送るだけで済む
    # (音符が1万個ある譜面でも、小節ごとに探し直さない)。
    k = 0
    n = len(out)
    for note in (data.get("notes") or []):
        t = float(note[0])
        c = str(note[1])
        while k + 1 < n and t >= edges[k + 1] - 1e-9:
            k += 1
        if c in ("1", "3"):
            out[k]["don"] += 1
        elif c in ("2", "4"):
            out[k]["ka"] += 1

    for s0, e0 in (data.get("gogo_regions") or []):
        for m in out:
            mid = m["start"] + m["dur"] / 2.0
            if s0 - 1e-9 <= mid <= e0 + 1e-9:
                m["gogo"] = True
    return out


def _nice_top(v):
    """目盛りの上端と刻みを決める。(上端, 刻み)。"""
    if v <= 0:
        return 5.0, 1.0
    for step in TICK_STEPS:
        # 目盛りが5本までに収まる刻みを選ぶ。
        if v / step <= 5:
            k = int(v / step)
            if abs(v - k * step) > 1e-9:
                k += 1
            return float(max(k, 1) * step), float(step)
    step = TICK_STEPS[-1]
    return float((int(v / step) + 1) * step), float(step)


class DensityCanvas(QWidget):
    """小節ごとの積み上げ棒。"""

    hovered = Signal(int)          # 指している小節(1始まり)。外れたら 0

    #: 枠の内側の余白(左は目盛りの字、下は小節番号のぶん広い)。
    PAD_L, PAD_R, PAD_T, PAD_B = 46, 12, 16, 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._rows = []
        self._show_gogo = True
        self._hover = -1

    def set_rows(self, rows, show_gogo=True):
        self._rows = list(rows or [])
        self._show_gogo = bool(show_gogo)
        self._hover = -1
        self.update()

    def row(self, i):
        return self._rows[i] if 0 <= i < len(self._rows) else None

    # -- 当たり判定 ---------------------------------------------------
    def _plot_rect(self):
        return QRectF(self.PAD_L, self.PAD_T,
                      max(1.0, self.width() - self.PAD_L - self.PAD_R),
                      max(1.0, self.height() - self.PAD_T - self.PAD_B))

    def _index_at(self, x):
        r = self._plot_rect()
        n = len(self._rows)
        if n == 0 or not (r.left() <= x <= r.right()):
            return -1
        i = int((x - r.left()) / (r.width() / n))
        return i if 0 <= i < n else -1

    def mouseMoveEvent(self, event):
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.hovered.emit(i + 1 if i >= 0 else 0)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.hovered.emit(0)
            self.update()
        super().leaveEvent(event)

    # -- 描く ---------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        self.draw_into(p, self.width(), self.height())
        p.end()

    def draw_into(self, p, w, h):
        """中身を描く。PNG へ書き出すときも同じ道を通す。"""
        rows = self._rows
        r = QRectF(self.PAD_L, self.PAD_T,
                   max(1.0, w - self.PAD_L - self.PAD_R),
                   max(1.0, h - self.PAD_T - self.PAD_B))
        fnt = QFont(self.font())
        fnt.setPointSizeF(max(7.0, fnt.pointSizeF() - 1))
        p.setFont(fnt)
        fm = QFontMetrics(fnt)

        rates = [((m["don"] + m["ka"]) / m["dur"]) if m["dur"] > 0 else 0.0
                 for m in rows]
        top, step = _nice_top(max(rates) if rates else 0.0)

        def y_of(v):
            return r.bottom() - (v / top) * r.height()

        n = len(rows)
        bw = (r.width() / n) if n else 0.0

        # ゴーゴーの下地は目盛り線より先に(いちばん奥に)。
        if self._show_gogo and n:
            i = 0
            while i < n:
                if rows[i]["gogo"]:
                    j = i
                    while j + 1 < n and rows[j + 1]["gogo"]:
                        j += 1
                    p.fillRect(QRectF(r.left() + i * bw, r.top(),
                                      (j - i + 1) * bw, r.height()), GOGO_COLOR)
                    i = j + 1
                else:
                    i += 1

        # 目盛りと数字。
        v = 0.0
        while v <= top + 1e-9:
            y = y_of(v)
            p.setPen(QPen(QColor(90, 90, 90), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QPen(QColor(200, 200, 200)))
            lbl = "%g" % v
            p.drawText(QPointF(r.left() - 6 - fm.horizontalAdvance(lbl),
                               y + fm.ascent() / 2 - 1), lbl)
            v += step

        # 棒。幅が細いときは隙間を空けない(全部つぶれてしまう)。
        gap = BAR_GAP if bw > 3.0 else 0.0
        p.setPen(Qt.NoPen)
        for i, m in enumerate(rows):
            dur = m["dur"]
            if dur <= 0:
                continue
            x = r.left() + i * bw
            bwi = max(1.0, bw - gap)
            d_rate = m["don"] / dur
            k_rate = m["ka"] / dur
            if d_rate > 0:
                p.fillRect(QRectF(x, y_of(d_rate), bwi,
                                  r.bottom() - y_of(d_rate)), DON_COLOR)
            if k_rate > 0:
                p.fillRect(QRectF(x, y_of(d_rate + k_rate), bwi,
                                  y_of(d_rate) - y_of(d_rate + k_rate)),
                           KA_COLOR)

        # 指している小節を白い縦線で。
        if 0 <= self._hover < n:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1))
            x = r.left() + (self._hover + 0.5) * bw
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        # 枠と、下の小節番号。
        p.setPen(QPen(QColor(140, 140, 140), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        p.drawLine(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
        if n:
            # 80px 以上あけて、きりのよい小節番号に打つ。
            want = max(1, int(n / max(1.0, r.width() / 80.0)))
            for cand in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000):
                if cand >= want:
                    want = cand
                    break
            p.setPen(QPen(QColor(200, 200, 200)))
            for i in range(0, n, want):
                x = r.left() + (i + 0.5) * bw
                lbl = str(i + 1)
                p.drawText(QPointF(x - fm.horizontalAdvance(lbl) / 2.0,
                                   r.bottom() + fm.ascent() + 6), lbl)

        # 左上に単位。
        p.setPen(QPen(QColor(170, 170, 170)))
        p.drawText(QPointF(4, fm.ascent() + 2), "打/秒")


class DensityGraphDialog(QDialog):
    """密度グラフの窓。コースを選び、PNG へ書き出せる。"""

    def __init__(self, main_window, content, course_key=None, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self._content = content
        self._rows = []
        self.setWindowTitle("密度グラフ")
        self.resize(900, 430)

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("コース"))
        self.cb_course = QComboBox()
        for c in (getattr(main_window, "courses_info", None) or []):
            self.cb_course.addItem(c["label"], c["key"])
        top.addWidget(self.cb_course)
        self.chk_gogo = QCheckBox("ゴーゴーを重ねる")
        self.chk_gogo.setChecked(True)
        top.addWidget(self.chk_gogo)
        top.addStretch()
        # 色の見本。文字だけだとどちらが赤か分からない。
        for color, name in ((DON_COLOR, "ドン"), (KA_COLOR, "カッ")):
            chip = QLabel("■ " + name)
            chip.setStyleSheet("color: %s;" % color.name())
            top.addWidget(chip)
        outer.addLayout(top)

        self.canvas = DensityCanvas()
        self.canvas.hovered.connect(self._on_hover)
        outer.addWidget(self.canvas, 1)

        self.lbl_info = QLabel("")
        outer.addWidget(self.lbl_info)

        row = QHBoxLayout()
        row.addStretch()
        btn_png = QPushButton("PNG で保存…")
        btn_png.clicked.connect(lambda *_: self._save_png())
        row.addWidget(btn_png)
        btn_close = QPushButton("閉じる")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        outer.addLayout(row)

        self.cb_course.currentIndexChanged.connect(self._reload)
        self.chk_gogo.toggled.connect(self._reload)
        if course_key:
            i = self.cb_course.findData(course_key)
            if i >= 0:
                self.cb_course.setCurrentIndex(i)
        self._reload()

    # -----------------------------------------------------------------
    def _reload(self, *_a):
        key = self.cb_course.currentData()
        try:
            data = self.main_window.analyzer.build_preview_timeline(
                self._content, None, key,
                branch_level=getattr(self.main_window,
                                     "_preview_branch_level", None) or "N")
            rows = measure_density(data)
        except Exception:  # noqa: BLE001
            rows = []
        self._rows = rows
        self.canvas.set_rows(rows, self.chk_gogo.isChecked())
        self._on_hover(0)

    def _on_hover(self, measure):
        """下の行に、指している小節(指していなければ全体)の数字を出す。"""
        rows = self._rows
        if not rows:
            self.lbl_info.setText("音符がありません。")
            return
        m = self.canvas.row(measure - 1) if measure else None
        if m is None:
            don = sum(r["don"] for r in rows)
            ka = sum(r["ka"] for r in rows)
            self.lbl_info.setText("%d小節　ドン %d / カッ %d"
                                  % (len(rows), don, ka))
            return
        rate = ((m["don"] + m["ka"]) / m["dur"]) if m["dur"] > 0 else 0.0
        self.lbl_info.setText(
            "%d小節目　ドン %d / カッ %d　%.2f 打/秒%s"
            % (measure, m["don"], m["ka"], rate,
               "　ゴーゴー" if m["gogo"] else ""))

    def _save_png(self):
        base = getattr(self.main_window, "current_file", "") or ""
        stem = os.path.splitext(os.path.basename(base))[0] or "density"
        start = os.path.join(os.path.dirname(base) if base else "",
                             stem + "_density.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "密度グラフを保存", start, "PNG (*.png)")
        if not path:
            return
        # 画面の倍で描く。小節が多い譜面は等倍だと棒がつぶれて読めない。
        cw, ch = self.canvas.width(), self.canvas.height()
        pm = QPixmap(cw * 2, ch * 2)
        pm.fill(QColor(40, 40, 40))
        p = QPainter(pm)
        p.scale(2.0, 2.0)
        self.canvas.draw_into(p, cw, ch)
        p.end()
        if not pm.save(path):
            QMessageBox.warning(self, "保存できませんでした",
                                "書き込めませんでした。" + chr(10) * 2
                                + "　" + path)
