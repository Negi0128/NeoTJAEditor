"""難易度(コース)を1つ足す小さな窓(利用者の指定 2026-11-12)。

TJA に新しいコースを書き足すとき、COURSE: / LEVEL: / BALLOON: … #START
#END の決まり文句を毎回手で打つのは手間だし、綴りを間違えると解析に
乗らない。ここで難易度と★の数だけ選べば、呼ぶ側(MainWindow.add_course)
が本文の末尾にひな型を書く。

窓は「選んだもの(COURSE の値, LEVEL)を返す」だけ。本文を触るのは呼ぶ側。

見た目は起動画面と同じ「ガラスと光」(dialogs/glass.py、利用者の承認
2026-11-12)。欄は 1 枚のカードにまとめ、OK だけアクセント色。
"""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QPushButton, QSpinBox, QVBoxLayout,
)

from neotja.dialogs import set_default_button
from neotja.dialogs.glass import apply_glass, field_label, make_card
from neotja.tja_analyzer import TJACourseAnalyzer

#: 選べる難易度。並びは TJA の 0〜4 の順(かんたん → おに(裏))。
#: 書く値は COURSE:Oni / COURSE:Edit のように英語名 — 数字より読みやすく、
#: どの太鼓シミュでも通る綴りなので。
COURSE_VALUES = ("Easy", "Normal", "Hard", "Oni", "Edit")


class AddCourseDialog(QDialog):
    def __init__(self, present_keys, parent=None):
        """present_keys: 本文にもうあるコースの鍵(Easy/Normal/…)の集まり。
        その項目は選べなくする — 同じコースが2つあると解析が先の方しか
        見ないので、作っても出てこない。"""
        super().__init__(parent)
        self.setWindowTitle("難易度を追加")
        present = set(present_keys or ())

        layout = QVBoxLayout(self)
        apply_glass(self)

        # 欄は 1 枚のガラスのカードに。QFormLayout でなく格子なのは、
        # ラベルの幅を他の窓(新規作成)とそろえるため(field_label)。
        card, form = make_card("追加する難易度")

        self.cb_course = QComboBox()
        first_free = None
        for i, key in enumerate(COURSE_VALUES):
            label = TJACourseAnalyzer.DIFF_LABEL.get(key, key)
            self.cb_course.addItem("%s  (COURSE:%s)" % (label, key), key)
            if key in present:
                # 灰色にして選べなくする(消すと並びの位置が覚えにくい)。
                item = self.cb_course.model().item(i)
                item.setEnabled(False)
                item.setText("%s  (COURSE:%s)  — あり" % (label, key))
            elif first_free is None:
                first_free = i
        # 最初の「まだ無い」難易度を選んでおく。全部あるなら先頭のまま
        # (OK を押しても呼ぶ側が断る)。
        self.cb_course.setCurrentIndex(first_free if first_free is not None else 0)
        form.addWidget(field_label("難易度"), 1, 0)
        form.addWidget(self.cb_course, 1, 1)

        self.spin_level = QSpinBox()
        self.spin_level.setRange(1, 10)
        self.spin_level.setValue(5)
        self.spin_level.setPrefix("★")
        form.addWidget(field_label("LEVEL"), 2, 0)
        form.addWidget(self.spin_level, 2, 1)
        layout.addWidget(card)
        # 欄が窮屈に見えない幅。高さは中身なり。
        self.setMinimumWidth(400)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("accentButton")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)
        # Enter は OK へ(キャンセルに取られないように)。
        set_default_button(self, btn_ok)
        self.all_present = first_free is None

    def course_value(self):
        """COURSE: に書く値(Easy/Normal/Hard/Oni/Edit)。"""
        return self.cb_course.currentData()

    def level(self):
        return int(self.spin_level.value())
