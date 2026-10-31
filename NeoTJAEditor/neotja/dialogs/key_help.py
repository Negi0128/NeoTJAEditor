"""作譜のキー一覧を出す小さな窓(利用者の指定 2026-10-31)。

作譜モードで開いても「何を押せば音符が置けるか」が画面に出ていなかった。
ヘルプを読まないと分からないのは初めての人には厳しいので、押せば出る所を
作る。

**出しても作譜の操作は止まらない。** 窓に焦点を渡さない(WA_ShowWithoutActivating
＋ 全部の部品を NoFocus)ので、出したままキーを試せる。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

#: (見出し, [(キー, 何が起きるか)])
SECTIONS = (
    ("音符を置く", (
        ("F / J", "ドン"),
        ("D / K", "カッ"),
        ("R / U", "連打  押したまま ← → で長さ"),
        ("E / I", "風船  押したまま ← → で長さ"),
        ("G", "ゴーゴー  (今いる所で 開始 / 終了)"),
    )),
    ("Alt を足すと大音符", (
        ("Alt + F / J", "大ドン"),
        ("Alt + D / K", "大カッ"),
    )),
    ("動かす・選ぶ", (
        ("← →", "カーソルを1つ動かす"),
        ("↑ ↓", "音符の間隔 (1/4〜1/48)"),
        ("Home / End", "曲の頭 / 終わり"),
        ("クリック", "音符を1つ選ぶ  (Ctrl+クリックで足し引き)"),
        ("右ドラッグ", "四角で囲って選ぶ"),
        ("右クリック", "選んだ音符に使う道具が出る"),
        ("Delete", "選んだものを消す"),
        ("Ctrl+Z / Y", "元に戻す / やり直す"),
    )),
    ("聴く", (
        ("Space", "その場から再生 / 一時停止"),
        ("Z / C", "再生速度を 遅く / 速く"),
    )),
)


class KeyHelpWindow(QWidget):
    """作譜のキー一覧。焦点を取らない小さな窓。"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowTitleHint
                         | Qt.WindowCloseButtonHint)
        self.setWindowTitle("作譜のキー")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        for title, rows in SECTIONS:
            head = QLabel(title)
            head.setObjectName("keyHelpHead")
            head.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(head)
            body = QLabel(chr(10).join(
                "  %-12s %s" % (k, what) for k, what in rows))
            body.setObjectName("keyHelpBody")
            body.setFocusPolicy(Qt.NoFocus)
            body.setTextInteractionFlags(Qt.NoTextInteraction)
            layout.addWidget(body)
        layout.addStretch()
        self.setStyleSheet(
            "QWidget { background: #1a1d24; }"
            "#keyHelpHead { color: #4ea3ff; font-weight: bold; }"
            "#keyHelpBody { color: #e8e8e8; font-family: 'Consolas','MS Gothic',"
            " monospace; }")

    def show_near(self, widget=None):
        """出す。もう出ていれば前へ出すだけ(2つ目は作らない)。"""
        if widget is not None and not self.isVisible():
            g = widget.window().geometry()
            self.move(g.right() - self.sizeHint().width() - 40, g.top() + 60)
        self.show()
        self.raise_()
