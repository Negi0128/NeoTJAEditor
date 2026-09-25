"""作譜モードの命令パネル。

ゲーム画面の**空いている場所**(レーンの枠より上・右寄り)に置く板。値を決めて
「追加」を押すと、その命令が**編集カーソルの位置**に入る(PeepoDrumKit の右の
パネルと同じ形)。右クリックのメニューや行の直接クリックと同じ操作を、目に
見えるボタンでもできるようにするためのもの — TJA の命令を覚えていなくても
譜面が作れる、という利用者の狙いに沿う。

置ける高さは、レーンの枠の上までの 110px ほどしかない(game_screen の
LANE_Y=196、枠はその 56px 上から)。窓は広げない・レーンには触らない、という
指定なので、縦積みにはせず **命令ごとの枠を横に並べた 3×2 のます目** にして
収めている。

値の欄は、カーソルが動くたびに「その位置で今効いている値」へ追従する。
ただし自分が触っている欄(フォーカスがある欄)は書き換えない — 入力中に
数字が飛ぶのを防ぐため。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout)


class CommandPanel(QFrame):
    #: 値のある命令を置く。(名前, 値)。名前は note_edit.COMMAND_NAMES。
    placeCommand = Signal(str, object)
    #: 開始/終了の印を置く。(種類, "on"/"off")。種類は GOGO / BARLINE。
    placeMarker = Signal(str, str)

    WIDTH = 672
    HEIGHT = 132
    #: 命令1つぶんの枠の大きさ。
    BOX_W = 214
    BOX_H = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("commandPanel")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        # ゲーム画面の上に置くので、窓の QSS 任せにせず自前で色を決める
        # (暗い背景に暗い文字だと読めない)。
        self.setStyleSheet(
            "#commandPanel { background: rgba(10,12,18,215);"
            " border: 1px solid #222a3a; }"
            "QFrame#cmdBox { background: rgba(20,24,34,220);"
            " border: 1px solid #33405c; border-radius: 4px; }"
            "#commandPanel QLabel { color: #cdd6f4; background: transparent; }"
            "#commandPanel QLabel#boxTitle { color: #8fa3c0; }"
            "#commandPanel QAbstractSpinBox { color: #ffffff; background: #10141d;"
            " border: 1px solid #2e3a50; border-radius: 3px; padding: 1px 3px;"
            " min-height: 24px; font-size: 14px; }"
            "#commandPanel QPushButton { color: #cdd6f4; background: #232b3b;"
            " border: 1px solid #3a4763; border-radius: 3px;"
            " padding: 2px 6px; min-height: 24px; font-size: 13px; }"
            "#commandPanel QPushButton:hover { background: #2d3750; }")

        grid = QGridLayout(self)
        grid.setContentsMargins(6, 5, 6, 5)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)

        # --- BPM ---
        self.sp_bpm = QDoubleSpinBox()
        self.sp_bpm.setDecimals(3)
        self.sp_bpm.setRange(1.0, 9999.0)
        self.sp_bpm.setValue(120.0)
        self.sp_bpm.setFixedWidth(108)
        grid.addWidget(self._box("BPM", [self.sp_bpm], "追加",
                                 lambda: self.placeCommand.emit("BPMCHANGE", self.sp_bpm.value())),
                       0, 0)

        # --- 拍子記号 ---
        self.sp_num = QSpinBox()
        self.sp_num.setRange(1, 64)
        self.sp_num.setValue(4)
        self.sp_num.setFixedWidth(48)
        self.sp_den = QSpinBox()
        self.sp_den.setRange(1, 64)
        self.sp_den.setValue(4)
        self.sp_den.setFixedWidth(48)
        slash = QLabel("/")
        slash.setFixedWidth(8)
        slash.setAlignment(Qt.AlignCenter)
        grid.addWidget(self._box("拍子記号", [self.sp_num, slash, self.sp_den], "追加",
                                 lambda: self.placeCommand.emit(
                                     "MEASURE",
                                     "%d/%d" % (self.sp_num.value(), self.sp_den.value()))),
                       0, 1)

        # --- スクロール(HS) ---
        self.sp_hs = QDoubleSpinBox()
        self.sp_hs.setDecimals(3)
        self.sp_hs.setRange(-100.0, 100.0)
        self.sp_hs.setSingleStep(0.05)
        self.sp_hs.setValue(1.0)
        self.sp_hs.setFixedWidth(108)
        grid.addWidget(self._box("スクロール", [self.sp_hs], "追加",
                                 lambda: self.placeCommand.emit("SCROLL", self.sp_hs.value())),
                       0, 2)

        # --- 小節線 / GOGO: 値が無いのでボタン2つ ---
        grid.addWidget(self._pair_box("小節線の表示", "表示", "非表示",
                                      lambda: self.placeMarker.emit("BARLINE", "off"),
                                      lambda: self.placeMarker.emit("BARLINE", "on")),
                       1, 0)
        grid.addWidget(self._pair_box("ゴーゴータイム", "開始", "終了",
                                      lambda: self.placeMarker.emit("GOGO", "on"),
                                      lambda: self.placeMarker.emit("GOGO", "off")),
                       1, 1)

        note = QLabel("カーソルの位置に置きます")
        f = note.font()
        f.setPixelSize(12)
        note.setFont(f)
        note.setStyleSheet("color: #6c7a96;")
        note.setAlignment(Qt.AlignCenter)
        grid.addWidget(note, 1, 2)

    # ------------------------------------------------------------------
    def _new_box(self, title):
        box = QFrame(self)
        box.setObjectName("cmdBox")
        box.setFixedSize(self.BOX_W, self.BOX_H)
        v = QVBoxLayout(box)
        v.setContentsMargins(7, 4, 7, 5)
        v.setSpacing(2)
        lab = QLabel(title)
        lab.setObjectName("boxTitle")
        f = lab.font()
        f.setPixelSize(12)
        lab.setFont(f)
        v.addWidget(lab)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        v.addLayout(row)
        return box, row

    def _box(self, title, widgets, add_text, on_add):
        """[見出し / 値の欄… + 追加] の枠。"""
        box, row = self._new_box(title)
        for wdg in widgets:
            row.addWidget(wdg)
        row.addStretch()
        btn = QPushButton(add_text)
        btn.setFixedWidth(52)
        btn.clicked.connect(lambda _c=False: on_add())
        row.addWidget(btn)
        return box

    def _pair_box(self, title, a_text, b_text, on_a, on_b):
        """[見出し / ボタン2つ] の枠(値が無い命令)。"""
        box, row = self._new_box(title)
        for text, fn in ((a_text, on_a), (b_text, on_b)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(btn)
        return box

    # ------------------------------------------------------------------
    def set_values(self, bpm=None, scroll=None, measure=None):
        """カーソルの位置で効いている値へ欄を合わせる。

        触っている欄(フォーカスがある欄)は書き換えない。"""
        if bpm is not None and not self.sp_bpm.hasFocus():
            self.sp_bpm.setValue(float(bpm))
        if scroll is not None and not self.sp_hs.hasFocus():
            self.sp_hs.setValue(float(scroll))
        if measure:
            try:
                n, d = (int(x) for x in str(measure).split("/"))
            except (ValueError, TypeError):
                return
            if not self.sp_num.hasFocus():
                self.sp_num.setValue(n)
            if not self.sp_den.hasFocus():
                self.sp_den.setValue(d)
