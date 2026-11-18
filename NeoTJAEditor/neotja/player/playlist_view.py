"""選曲の「プレイリスト」の部品(v15-design/PLAYLIST_SPEC.md)。

一覧の1行(PlaylistRow)・ジャケットの枠(PlaylistJacket)・作成/編集の窓
(PlaylistDialog)。並べる・開くのは LibraryView。
"""

import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from neotja.player import playlists, song_lists, thumbs
from neotja.start_screen import ElidedText, GlassCard

#: 一覧の1行の高さと、その中のジャケットの大きさ。
ROW_HEIGHT = 112
JACKET_PX = 88
#: 作成/編集の窓のジャケットの見本の大きさ。
DIALOG_JACKET_PX = 132


def _ui_font(px, weight=QFont.DemiBold):
    from neotja.player.difficulty import thin_font
    return thin_font(px, weight)


def load_jacket_pixmap(image_path, px):
    """絵を正方形に切って px の2倍で持つ。同じ (道, 更新日時, 大きさ) は覚えておく。"""
    key = thumbs.thumb_key(image_path)
    if key is None:
        return None
    key = key + ("playlist", px)
    pm = thumbs.CACHE.get(key)
    if pm is None:
        img = thumbs.load_thumb_image(image_path, size=px * 2)
        pm = thumbs.CACHE.put(key, img) if img is not None else None
    return pm


def playlist_jacket_pixmap(pl, cfg, px=JACKET_PX):
    """プレイリストのジャケット。無ければ1曲目のジャケット。どちらも無ければ None。"""
    p = playlists.jacket_path(pl)
    if p:
        pm = load_jacket_pixmap(p, px)
        if pm is not None:
            return pm
    items = (pl or {}).get("items") or []
    if items:
        from neotja.chart_art import existing_art
        try:
            art = existing_art(thumbs.art_snapshot(cfg), items[0]["path"], "jacket")
        except Exception:  # noqa: BLE001
            art = ""
        if art:
            return load_jacket_pixmap(art, px)
    return None


class PlaylistJacket(QWidget):
    """正方形のジャケット。無ければ No Image の枠(難易度選択などと同じ形)。"""

    def __init__(self, px, parent=None):
        super().__init__(parent)
        self.setFixedSize(px, px)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.pixmap = None

    def set_pixmap(self, pm):
        self.pixmap = pm if (pm is not None and not pm.isNull()) else None
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.pixmap is not None:
            p.drawPixmap(r, self.pixmap, QRectF(self.pixmap.rect()))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.2))
            p.setBrush(QColor(10, 18, 32, 150))
            p.drawRect(r)
            p.setFont(_ui_font(max(9, self.width() * 0.17)))
            p.setPen(QColor(235, 240, 248))
            p.drawText(r, Qt.AlignCenter, "No\nImage")
        p.end()


class PlaylistRow(GlassCard):
    """プレイリストの1行。押すと開く、右クリックで 編集 / 削除。"""

    opened = Signal(str)
    menu_requested = Signal(str, object)   # (id, 画面の位置)

    def __init__(self, pl, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.pid = pl["id"]
        self.pl = pl
        self.setObjectName("playlistRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 12, 18, 12)
        h.setSpacing(18)
        self.jacket = PlaylistJacket(JACKET_PX)
        h.addWidget(self.jacket, 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addStretch(1)
        self.title = ElidedText(pl.get("title") or "", "fg_bright", 19, bold=True, elide=True)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        texts.addWidget(self.title)
        self.desc = ElidedText(pl.get("description") or "", "fg_dim", 13, elide=True)
        self.desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.desc.setVisible(bool(self.desc.text()))
        texts.addWidget(self.desc)
        texts.addStretch(1)
        h.addLayout(texts, 1)
        arrow = QLabel("›")
        arrow.setObjectName("chevron")
        h.addWidget(arrow)
        self.setToolTip(" ".join(pl.get("description", "").split()))

    def matches(self, q):
        q = song_lists.normalize(q).strip()
        return (not q or q in song_lists.normalize(self.pl.get("title", ""))
                or q in song_lists.normalize(self.pl.get("description", "")))

    def refresh_theme(self):
        self.title.refresh()
        self.desc.refresh()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.setFocus()
            self.opened.emit(self.pid)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def contextMenuEvent(self, ev):
        self.menu_requested.emit(self.pid, ev.globalPos())
        ev.accept()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.opened.emit(self.pid)
            ev.accept()
            return
        super().keyPressEvent(ev)


# ----------------------------------------------------------------------
#: 作成/編集の窓の見た目(録画の背景の窓 RecordBackgroundDialog と同じ色・形)。
DIALOG_QSS = """
QDialog#PlaylistDialog { background: #101925; color: #e5edf7; }
#PlaylistDialog QWidget { color: #e5edf7; font-family: "Yu Gothic UI"; font-size: 13px; }
#PlaylistDialog QLabel { background: transparent; border: none; }
#PlaylistDialog QLabel#plHead { font-size: 19px; font-weight: bold; color: #ffffff; }
#PlaylistDialog QLabel#plField { color: #b9d9ed; font-weight: 600; }
#PlaylistDialog QLineEdit, #PlaylistDialog QPlainTextEdit {
    background: #0c1521; border: 1px solid #3b536d; border-radius: 2px;
    padding: 5px 7px; selection-background-color: #356d9b; font-size: 14px;
}
#PlaylistDialog QLineEdit:focus, #PlaylistDialog QPlainTextEdit:focus { border-color: #8bcfe5; }
#PlaylistDialog QPushButton {
    background: #19283a; border: 1px solid #54758d; border-radius: 3px;
    padding: 7px 16px; min-height: 20px; font-weight: bold;
}
#PlaylistDialog QPushButton:hover { background: #233d54; border-color: #8ad9ee; }
#PlaylistDialog QPushButton:pressed { background: #101d2c; }
#PlaylistDialog QPushButton:default { border-color: #8ad9ee; background: #23415a; }
#PlaylistDialog QPushButton:disabled { color: #687789; border-color: #2b3b4d; background: #172230; }
"""


class PlaylistDialog(QDialog):
    """プレイリストの作成/編集。タイトル・説明・ジャケット。

    ジャケットの絵を選ぶ口は pick_image(差し替えられる。試験はファイルの
    窓を出さない)。決まった値は values()。"""

    def __init__(self, pl=None, parent=None, cfg=None):
        super().__init__(parent)
        self.setObjectName("PlaylistDialog")
        editing = pl is not None
        self.setWindowTitle("プレイリストを編集" if editing else "プレイリストを作成")
        self.setModal(True)
        self.setStyleSheet(DIALOG_QSS)
        self.resize(620, 470)
        self._pl = pl
        self._cfg = cfg or {}
        #: 選び直した絵(None なら替えない)・外したか。
        self.jacket_src = None
        self.clear_jacket = False

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 20)
        v.setSpacing(0)
        head = QLabel(self.windowTitle())
        head.setObjectName("plHead")
        v.addWidget(head)
        v.addSpacing(18)

        body = QHBoxLayout()
        body.setSpacing(24)
        v.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._field("タイトル"))
        self.ed_title = QLineEdit((pl or {}).get("title", ""))
        self.ed_title.setMinimumHeight(34)
        left.addWidget(self.ed_title)
        left.addSpacing(12)
        left.addWidget(self._field("説明"))
        self.ed_desc = QPlainTextEdit((pl or {}).get("description", ""))
        self.ed_desc.setTabChangesFocus(True)
        left.addWidget(self.ed_desc, 1)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(self._field("ジャケット"))
        self.jacket = PlaylistJacket(DIALOG_JACKET_PX)
        right.addWidget(self.jacket, 0, Qt.AlignLeft)
        right.addSpacing(6)
        self.btn_pick = QPushButton("画像を選択…")
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.clicked.connect(self._pick)
        right.addWidget(self.btn_pick)
        self.btn_clear = QPushButton("外す")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        right.addWidget(self.btn_clear)
        right.addStretch(1)
        body.addLayout(right)

        v.addSpacing(20)
        bh = QHBoxLayout()
        bh.setSpacing(10)
        bh.addStretch(1)
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        bh.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton("保存" if editing else "作成")
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        bh.addWidget(self.btn_ok)
        v.addLayout(bh)

        self.ed_title.textChanged.connect(self._sync)
        self._sync()
        self._show_jacket()
        self.ed_title.setFocus()

    @staticmethod
    def _field(text):
        lbl = QLabel(text)
        lbl.setObjectName("plField")
        return lbl

    # ------------------------------------------------------------------
    def pick_image(self):
        """絵のファイルを選ぶ。やめたら ""。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "ジャケット", "",
            "画像 (%s);;すべてのファイル (*.*)"
            % " ".join("*" + e for e in playlists.IMAGE_EXTS))
        return path

    def set_jacket_source(self, path):
        """絵を選んだ(pick_image の結果。試験からも)。"""
        if not path or not os.path.isfile(path):
            return
        self.jacket_src = path
        self.clear_jacket = False
        self._show_jacket()

    def _pick(self):
        self.set_jacket_source(self.pick_image())

    def _clear(self):
        self.jacket_src = None
        self.clear_jacket = True
        self._show_jacket()

    def _show_jacket(self):
        px = DIALOG_JACKET_PX
        if self.jacket_src:
            pm = load_jacket_pixmap(self.jacket_src, px)
        elif self.clear_jacket or self._pl is None:
            pm = None
        else:
            pm = playlist_jacket_pixmap(self._pl, self._cfg, px)
        self.jacket.set_pixmap(pm)
        has_own = bool(self.jacket_src) or (
            not self.clear_jacket and self._pl is not None
            and bool(playlists.jacket_path(self._pl)))
        self.btn_clear.setEnabled(has_own)

    def _sync(self):
        self.btn_ok.setEnabled(bool(self.ed_title.text().strip()))

    def values(self):
        """(タイトル, 説明, 新しい絵 または None, 絵を外すか)。"""
        return (self.ed_title.text().strip(), self.ed_desc.toPlainText().strip(),
                self.jacket_src, self.clear_jacket)
