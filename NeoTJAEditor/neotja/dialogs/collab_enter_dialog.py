"""ルームを作る/入るときに名前(と合言葉)を入れてもらう画面。

名前は**相手の画面に出る**(カーソルの名札・編集モードの持ち主・入退室の
知らせ)。利用者の指定 2026-10-16「ルーム作成、入室の際は自分の名前を
入力したい」。前に入れた名前を覚えておき、次からは初期値にする。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QVBoxLayout)

#: 名前の長さ。相手の画面の名札に出るので、長すぎると譜面が隠れる。
#:
#: **数え方を字の幅で決める**(利用者の指定 2026-10-16「名前は五文字まで、
#: アルファベットは8文字まで」)。半角1つを 1.0、全角1つを 1.6 と数えて
#: 上限 8.0 にすると、ひらがな・漢字なら5文字、英数字なら8文字でちょうど
#: 止まる。混ざった名前(「ねぎ3」など)も見た目の幅でそろう。
NAME_WIDTH_MAX = 8.0
HALF_W, FULL_W = 1.0, 1.6


def name_width(s):
    """名札に出したときのおおよその幅。半角=1.0 / 全角=1.6。"""
    return sum(HALF_W if ord(c) < 128 else FULL_W for c in str(s or ''))


def clip_name(s):
    """上限の幅に収まるところまでで切る。"""
    out, w = [], 0.0
    for c in str(s or ''):
        w += HALF_W if ord(c) < 128 else FULL_W
        if w > NAME_WIDTH_MAX + 1e-9:
            break
        out.append(c)
    return ''.join(out)


class CollabEnterDialog(QDialog):
    """mode は "create"(ルームを作る) か "join"(入室)。"""

    def __init__(self, mode, default_name="", parent=None):
        super().__init__(parent)
        self._mode = "join" if mode == "join" else "create"
        self.setWindowTitle("ルームを作成" if self._mode == "create"
                            else "ルームに入室")
        lay = QVBoxLayout(self)
        # **試験的**だと最初に言っておく(利用者の指定 2026-10-16)。
        warn = QLabel(
            "⚠ 合作は試験的な機能です。うまく動かないことがあります。"
            "譜面はアプリの隣の「合作」フォルダに写して扱うので元の"
            "ファイルは触りませんが、大事な譜面は控えを取っておいてください。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#e0af68;")
        lay.addWidget(warn)
        lay.addWidget(QLabel(
            "この名前が相手の画面に出ます（カーソルの名札・編集モードの持ち主）。"))
        form = QFormLayout()
        self.ed_name = QLineEdit(str(default_name or ""))
        # 幅で切るので、Qt の「文字数」の上限は使えない。
        self.ed_name.textChanged.connect(self._clip)
        self.ed_name.setPlaceholderText("例: ねぎ")
        form.addRow("あなたの名前:", self.ed_name)
        self.ed_code = None
        if self._mode == "join":
            self.ed_code = QLineEdit()
            self.ed_code.setPlaceholderText("渡された合言葉を貼ってください")
            form.addRow("合言葉:", self.ed_code)
        lay.addLayout(form)

        self._warn = QLabel("")
        self._warn.setStyleSheet("color: #f7768e;")
        lay.addWidget(self._warn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setText(
            "ルームを作る" if self._mode == "create" else "入室する")
        lay.addWidget(bb)
        self.resize(460, 170)
        (self.ed_code or self.ed_name).setFocus(Qt.OtherFocusReason)

    def name(self):
        return self.ed_name.text().strip()

    def code(self):
        return self.ed_code.text().strip() if self.ed_code else ""

    def _clip(self, text):
        """幅の上限を超えたら切り戻す。残りの幅も知らせる。"""
        cut = clip_name(text)
        if cut != text:
            at = self.ed_name.cursorPosition()
            self.ed_name.blockSignals(True)
            self.ed_name.setText(cut)
            self.ed_name.setCursorPosition(min(at, len(cut)))
            self.ed_name.blockSignals(False)
        self._warn.setText("")

    def _try_accept(self):
        """空のまま進ませない。名無しだと相手の画面で誰だか分からない。"""
        if not self.name():
            self._warn.setText("名前を入れてください。")
            self.ed_name.setFocus(Qt.OtherFocusReason)
            return
        if self._mode == "join" and not self.code():
            self._warn.setText("招待コードを貼ってください。")
            self.ed_code.setFocus(Qt.OtherFocusReason)
            return
        self.accept()
