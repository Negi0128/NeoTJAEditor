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


class CourseMarkIcon(QWidget):
    """プレイリストの曲の行の右に出す、保存したコースのマーク(小さく)。"""

    def __init__(self, course, px=34, parent=None):
        super().__init__(parent)
        self.setFixedSize(px, px)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.course = course
        self.setToolTip(SLOT_LABEL.get(course, course or ""))

    def set_course(self, course):
        self.course = course
        self.setToolTip(SLOT_LABEL.get(course, course or ""))
        self.update()

    def paintEvent(self, ev):
        if not self.course:
            return
        from neotja.player.difficulty import FALLBACK_MARK, course_mark
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        pm = course_mark(self.course) if self.course in FALLBACK_MARK else None
        if pm is not None:
            p.drawPixmap(r, pm, QRectF(pm.rect()))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(FALLBACK_MARK.get(self.course, "#888888")))
            p.drawEllipse(r.adjusted(4, 4, -4, -4))
        p.end()


#: コースの呼び名(ツールチップ用。裏は「おに(裏)」)。
SLOT_LABEL = {"Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい",
              "Oni": "おに", "Edit": "おに(裏)"}


class CoursePicker(QDialog):
    """追加する・変えるときのコース選び。譜面にあるコースだけを、難易度選択と
    同じカード(CourseCard)を小さくして並べる。押したら決まり。"""

    SCALE = 0.62

    def __init__(self, title, courses, current=None, parent=None):
        super().__init__(parent)
        from neotja.player.difficulty import CourseCard
        self.setObjectName("CoursePicker")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setStyleSheet(DIALOG_QSS.replace("PlaylistDialog", "CoursePicker"))
        order = ("Easy", "Normal", "Hard", "Oni", "Edit")
        levels = dict(courses or [])
        self.keys = [k for k in order if k in levels]
        self.course = current if current in levels else default_course_of(courses)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 20, 26, 18)
        v.setSpacing(0)
        head = ElidedText(title or "", "fg_bright", 17, bold=True, elide=True)
        head.setFixedWidth(max(260, int(236 * self.SCALE + 8) * len(self.keys) - 20))
        v.addWidget(head)
        v.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.cards = {}
        for k in self.keys:
            c = CourseCard(k)
            c.scale = self.SCALE
            c.setFixedSize(int(236 * self.SCALE), int(232 * self.SCALE))
            c.set_state(k, True, levels.get(k), k == self.course)
            c.setToolTip(SLOT_LABEL.get(k, k))
            c.clicked.connect(self.pick)
            row.addWidget(c)
            self.cards[k] = c
        v.addLayout(row)
        v.addSpacing(8)
        bh = QHBoxLayout()
        bh.addStretch(1)
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        bh.addWidget(self.btn_cancel)
        v.addLayout(bh)
        self.setFocusPolicy(Qt.StrongFocus)

    def pick(self, key):
        if key in self.cards:
            self.course = key
            self.accept()

    def _move(self, d):
        if not self.keys:
            return
        i = self.keys.index(self.course) if self.course in self.keys else 0
        self.course = self.keys[max(0, min(len(self.keys) - 1, i + d))]
        for k, c in self.cards.items():
            c.selected = k == self.course
            c.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key_Left, Qt.Key_Right):
            self._move(-1 if k == Qt.Key_Left else 1)
            return
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.course:
            self.accept()
            return
        super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(139, 217, 238, 150), 1))
        p.setBrush(QColor("#101925"))
        p.drawRoundedRect(r, 4, 4)
        p.end()


def default_course_of(courses):
    return playlists.default_course(courses)


class AddModeBar(QWidget):
    """「曲を追加」のあいだ右の上に出す帯。追加先の題と「完了」だけ。"""

    done = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("addModeBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(52)
        h = QHBoxLayout(self)
        h.setContentsMargins(40, 0, 40, 0)
        h.setSpacing(14)
        self.plus = QLabel("＋")
        self.plus.setObjectName("addModePlus")
        h.addWidget(self.plus)
        self.title = ElidedText("", "fg_bright", 15, bold=True, elide=True)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h.addWidget(self.title, 1)
        self.btn_done = QPushButton("完了")
        self.btn_done.setObjectName("flat")
        self.btn_done.setCursor(Qt.PointingHandCursor)
        self.btn_done.clicked.connect(self.done)
        h.addWidget(self.btn_done)

    def set_title(self, text):
        self.title._text = text
        self.title.update()


#: 「連続再生」の板の見た目(平ら・暗い半透明・細い薄シアンの縁・ほぼ四角・太い Yu Gothic UI)。
PLAY_PANEL_QSS = """
QFrame#playPanel { background-color: rgba(12, 22, 36, 240); border: 1px solid rgba(139, 217, 238, 170);
                   border-radius: 3px; }
#playPanel QWidget { color: #e5edf7; font-family: "Yu Gothic UI"; font-size: 14px; font-weight: bold;
                     background: transparent; }
#playPanel QLabel#ppHead { font-size: 17px; color: #ffffff; }
#playPanel QLabel#ppField { color: #b9d9ed; font-size: 13px; }
#playPanel QSpinBox { background: #0c1521; border: 1px solid #3b536d; border-radius: 2px;
                      padding: 4px 6px; min-height: 22px; font-size: 15px; }
#playPanel QSpinBox:focus { border-color: #8bcfe5; }
#playPanel QSpinBox:disabled { color: #687789; border-color: #29394b; }
#playPanel QPushButton { background-color: rgba(16, 28, 44, 190); border: 1px solid rgba(139, 217, 238, 120);
                         border-radius: 3px; padding: 7px 16px; }
#playPanel QPushButton:hover { background-color: rgba(35, 61, 84, 215); border-color: #8ad9ee; }
#playPanel QPushButton:checked { background-color: rgba(52, 120, 160, 200); border-color: #8ad9ee; color: #ffffff; }
#playPanel QPushButton#ppStart { font-size: 16px; padding: 9px 16px; border-color: #8ad9ee;
                                 background-color: rgba(35, 65, 90, 220); }
"""


class PlaylistPlayPanel(QWidget):
    """「連続再生」を押すと出る小さな板(窓ではなく選曲の中に重ねる)。
    範囲(何曲目から何曲目まで)・手動/自動・自動の待ち時間・「開始」。"""

    #: 開始 {"start": 0 から, "end": 0 から(含む), "auto": bool, "wait": 秒}。
    started = Signal(object)
    closed = Signal()

    def __init__(self, count, auto=False, wait=10, wait_range=(3, 60), parent=None):
        from PySide6.QtWidgets import QFrame, QSpinBox
        super().__init__(parent)
        self.setObjectName("playPanelScrim")
        # 板の外を押したら閉じる(後ろは押させない)。
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.frame = QFrame(self)
        self.frame.setObjectName("playPanel")
        self.frame.setStyleSheet(PLAY_PANEL_QSS)
        v = QVBoxLayout(self.frame)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        head = QLabel("連続再生")
        head.setObjectName("ppHead")
        v.addWidget(head)

        n = max(1, int(count))
        v.addWidget(self._field("範囲"))
        rh = QHBoxLayout()
        rh.setSpacing(8)
        self.sp_from = QSpinBox()
        self.sp_to = QSpinBox()
        for sp in (self.sp_from, self.sp_to):
            sp.setRange(1, n)
            sp.setFixedWidth(84)
            sp.setAlignment(Qt.AlignRight)
        self.sp_from.setValue(1)
        self.sp_to.setValue(n)
        self.sp_from.valueChanged.connect(lambda x: self.sp_to.setValue(max(x, self.sp_to.value())))
        self.sp_to.valueChanged.connect(lambda x: self.sp_from.setValue(min(x, self.sp_from.value())))
        rh.addWidget(self.sp_from)
        rh.addWidget(QLabel("〜"))
        rh.addWidget(self.sp_to)
        rh.addStretch(1)
        v.addLayout(rh)

        v.addWidget(self._field("再生"))
        mh = QHBoxLayout()
        mh.setSpacing(0)
        self.btn_manual = QPushButton("手動")
        self.btn_auto = QPushButton("自動")
        for b in (self.btn_manual, self.btn_auto):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(78)
            mh.addWidget(b)
        self.btn_manual.clicked.connect(lambda: self.set_auto(False))
        self.btn_auto.clicked.connect(lambda: self.set_auto(True))
        mh.addSpacing(16)
        self.sp_wait = QSpinBox()
        lo, hi = wait_range
        self.sp_wait.setRange(int(lo), int(hi))
        try:
            self.sp_wait.setValue(int(wait))
        except (TypeError, ValueError):
            self.sp_wait.setValue(10)
        self.sp_wait.setFixedWidth(72)
        self.sp_wait.setAlignment(Qt.AlignRight)
        mh.addWidget(self.sp_wait)
        self.lbl_sec = QLabel("秒")
        mh.addSpacing(6)
        mh.addWidget(self.lbl_sec)
        mh.addStretch(1)
        v.addLayout(mh)

        v.addSpacing(6)
        self.btn_start = QPushButton("▶  開始")
        self.btn_start.setObjectName("ppStart")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.start)
        v.addWidget(self.btn_start)
        self.frame.setFixedWidth(330)
        self.frame.adjustSize()
        self.set_auto(auto)

    @staticmethod
    def _field(text):
        lbl = QLabel(text)
        lbl.setObjectName("ppField")
        return lbl

    def set_auto(self, on):
        self.auto = bool(on)
        self.btn_manual.setChecked(not self.auto)
        self.btn_auto.setChecked(self.auto)
        self.sp_wait.setEnabled(self.auto)
        self.lbl_sec.setEnabled(self.auto)

    def options(self):
        return {"start": self.sp_from.value() - 1, "end": self.sp_to.value() - 1,
                "auto": self.auto, "wait": self.sp_wait.value()}

    def start(self):
        self.started.emit(self.options())
        self.close_panel()

    def open_at(self, anchor):
        """anchor(ボタン)の下に、右をそろえて出す。親いっぱいに広がって外の押下を受ける。"""
        par = self.parentWidget()
        self.setGeometry(par.rect())
        self.frame.adjustSize()
        br = anchor.mapTo(par, anchor.rect().bottomRight())
        x = max(8, min(br.x() - self.frame.width(), par.width() - self.frame.width() - 8))
        self.frame.move(int(x), int(br.y() + 8))
        self.show()
        self.raise_()
        self.btn_start.setFocus()

    def close_panel(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def mousePressEvent(self, ev):
        if not self.frame.geometry().contains(ev.position().toPoint()):
            self.close_panel()
        ev.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close_panel()
            ev.accept()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.start()
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
