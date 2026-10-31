"""譜面プレビューから譜面のヘッダを直す小さな窓(利用者の指定 2026-10-31)。

プレビューを見ているあいだはテキストのエディタが隠れているので、曲名や
BPM を直すのにいちいち窓を行き来していた。ここで直せるようにする。

書くのは呼ぶ側(MainWindow.apply_header_values)。この窓は「いまの値を見せて、
変えたものだけ返す」だけにしてある。
"""
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout,
)

#: (鍵, 見出し, 下に出す小さな説明)
FIELDS = (
    ("TITLE", "曲名", ""),
    ("SUBTITLE", "サブタイトル", "先頭の -- は消さない"),
    ("BPM", "BPM", ""),
    ("WAVE", "音源", "TJA と同じフォルダのファイル名"),
    ("OFFSET", "OFFSET", "音源と譜面のズレ(秒)"),
    ("LEVEL", "レベル", "いま見ているコースの★の数"),
    ("DEMOSTART", "DEMOSTART", "選曲画面の試聴開始(秒)"),
)


class HeaderDialog(QDialog):
    def __init__(self, values, apply_cb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ヘッダ")
        self.apply_cb = apply_cb
        self._values = dict(values or {})
        self._edits = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        for key, label, hint in FIELDS:
            ed = QLineEdit(str(self._values.get(key, "") or ""))
            if hint:
                ed.setPlaceholderText(hint)
            self._edits[key] = ed
            form.addRow(label, ed)
        layout.addLayout(form)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("反映")
        ok.setObjectName("accentButton")
        ok.clicked.connect(self._apply)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)
        self.resize(420, 10)

    def changed_values(self):
        """触った欄だけ。触っていない欄は本文に手を付けない。"""
        out = {}
        for key, _label, _hint in FIELDS:
            now = self._edits[key].text().strip()
            was = str(self._values.get(key, "") or "").strip()
            if now != was:
                out[key] = now
        return out

    def _apply(self):
        changed = self.changed_values()
        if changed:
            self.apply_cb(changed)
        self.accept()
