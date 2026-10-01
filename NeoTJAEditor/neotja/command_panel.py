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
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout)


class CommandPanel(QFrame):
    #: 値のある命令を置く。(名前, 値)。名前は note_edit.COMMAND_NAMES。
    placeCommand = Signal(str, object)
    #: すでに置いてある命令の値を書き換える。(名前, 値)。どの命令かは
    #: 呼ばれた側(preview_dock)が、選んでいるもの/カーソルの位置から決める。
    editCommand = Signal(str, object)
    #: 開始/終了の印を置く。(種類, "on"/"off")。種類は GOGO / BARLINE。
    placeMarker = Signal(str, str)
    #: 譜面分岐の系統を選ぶ。("N"/"E"/"M")
    selectBranch = Signal(str)

    #: 命令の行の種類 → その枠を持っている欄の名前(set_values の editing 用)。
    KINDS = ("bpm", "measure", "hs")

    WIDTH = 672
    HEIGHT = 132
    #: 命令1つぶんの枠の大きさ。
    BOX_W = 214
    BOX_H = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        # 種類 → (枠, ボタン)。「追加」と「変更」を切り替えるために持つ。
        self._boxes = {}
        # いま「変更」になっている種類。
        self._editing = set()
        # 譜面分岐の入れ物(_branch_box で作る)。
        self._branch_auto = None
        self._branch_combo = None
        self._branch_title = None
        self._branch_level = None
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
        grid.addWidget(self._box("bpm", "BPM", [self.sp_bpm], "BPMCHANGE",
                                 lambda: self.sp_bpm.value()),
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
        grid.addWidget(self._box("measure", "拍子記号",
                                 [self.sp_num, slash, self.sp_den], "MEASURE",
                                 lambda: "%d/%d" % (self.sp_num.value(),
                                                    self.sp_den.value())),
                       0, 1)

        # --- スクロール(HS) ---
        self.sp_hs = QDoubleSpinBox()
        self.sp_hs.setDecimals(3)
        self.sp_hs.setRange(-100.0, 100.0)
        self.sp_hs.setSingleStep(0.05)
        self.sp_hs.setValue(1.0)
        self.sp_hs.setFixedWidth(108)
        grid.addWidget(self._box("hs", "スクロール", [self.sp_hs], "SCROLL",
                                 lambda: self.sp_hs.value()),
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

        # --- 譜面分岐: どの系統を見て(編集して)いるか ---
        grid.addWidget(self._branch_box(), 1, 2)

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

    def _box(self, kind, title, widgets, name, get_value):
        """[見出し / 値の欄… + 追加(変更)] の枠。

        その位置に同じ命令が居るときはボタンが「変更」になり、押すと
        editCommand が飛ぶ(新しく足すのではなく、その行の値を書き換える)。
        居なければ今までどおり「追加」で placeCommand。"""
        box, row = self._new_box(title)
        for wdg in widgets:
            row.addWidget(wdg)
        row.addStretch()
        btn = QPushButton("追加")
        btn.setFixedWidth(52)

        def pressed(_c=False, _k=kind, _n=name, _v=get_value):
            if _k in self._editing:
                self.editCommand.emit(_n, _v())
            else:
                self.placeCommand.emit(_n, _v())
        btn.clicked.connect(pressed)
        row.addWidget(btn)
        self._boxes[kind] = (box, btn)
        return box

    #: 系統 → (ボタンの文字, 選ばれているときの色)。色は TNDE-R のレーンの
    #: 地の色に合わせた(普通=灰、玄人=青緑、達人=紫)。先頭の「自動」は
    #: 本家と同じ動き(#BRANCHSTART の条件で区間ごとに決まる)。
    BRANCHES = (("auto", "自動", "#3f6b3f"), ("N", "普通", "#5b6470"),
                ("E", "玄人", "#2f6f86"), ("M", "達人", "#7d2a72"))

    def _branch_box(self):
        """[譜面分岐 / 普通・玄人・達人] の枠。

        分岐のある譜面でだけ押せる。押すとゲーム画面の表示と、**作譜モードの
        編集先**が、その系統に切り替わる(分岐は同じ時間の別案なので、どれを
        編集しているのかが分からないと打ち込めない)。

        「自動」は本家と同じ — #BRANCHSTART の条件で区間ごとに決まる
        (自動演奏なので精度は常に100%、連打は全部拾う)。普通/玄人/達人を
        選ぶと、条件を見ずにその系統だけを通して流す。"""
        box, row = self._new_box("譜面分岐")
        self._branch_title = box.findChild(QLabel, "boxTitle")
        row.setSpacing(4)
        # ボタンは2つだけ(利用者の指定 2026-10-01)。「自動」と、系統を選ぶ
        # プルダウン。4つ並べていたころは枠がいっぱいで、どれが効いているかも
        # 読みにくかった。
        self._branch_auto = QPushButton("自動")
        self._branch_auto.setFixedWidth(46)
        self._branch_auto.clicked.connect(
            lambda _c=False: self.selectBranch.emit("auto"))
        row.addWidget(self._branch_auto)
        self._branch_combo = QComboBox()
        for key, text, _col in self.BRANCHES:
            if key != "auto":
                self._branch_combo.addItem(text, key)
        self._branch_combo.setCurrentIndex(self._branch_combo.count() - 1)  # 達人
        self._branch_combo.activated.connect(self._on_branch_combo)
        row.addWidget(self._branch_combo, 1)
        self._branch_box_w = box
        self.set_branch(None, False)
        return box

    def _on_branch_combo(self, _idx):
        key = self._branch_combo.currentData()
        if key:
            self.selectBranch.emit(key)

    def choose_branch(self, key):
        """系統を選ぶ(プルダウンを操作したのと同じ)。試験からも使う。"""
        if key == "auto":
            self._branch_auto.click()
            return
        i = self._branch_combo.findData(key)
        if i >= 0:
            self._branch_combo.setCurrentIndex(i)
            self._on_branch_combo(i)

    #: 選ばれている側だけ色を付ける。
    _BRANCH_ON_CSS = ("color: #ffffff; background: %s; border: 1px solid #cdd6f4;"
                      " border-radius: 3px; padding: 2px 4px; min-height: 24px;"
                      " font-size: 13px;")

    def set_branch(self, level, has_branches):
        """いまの系統を反映する。分岐の無い譜面では押せなくする。"""
        keys = {k for k, _t, _c in self.BRANCHES}
        self._branch_level = level if level in keys else None
        cols = {k: c for k, _t, c in self.BRANCHES}
        on = bool(has_branches)
        self._branch_auto.setEnabled(on)
        self._branch_combo.setEnabled(on)
        auto_on = on and self._branch_level == "auto"
        self._branch_auto.setStyleSheet(
            (self._BRANCH_ON_CSS % cols["auto"]) if auto_on else "")
        if self._branch_level in ("N", "E", "M"):
            i = self._branch_combo.findData(self._branch_level)
            if i >= 0 and i != self._branch_combo.currentIndex():
                self._branch_combo.blockSignals(True)
                self._branch_combo.setCurrentIndex(i)
                self._branch_combo.blockSignals(False)
        combo_on = on and self._branch_level in ("N", "E", "M")
        self._branch_combo.setStyleSheet(
            (self._BRANCH_ON_CSS % cols.get(self._branch_level, "#5b6470"))
            if combo_on else "")
        if self._branch_title is not None:
            self._branch_title.setText("譜面分岐" if has_branches
                                       else "譜面分岐（この譜面には無い）")

    def _pair_box(self, title, a_text, b_text, on_a, on_b):
        """[見出し / ボタン2つ] の枠(値が無い命令)。"""
        box, row = self._new_box(title)
        for text, fn in ((a_text, on_a), (b_text, on_b)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(btn)
        return box

    # ------------------------------------------------------------------
    def set_editing(self, kinds):
        """「変更」にする枠を決める(kinds は "bpm"/"hs"/"measure" の集まり)。

        その位置に同じ命令が置いてある枠だけ、ボタンが「変更」に変わり、枠の
        線が明るくなる。欄の値は set_values でその命令自身の値になっている。"""
        kinds = set(kinds or ())
        if kinds == self._editing:
            return
        self._editing = kinds
        for kind, (box, btn) in self._boxes.items():
            on = kind in kinds
            btn.setText("変更" if on else "追加")
            box.setProperty("editing", "1" if on else "0")
            # 枠1つだけに当て直す(全体の QSS は触らない)。
            box.setStyleSheet(
                "QFrame#cmdBox { background: rgba(28,34,50,230);"
                " border: 1px solid #7aa2f7; border-radius: 4px; }" if on else "")


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
