"""同時再生で並べる難易度を選ぶ。

並べられるのは **2〜4本**(利用者の指定 2026-10-03)。1本なら今までの再生で
足りるし、5本は 1280x720 に入らない(帯1本 176px x 4 = 704px)。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                               QVBoxLayout)

from neotja.multi_screen import MAX_BANDS, MIN_BANDS


class MultiCourseDialog(QDialog):
    """譜面にあるコースを並べて、同時に見たいものへチェックを入れる。"""

    def __init__(self, parent, courses, selected=None):
        """courses は [{"key": "Oni", "label": "おに", "level": 10}, ...]。"""
        super().__init__(parent)
        self.setWindowTitle("同時再生する難易度")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("縦に並べる難易度を選びます（%d〜%d個）。"
                           % (MIN_BANDS, MAX_BANDS)))
        self._boxes = []
        pre = set(selected or [])
        for c in courses:
            lv = c.get("level")
            text = c.get("label") or c.get("key")
            if lv not in (None, ""):
                text = "%s　★%s" % (text, lv)
            box = QCheckBox(text)
            box.setChecked(c["key"] in pre)
            box.toggled.connect(self._refresh)
            v.addWidget(box)
            self._boxes.append((c["key"], box))
        # 何も指定が無ければ、難しいほうから既定で2つ入れておく。
        if not pre:
            for _k, b in self._boxes[:MIN_BANDS]:
                b.setChecked(True)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        v.addWidget(self._note)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                         | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        v.addWidget(self._buttons)
        self._refresh()

    def _refresh(self, *_a):
        n = len(self.selected())
        ok = MIN_BANDS <= n <= MAX_BANDS
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        if n < MIN_BANDS:
            self._note.setText("あと %d 個選んでください。" % (MIN_BANDS - n))
        elif n > MAX_BANDS:
            self._note.setText("%d 個までです（%d 個選ばれています）。"
                               % (MAX_BANDS, n))
        else:
            self._note.setText("%d 本を縦に並べます。" % n)
        # 上限に達したら、チェックの入っていない箱は押せなくする。
        for _k, b in self._boxes:
            b.setEnabled(b.isChecked() or n < MAX_BANDS)

    def selected(self):
        """選んだコースの key を、譜面に出てくる順で返す。"""
        return [k for k, b in self._boxes if b.isChecked()]

    @staticmethod
    def ask(parent, courses, selected=None):
        """選び終えた key のリスト。取り消したら None。"""
        dlg = MultiCourseDialog(parent, courses, selected)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.selected()
