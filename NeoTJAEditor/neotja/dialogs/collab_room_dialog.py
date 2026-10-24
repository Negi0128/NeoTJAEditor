"""ルームを作ったときに出す画面(渡すものを相手へ渡すため)。

渡すものは3つある。

  招待コード : 同一LAN              … すぐ出る。速い。同じ LAN の相手だけ
  招待コード : インターネット経由    … 入口ができるまで数十秒待つ。誰にでも
  招待コード : NeoTJAEditor Mobile … スマホで開く URL(入れるものは同じ口)

**待たせない。** 家の中のぶんを先に出しておき、ネット越しのぶんは出来たら
足す(利用者の指定 2026-10-16: 相手に何も入れさせない)。ネット越しの入口が
出来たら、スマホ用の URL も**家の中のぶんと並べて**出す(利用者の指定
2026-10-24: 相手の居場所で選べるように)。

いちばん下は、合作に入らずスマホ1台で使う口(?solo)。開いたときに
「合作に入る」を出さずに、そのまま譜面を書ける。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                               QLabel, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout)

from neotja.collab import CODE_PREFIX

#: 単独で使う口に付ける印。mobile.html がこれを見て、合作へ繋ぎに行かない。
SOLO_QUERY = "?solo"


def mobile_url_of(code):
    """招待コードを、スマホで開く URL に直す。読めなければ空。

    家の中のコードは `neotja1:192.168.11.2:7391` の形。ページも WebSocket も
    同じ口から出るので、そのまま http:// を被せればよい。ネット越しのぶんは
    すでに URL(https://...)なので、そのまま使う。
    """
    s = str(code or "").strip()
    if not s:
        return ""
    if s.startswith(CODE_PREFIX):
        s = s[len(CODE_PREFIX):]
    if s.startswith("http://") or s.startswith("https://"):
        return s.rstrip("/") + "/"
    if s.startswith("ws://"):
        return "http://" + s[5:].rstrip("/") + "/"
    if s.startswith("wss://"):
        return "https://" + s[6:].rstrip("/") + "/"
    return "http://" + s.rstrip("/") + "/"


class _Field(QPlainTextEdit):
    """読むだけの小さな欄。行数に合わせて縮む。"""

    def __init__(self, text="", lines=1):
        super().__init__(text)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        h = self.fontMetrics().lineSpacing() * max(1, lines) + 12
        self.setFixedHeight(h)
        self.setFrameShape(QFrame.StyledPanel)

    def fit(self, lines):
        self.setFixedHeight(self.fontMetrics().lineSpacing()
                            * max(1, lines) + 12)


class CollabRoomDialog(QDialog):

    def __init__(self, lan_codes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ルームを作成")
        self.setModal(False)          # 開けたまま譜面を触れるようにする
        self._net_code = ""
        self._lan_codes = [str(c) for c in (lan_codes or []) if c]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("ルームを作りました。渡したいものをコピーしてください。"))

        # ---- 招待コード : 同一LAN ----
        self.box_lan = _Field("\n".join(self._lan_codes),
                              max(1, len(self._lan_codes)))
        lay.addLayout(self._row("招待コード : 同一LAN", self.box_lan,
                                self._copy_lan))

        # ---- 招待コード : インターネット経由 ----
        self.box_net = _Field("入口を用意しています… (1分ほどかかります)", 1)
        self.btn_copy = QPushButton("コピー")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy)
        lay.addLayout(self._row("招待コード : インターネット経由",
                                self.box_net, None, self.btn_copy))
        # 1分ほど待たされるので、あとどれくらいかを出す
        # (利用者の指定 2026-10-16)。
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("入口を用意しています… %p%")
        lay.addWidget(self.bar)

        # ---- 招待コード : NeoTJAEditor Mobile ----
        self.box_mobile = _Field("", 1)
        lay.addLayout(self._row("招待コード : NeoTJAEditor Mobile",
                                self.box_mobile, self._copy_mobile))
        hint = QLabel("スマホのブラウザで開くだけで、合作に入れます。")
        hint.setStyleSheet("color: #8a94a6;")
        lay.addWidget(hint)

        # ---- 単独で使う口 ----
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)
        self.box_solo = _Field("", 1)
        lay.addLayout(self._row("スマホを単独で使う（合作に入らない）",
                                self.box_solo, self._copy_solo))

        note = QLabel(
            "招待コードは「合作 → ルームに入室」に貼るだけです。\n"
            "インターネット経由のぶんはこのルームだけのもので、閉じると無効になります。")
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(note)
        # 「閉じる」はいちばん下に1つだけ。コピーの列と同じ高さに置くと、
        # どの欄のボタンなのか分からなくなる。
        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton("閉じる")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self._sync_mobile()
        self.resize(620, 480)

    @staticmethod
    def _row(title, field, copy_cb, copy_btn=None):
        """「見出し / 欄 / コピー」の1組。"""
        box = QVBoxLayout()
        lab = QLabel("<b>%s</b>" % title)
        box.addWidget(lab)
        inner = QHBoxLayout()
        inner.addWidget(field, 1)
        if copy_btn is None:
            copy_btn = QPushButton("コピー")
            if copy_cb is not None:
                copy_btn.clicked.connect(copy_cb)
        inner.addWidget(copy_btn, 0)
        box.addLayout(inner)
        return box

    # ---- スマホ用の URL -------------------------------------------------
    def _mobile_urls(self):
        """スマホで開く URL。家の中のぶんが先、出来ていればネット越しも。"""
        urls = []
        for c in self._lan_codes:
            u = mobile_url_of(c)
            if u and u not in urls:
                urls.append(u)
        if self._net_code:
            u = mobile_url_of(self._net_code)
            if u and u not in urls:
                urls.append(u)
        return urls

    def _sync_mobile(self):
        urls = self._mobile_urls()
        self.box_mobile.setPlainText("\n".join(urls))
        self.box_mobile.fit(max(1, len(urls)))
        solo = [u + SOLO_QUERY for u in urls]
        self.box_solo.setPlainText("\n".join(solo))
        self.box_solo.fit(max(1, len(solo)))

    def set_net_code(self, code):
        """入口ができた。招待コードを入れてコピーを押せるようにする。"""
        self._net_code = str(code or "")
        self.bar.setValue(100)
        self.bar.setFormat("入口ができました")
        self.box_net.setPlainText(self._net_code)
        self.btn_copy.setEnabled(bool(self._net_code))
        self._sync_mobile()

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
        self._sync_mobile()

    # ---- コピー ---------------------------------------------------------
    @staticmethod
    def _copy_first(field):
        txt = field.toPlainText().strip().split("\n")
        if txt and txt[0]:
            QApplication.clipboard().setText(txt[0])

    def _copy(self):
        if self._net_code:
            QApplication.clipboard().setText(self._net_code)

    def _copy_lan(self):
        self._copy_first(self.box_lan)

    def _copy_mobile(self):
        self._copy_first(self.box_mobile)

    def _copy_solo(self):
        self._copy_first(self.box_solo)
