# -*- coding: utf-8 -*-
"""歌詞の帯にジャケットの絵を付ける窓(利用者の承認 2026-11-13、見本は
lyric_jacket_menu.png)。

作譜ペインの歌詞の帯を右クリック →「ジャケットを挿入…」で出る。窓は
「選んだ絵の道を返す」だけで、対応表を書き換えるのは呼ぶ側
(MainWindow.edit_lyric_jacket)。

**付ける単位は歌詞の文字**(利用者の決定 2026-11-13)。見本にあった
「この帯だけ / 同じ歌詞すべて」の選択は置かない — 同じ文字の歌詞はいつも
同じ絵になるので、選ばせても片方しか意味が無い。代わりに、いくつの帯に
効くかを薄い字で添える(思ったより広く効いて驚かないように)。

見た目は起動画面と同じ「ガラスと光」(dialogs/glass.py)。
"""
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from neotja.dialogs import set_default_button
from neotja.dialogs.glass import apply_glass, card_title, field_label, make_card
from neotja.lyric_jacket import IMAGE_FILTER
from neotja.theme import COLORS

#: 見本の正方形の一辺(px)。
PREVIEW_PX = 120
#: 見本の角丸(px)。カードの角丸(12)より少し小さく。
PREVIEW_RADIUS = 10


def square_pixmap(path, side, radius=0, dpr=1.0):
    """絵の真ん中から取れる一番大きい正方形を side に縮め、角を丸めて返す。

    録画(recorder._bake_art)と同じ切り方。読めなければ None。
    作譜ペインの小さな絵もこれを使う(切り方が窓と食い違わないように)。"""
    from PySide6.QtGui import QImage
    img = QImage(str(path or ""))
    if img.isNull():
        return None
    sq = min(img.width(), img.height())
    if sq <= 0:
        return None
    img = img.copy((img.width() - sq) // 2, (img.height() - sq) // 2, sq, sq)
    px = max(1, int(round(side * dpr)))
    img = img.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(px, px)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if radius > 0:
        clip = QPainterPath()
        r = radius * dpr
        clip.addRoundedRect(QRectF(0, 0, px, px), r, r)
        p.setClipPath(clip)
    p.drawImage(0, 0, img)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class _Preview(QLabel):
    """正方形の見本。絵が無いときは点線の枠と「画像なし」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PREVIEW_PX, PREVIEW_PX)
        self._pm = None

    def set_path(self, path):
        self._pm = (square_pixmap(path, PREVIEW_PX, PREVIEW_RADIUS,
                                  self.devicePixelRatioF())
                    if path else None)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self._pm is not None:
            p.drawPixmap(0, 0, self._pm)
            return
        pen = QPen(QColor(COLORS["fg_dim"]))
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, PREVIEW_RADIUS, PREVIEW_RADIUS)
        p.drawText(r, Qt.AlignCenter, "画像なし")


class LyricJacketDialog(QDialog):
    """text の歌詞に付ける絵を選ぶ。OK のあと path() が選んだ道を返す。"""

    def __init__(self, text, current="", count=0, start_dir="", parent=None):
        super().__init__(parent)
        self._text = str(text or "")
        self._path = str(current or "")
        self._start_dir = str(start_dir or "")
        verb = "変更" if self._path else "挿入"
        title = "ジャケットを%s — 歌詞「%s」" % (verb, self._text)
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)
        apply_glass(self)
        layout.addWidget(card_title(title))

        card, grid = make_card()
        row = QHBoxLayout()
        row.setSpacing(16)
        self.preview = _Preview()
        row.addWidget(self.preview, 0, Qt.AlignTop)

        side = QVBoxLayout()
        side.setSpacing(4)
        side.addWidget(field_label("画像"))
        self.lbl_name = QLabel()
        self.lbl_name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        side.addWidget(self.lbl_name)
        self.lbl_size = QLabel()
        self.lbl_size.setObjectName("dim")
        side.addWidget(self.lbl_size)
        side.addSpacing(10)
        self.btn_pick = QPushButton("画像を選ぶ…")
        self.btn_pick.clicked.connect(self._browse)
        pick_row = QHBoxLayout()
        pick_row.addWidget(self.btn_pick)
        pick_row.addStretch(1)
        side.addLayout(pick_row)
        side.addStretch(1)
        row.addLayout(side, 1)
        grid.addLayout(row, 0, 0, 1, 2)

        # 付ける単位は歌詞の文字。いくつの帯に効くかだけ添える(上の説明)。
        self.lbl_note = QLabel("同じ歌詞 %d か所に効きます" % max(1, int(count)))
        self.lbl_note.setObjectName("dim")
        grid.addWidget(self.lbl_note, 1, 0, 1, 2)
        layout.addWidget(card)
        self.setMinimumWidth(440)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("OK")
        self.btn_ok.setObjectName("accentButton")
        self.btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_ok)
        layout.addLayout(btn_row)
        set_default_button(self, self.btn_ok)
        self._show()

    def path(self):
        return self._path

    def set_path(self, path):
        """絵を選んだことにする(ファイル選択の窓のあと。テストからも呼ぶ)。"""
        self._path = str(path or "")
        self._show()

    def _show(self):
        path = self._path
        self.preview.set_path(path)
        if path:
            self.lbl_name.setText(os.path.basename(path))
            self.lbl_name.setToolTip(path)
            # 大きさは読むだけ(中身は開かない)。読めなければ出さない。
            size = QImageReader(path).size()
            if size.isValid():
                self.lbl_size.setText("%d × %d" % (size.width(), size.height()))
            elif not os.path.exists(path):
                self.lbl_size.setText("見つかりません")
            else:
                self.lbl_size.setText("")
        else:
            self.lbl_name.setText("まだ選んでいません")
            self.lbl_name.setToolTip("")
            self.lbl_size.setText("")
        # 絵が無いまま OK を押しても何も起きないので、押せなくしておく
        # (外すのは右クリックの「ジャケットを外す」)。
        self.btn_ok.setEnabled(bool(path))

    def _browse(self):
        start = self._path or self._start_dir
        got, _ = QFileDialog.getOpenFileName(
            self, "ジャケットの画像", start,
            IMAGE_FILTER + ";;すべて (*.*)")
        if got:
            self.set_path(got)
