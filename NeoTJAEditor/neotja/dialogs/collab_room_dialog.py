"""ルームを作ったときに出す画面(招待コードを相手へ渡すため)。

招待コードは2種類ある。

  家の中   … すぐ出る。速い。同じ LAN の相手だけ
  ネット越し… トンネルの入口ができるまで数秒待つ。誰にでも渡せる

**待たせない。** 家の中のぶんを先に出しておき、ネット越しのぶんは出来たら
入れ替える(利用者の指定 2026-10-16: 相手に何も入れさせない)。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout)


class CollabRoomDialog(QDialog):

    def __init__(self, lan_codes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ルームを作成")
        self.setModal(False)          # 開けたまま譜面を触れるようにする
        self._net_code = ""
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("ルームを作りました。招待コードを相手に渡してください。"))

        lay.addWidget(QLabel("<b>同じ家の中（LAN）</b>"))
        self.box_lan = QPlainTextEdit("\n".join(lan_codes or []))
        self.box_lan.setReadOnly(True)
        self.box_lan.setMaximumHeight(72)
        lay.addWidget(self.box_lan)

        lay.addWidget(QLabel("<b>離れている相手（インターネット越し）</b>"))
        self.box_net = QPlainTextEdit(
            "入口を用意しています… (1分ほどかかります)"
            "  ※出来るまで渡しても相手は入れません。")
        self.box_net.setReadOnly(True)
        self.box_net.setMaximumHeight(64)
        lay.addWidget(self.box_net)
        # 1分ほど待たされるので、あとどれくらいかを出す
        # (利用者の指定 2026-10-16)。
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("入口を用意しています… %p%")
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        self.btn_copy = QPushButton("ネット越しの招待コードをコピー")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy)
        row.addWidget(self.btn_copy)
        btn_copy_lan = QPushButton("家の中の招待コードをコピー")
        btn_copy_lan.clicked.connect(self._copy_lan)
        row.addWidget(btn_copy_lan)
        row.addStretch(1)
        btn_close = QPushButton("閉じる")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)

        note = QLabel(
            "相手は「合作 → ルームに入室」に貼るだけです。\n"
            "ネット越しの招待コードはこのルームだけのもので、閉じると無効になります。")
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(note)
        self.resize(560, 360)

    def set_net_code(self, code):
        """入口ができた。招待コードを入れてコピーを押せるようにする。"""
        self._net_code = str(code or "")
        self.bar.setValue(100)
        self.bar.setFormat("入口ができました")
        self.box_net.setPlainText(self._net_code)
        self.btn_copy.setEnabled(bool(self._net_code))

    #: できるまでの見込み(秒)。実測 60 秒前後なので、それを 100% とする。
    EXPECT_SEC = 60.0

    def set_net_waiting(self, secs):
        """まだ名前が引けない。あとどれくらいかを出す。

        **100% には届かせない。** 見込みを過ぎても出来ていないことはあるし、
        100% と出したあとに待たされるほうが不安になる。"""
        if self._net_code:
            return
        secs = float(secs)
        pct = max(0, min(99, int(100.0 * secs / self.EXPECT_SEC)))
        self.bar.setValue(pct)
        left = int(self.EXPECT_SEC - secs)
        self.box_net.setPlainText(
            ("入口を用意しています… 残り およそ %d 秒" % left) if left > 0
            else "入口を用意しています… もう少しです")

    def set_net_error(self, why):
        """入口が作れなかった。理由を出して、家の中だけで使ってもらう。"""
        self._net_code = ""
        self.bar.setValue(0)
        self.bar.setFormat("入口は作れませんでした")
        self.box_net.setPlainText(str(why or "入口を作れませんでした。"))
        self.btn_copy.setEnabled(False)

    def _copy(self):
        if self._net_code:
            QApplication.clipboard().setText(self._net_code)

    def _copy_lan(self):
        txt = self.box_lan.toPlainText().strip().split("\n")
        if txt and txt[0]:
            QApplication.clipboard().setText(txt[0])
