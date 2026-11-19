"""連続再生の最初に出すプレイリストの照会画面(利用者の指定 2026-11-19)。

左にプレイリストのジャケット(無ければ1曲目のジャケット、それも無ければ
No Image)・題・説明、右に選んだ範囲の曲(番号・曲名・コースのマーク・★)と
曲数、「再生する」(平ら・ドン)と「やめる」。自動の連続再生でもここは
自分で進まない。Enter / Space = 再生する、Esc = やめる。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from neotja.player.difficulty import BASE_H, BASE_W, NAVY, title_font
from neotja.player.intro import PL_BUTTON_QSS
from neotja.player.playlist_view import CourseMarkIcon, PlaylistJacket

ROW_H = 56


class _SongLine(QWidget):
    """曲の1行(番号・曲名・コースのマーク・★)。見つからない曲は灰色。"""

    def __init__(self, number, title, course, level, missing, parent=None):
        super().__init__(parent)
        self.missing = bool(missing)
        self.setObjectName("ovRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("missing", self.missing)
        self.setFixedHeight(ROW_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 4, 18, 4)
        h.setSpacing(14)
        self.num = QLabel(str(number))
        self.num.setObjectName("ovNum")
        self.num.setFixedWidth(34)
        self.num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.num)
        self.title = QLabel(title)
        self.title.setObjectName("ovTitle")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(self.title, 1)
        if self.missing:
            self.mark = None
            self.star = QLabel("ファイルなし")
            self.star.setObjectName("ovMissing")
        else:
            self.mark = CourseMarkIcon(course, px=40)
            h.addWidget(self.mark)
            self.star = QLabel("★%s" % (level if level is not None else "—"))
            self.star.setObjectName("ovStar")
        self.star.setMinimumWidth(64)
        self.star.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.star)
        for w in (self.num, self.title, self.star):
            w.setProperty("missing", self.missing)


OV_QSS = """
QWidget { font-family: "Yu Gothic UI"; color: #ffffff; }
QLabel { background: transparent; }
QLabel#ovDesc { color: #c6d6e6; font-size: 16px; }
QLabel#ovCount { color: #b9d9ed; font-size: 18px; font-weight: bold; }
QWidget#ovRow { background-color: rgba(12, 22, 36, 170); border: 1px solid rgba(139, 217, 238, 70);
                border-radius: 3px; }
QWidget#ovRow[missing="true"] { background-color: rgba(12, 22, 36, 90); border-color: rgba(255, 255, 255, 30); }
QLabel#ovNum { color: #8fa3c0; font-size: 16px; font-weight: bold; }
QLabel#ovTitle { font-size: 19px; font-weight: bold; }
QLabel#ovStar { font-size: 19px; font-weight: bold; }
QLabel[missing="true"] { color: #687789; }
QLabel#ovMissing { font-size: 14px; font-weight: bold; }
QScrollArea { background: transparent; border: none; }
QWidget#ovListInner { background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: rgba(139, 217, 238, 90); min-height: 30px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class PlaylistOverviewPage(QWidget):
    """プレイリストの照会画面。"""

    play_requested = Signal()
    quit_requested = Signal()
    sound_requested = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(OV_QSS)
        outer = QHBoxLayout(self)
        self._outer = outer
        outer.setContentsMargins(90, 70, 90, 60)
        outer.setSpacing(70)

        left = QVBoxLayout()
        left.setSpacing(18)
        self.jacket = PlaylistJacket(360)
        left.addWidget(self.jacket, 0, Qt.AlignLeft)
        self.lbl_title = QLabel("")
        self.lbl_title.setObjectName("ovHead")
        self.lbl_title.setWordWrap(True)
        left.addWidget(self.lbl_title)
        self.lbl_desc = QLabel("")
        self.lbl_desc.setObjectName("ovDesc")
        self.lbl_desc.setWordWrap(True)
        left.addWidget(self.lbl_desc)
        left.addStretch(1)
        self._left = QWidget()
        self._left.setLayout(left)
        self._left.setFixedWidth(400)
        outer.addWidget(self._left)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("ovCount")
        right.addWidget(self.lbl_count)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("ovListInner")
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 10, 0)
        self._list.setSpacing(8)
        self._list.addStretch(1)
        self.scroll.setWidget(inner)
        right.addWidget(self.scroll, 1)
        bh = QHBoxLayout()
        bh.setSpacing(16)
        self.btn_quit = QPushButton("やめる")
        self.btn_play = QPushButton("再生する")
        for b in (self.btn_quit, self.btn_play):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self.request_play)
        self.btn_quit.clicked.connect(self.request_quit)
        bh.addWidget(self.btn_quit, 1)
        bh.addWidget(self.btn_play, 2)
        right.addLayout(bh)
        outer.addLayout(right, 1)
        self.rows = []
        self._apply_scale()

    # ------------------------------------------------------------------
    def set_playlist(self, pl, entries, jacket_pixmap=None):
        """pl: プレイリスト。entries: [(番号, 曲名, コース, レベル, 見つからないか)]。"""
        self.lbl_title.setText(pl.get("title", ""))
        desc = pl.get("description", "")
        self.lbl_desc.setText(desc)
        self.lbl_desc.setVisible(bool(desc))
        self.jacket.set_pixmap(jacket_pixmap)
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for e in entries:
            row = _SongLine(*e)
            self._list.insertWidget(self._list.count() - 1, row)
            self.rows.append(row)
        playable = sum(1 for e in entries if not e[4])
        self.lbl_count.setText("%d曲" % playable)
        self.scroll.verticalScrollBar().setValue(0)
        self._apply_scale()

    def request_play(self):
        self.sound_requested.emit("don")
        self.play_requested.emit()

    def request_quit(self):
        self.sound_requested.emit("pi")
        self.quit_requested.emit()

    def keyPressEvent(self, ev):
        if not ev.isAutoRepeat():
            k = ev.key()
            if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.request_play()
            elif k == Qt.Key_Escape:
                self.request_quit()
        ev.accept()

    # ------------------------------------------------------------------
    def _s(self):
        return max(0.5, min(self.width() / BASE_W, self.height() / BASE_H))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_scale()

    def _apply_scale(self):
        s = self._s()
        self._outer.setContentsMargins(int(90 * s), int(70 * s), int(90 * s), int(60 * s))
        self._outer.setSpacing(int(70 * s))
        side = int(360 * s)
        self.jacket.setFixedSize(side, side)
        self._left.setFixedWidth(int(400 * s))
        # 題は曲名と同じ書体(勘亭流、無ければ太い UI 書体)。親の QSS が字の
        # 大きさを上書きするので、setFont ではなく自分の QSS に書く。
        fam = title_font(46 * s).family()
        self.lbl_title.setStyleSheet('QLabel { font-family: "%s"; font-size: %dpx; }'
                                     % (fam, max(14, round(46 * s))))
        self.lbl_desc.setStyleSheet("QLabel { font-size: %dpx; }" % max(11, round(18 * s)))
        self.lbl_count.setStyleSheet("QLabel { font-size: %dpx; }" % max(11, round(20 * s)))
        self.btn_play.setFixedHeight(int(96 * s))
        self.btn_quit.setFixedHeight(int(96 * s))
        self.btn_play.setStyleSheet(PL_BUTTON_QSS % {
            "bw": max(1, round(1.5 * s)), "r": max(2, round(3 * s)),
            "px": max(12, round(34 * s)), "ls": max(1, round(6 * s))})
        self.btn_quit.setStyleSheet(PL_BUTTON_QSS % {
            "bw": 1, "r": max(2, round(3 * s)),
            "px": max(11, round(24 * s)), "ls": max(1, round(3 * s))})
        for r in self.rows:
            r.setFixedHeight(int(ROW_H * s))

    def paintEvent(self, ev):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, NAVY.lighter(108))
        grad.setColorAt(1, NAVY.darker(115))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()
