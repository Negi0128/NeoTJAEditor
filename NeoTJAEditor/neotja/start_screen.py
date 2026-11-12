"""起動したときの最初の画面(利用者の指定 2026-11-12、見本は start_mock_A2)。

ファイルを何も開いていない起動直後だけ、中央(TJA と再生画面の分割)の
上に被せて出す。何か開くか新規作成が決まれば消え、以後は戻ってこない
(「閉じる」は無いので、戻す道も作っていない)。

中身は 4 つ。アプリ名と版、「新規作成」「TJA を開く」の2枚のカード、
最近使った項目、ヘルプ/環境設定。ここでは**並べて合図を出すだけ**で、
実際に開く・作る・設定を出すのは MainWindow の既存の口へ繋ぐ(配線を
二重に持たないため)。

最近使った項目は、曲名とパスを**モザイクにして**出す。配信や画面共有で
起動したとき、作りかけの曲名やフォルダ名が映ってしまうため。「表示する」
で全部見えるようにする。一方通行で、設定にも残さない — 起動し直せば
また隠れる。モザイクは文字をいったん絵に描いて 8 分の 1 に縮め、
最近傍で元の大きさへ戻す(見本と同じ作り)。ぼかしでなくモザイクなのは、
ぼかしだと短い曲名が読めてしまうから。
"""

import os
import datetime

from PySide6.QtCore import Qt, Signal, QSize, QRect
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
    QScrollArea, QSizePolicy,
)

from neotja.constants import APP_NAME, VERSION
from neotja.theme import COLORS

#: 画面の中央の列の幅。見本(1180)に合わせる。窓が狭ければ縮む。
COLUMN_WIDTH = 1180
#: 最近使った項目に出す上限。設定には 10 件まで残るが、画面には 8 件。
RECENT_ROWS = 8
#: モザイクの粗さ(縮小の倍率)。
MOSAIC_BLOCK = 8
#: ヘッダを読むのに頭から何バイトまで見るか。TITLE: は先頭にあり、COURSE:
#: は本文の途中にも出るので、譜面全体を読む。ただし異常に大きいファイルで
#: 起動が遅くならないよう上限を置く。
HEADER_READ_LIMIT = 2 * 1024 * 1024

#: COURSE: の表記ゆれを、画面に出す名前へそろえる。TJA の仕様では数字と
#: 英語名のどちらも許される。
COURSE_NAMES = {
    "0": "Easy", "easy": "Easy",
    "1": "Normal", "normal": "Normal",
    "2": "Hard", "hard": "Hard",
    "3": "Oni", "oni": "Oni",
    "4": "Edit", "edit": "Edit", "ura": "Edit",
    "5": "Tower", "tower": "Tower",
    "6": "Dan", "dan": "Dan",
}
#: 画面に並べる順(難しいほうから)。知らない名前は最後。
COURSE_ORDER = ["Edit", "Oni", "Hard", "Normal", "Easy", "Tower", "Dan"]
#: 難易度の札の色。本家の色づかい(かんたん=赤、ふつう=緑、むずかしい=青、
#: おに=紫)に合わせ、テーマの色の中から近いものを選ぶ。
COURSE_COLOR_KEY = {
    "Easy": "don", "Normal": "ok", "Hard": "accent", "Oni": "kusudama",
    "Edit": "cmd",
}


def scan_tja_header(path):
    """譜面の TITLE: と、どの COURSE: があるかを手早く読む。

    戻り値は (題名, 難易度名の一覧)。題名が無ければファイル名(拡張子なし)。
    文字コードはアプリで開くときと同じ順(cp932 → utf-8-sig)で試し、
    どちらでも読めなければ化けた字を '?' に置き換えて読む — ここは一覧を
    出すだけなので、読めない1文字で行ごと落とす理由が無い。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as f:
            raw = f.read(HEADER_READ_LIMIT)
    except OSError:
        return stem, []
    text = None
    for enc in ("cp932", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("cp932", errors="replace")
    text = text.lstrip("﻿")
    title = ""
    courses = []
    has_start = False
    for line in text.splitlines():
        s = line.strip()
        up = s.upper()
        if not title and up.startswith("TITLE:"):
            title = s[6:].strip()
        elif up.startswith("COURSE:"):
            key = s[7:].strip().lower()
            name = COURSE_NAMES.get(key, s[7:].strip() or "?")
            if name not in courses:
                courses.append(name)
        elif up.startswith("#START"):
            has_start = True
    # COURSE: を書かない譜面は仕様上おに扱い。本文があるのに札が1つも
    # 無いと「壊れた譜面」に見えるので、そのときだけ補う。
    if not courses and has_start:
        courses.append("Oni")
    courses.sort(key=lambda n: (COURSE_ORDER.index(n) if n in COURSE_ORDER
                                else len(COURSE_ORDER), n))
    return (title or stem), courses


def format_mtime(path):
    """ファイルの更新日時を「2026/11/12 09:41」の形で。読めなければ空。"""
    try:
        t = os.path.getmtime(path)
    except OSError:
        return ""
    return datetime.datetime.fromtimestamp(t).strftime("%Y/%m/%d %H:%M")


class MosaicText(QWidget):
    """曲名・パス用の文字。隠している間はモザイクで描く。

    QLabel に描かせてから絵を加工する手もあるが、隠れている間だけ別の
    描き方をしたいだけなので、自分で paintEvent を持つほうが短い。
    文字の色と大きさは COLORS とテーマ替え(refresh)に追従する。
    パスは幅に収まらなければ真ん中を省略する(ElideMiddle)。"""

    def __init__(self, text, color_key, px, bold=False, elide=False, parent=None):
        super().__init__(parent)
        self._text = text
        self._color_key = color_key
        self._px = px
        self._bold = bold
        self._elide = elide
        self._revealed = False
        self._cache = None       # (鍵, QPixmap)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        pol = QSizePolicy(QSizePolicy.Expanding if elide else QSizePolicy.Preferred,
                          QSizePolicy.Fixed)
        self.setSizePolicy(pol)
        self.setFocusPolicy(Qt.NoFocus)

    def text(self):
        return self._text

    def _font(self):
        f = QFont(self.font())
        f.setPixelSize(self._px)
        f.setBold(self._bold)
        return f

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + 2, fm.height() + 2)

    def minimumSizeHint(self):
        fm = QFontMetrics(self._font())
        # 省略できる相手は細くしてよい。曲名は最低限そのまま。
        w = 40 if self._elide else fm.horizontalAdvance(self._text) + 2
        return QSize(w, fm.height() + 2)

    def set_revealed(self, on):
        self._revealed = bool(on)
        self._cache = None
        self.update()

    def refresh(self):
        self._cache = None
        self.update()

    def _shown_text(self, width):
        if not self._elide:
            return self._text
        fm = QFontMetrics(self._font())
        return fm.elidedText(self._text, Qt.ElideMiddle, max(10, width))

    def paintEvent(self, ev):
        p = QPainter(self)
        font = self._font()
        color = QColor(COLORS.get(self._color_key, COLORS["fg"]))
        text = self._shown_text(self.width())
        if self._revealed:
            p.setFont(font)
            p.setPen(color)
            p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, text)
            p.end()
            return
        key = (text, self.width(), self.height(), color.name(), self._px)
        if self._cache is None or self._cache[0] != key:
            fm = QFontMetrics(font)
            w = max(1, min(self.width(), fm.horizontalAdvance(text) + 2))
            h = max(1, self.height())
            src = QPixmap(w, h)
            # 背景は透明。モザイク化で文字の色が周りの透明と混ざって、
            # 行の背景(カードの色)の上でちょうど薄く見える。
            src.fill(Qt.transparent)
            q = QPainter(src)
            q.setFont(font)
            q.setPen(color)
            q.drawText(QRect(0, 0, w, h), Qt.AlignLeft | Qt.AlignVCenter, text)
            q.end()
            small = src.scaled(max(1, w // MOSAIC_BLOCK), max(1, h // MOSAIC_BLOCK),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            big = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            self._cache = (key, big)
        p.drawPixmap(0, 0, self._cache[1])
        p.end()


class RecentRow(QFrame):
    """最近使った項目の1行。クリックか Enter で開く。"""

    activated = Signal(str)

    def __init__(self, path, title, courses, date_text, last=False, parent=None):
        super().__init__(parent)
        self.path = path
        self.setObjectName("recentRowLast" if last else "recentRow")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = MosaicText(title, "fg_bright", 14, bold=True)
        top.addWidget(self.title)
        self.chips = []
        for c in courses:
            chip = QLabel(c)
            chip.setObjectName("chip")
            chip.setProperty("course", c)
            top.addWidget(chip)
            self.chips.append(chip)
        top.addStretch(1)
        left.addLayout(top)
        self.path_label = MosaicText(path, "fg_dim", 12, elide=True)
        left.addWidget(self.path_label)
        h.addLayout(left, 1)
        self.date = QLabel(date_text)
        self.date.setObjectName("dim13")
        self.date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.date)
        self._restyle_chips()

    def _restyle_chips(self):
        # 札の色は QSS の objectName では色を分けにくい(難易度ごとに違う)
        # ので、部品ごとに当てる。テーマ替えで呼び直す。
        for chip in self.chips:
            col = COLORS.get(COURSE_COLOR_KEY.get(chip.text(), "fg_dim"), COLORS["fg_dim"])
            chip.setStyleSheet(
                "QLabel { color: %s; background-color: %s; border: 1px solid %s;"
                " border-radius: 3px; padding: 1px 6px; font-size: 11px;"
                " font-weight: bold; }" % (col, COLORS["bg3"], col))

    def set_revealed(self, on):
        self.title.set_revealed(on)
        self.path_label.set_revealed(on)

    def refresh_theme(self):
        self._restyle_chips()
        self.title.refresh()
        self.path_label.refresh()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.setFocus()
            self.activated.emit(self.path)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.path)
            ev.accept()
            return
        # Esc は何もしない(この画面に「閉じる」は無い)。残りは親へ。
        super().keyPressEvent(ev)


class StartScreen(QWidget):
    """起動画面。合図を出すだけで、開く/作るの実処理は MainWindow 側。"""

    #: 「YouTube / SoundCloud から」
    new_from_url = Signal()
    #: 「音源ファイルから」
    new_from_file = Signal()
    #: 「ファイルを選ぶ…」
    open_requested = Signal()
    #: 最近使った項目の行(パス)
    recent_activated = Signal(str)
    help_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("startScreen")
        self._revealed = False
        self._rows = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 低い窓(1280x820 の既定など)では縦に入りきらないので、切れる
        # 代わりにスクロールさせる。枠は見せない。
        self.scroll = QScrollArea()
        self.scroll.setObjectName("startScroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)

        page = QWidget()
        page.setObjectName("startPage")
        pl = QHBoxLayout(page)
        pl.setContentsMargins(24, 0, 24, 0)
        # 列が伸び率 1、両脇の余白は 0。余白にも伸び率を付けると列は
        # 推奨幅(600 ほど)で止まり、1180 まで広がらない。列が上限に
        # 当たった残りは両脇へ等分されるので中央に収まる。
        pl.addStretch(0)
        col = QWidget()
        col.setObjectName("startColumn")
        col.setMaximumWidth(COLUMN_WIDTH)
        col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        pl.addWidget(col, 1)
        pl.addStretch(0)
        self.scroll.setWidget(page)

        v = QVBoxLayout(col)
        v.setContentsMargins(0, 40, 0, 24)
        v.setSpacing(0)
        v.addStretch(2)

        head = QHBoxLayout()
        head.addWidget(self._label(APP_NAME, "appName"))
        head.addStretch(1)
        head.addWidget(self._label("v" + VERSION, "appVer"))
        v.addLayout(head)
        v.addSpacing(6)
        v.addWidget(self._label("はじめる", "h2"))
        v.addSpacing(14)

        cards = QHBoxLayout()
        cards.setSpacing(20)
        self.btn_new_url = QPushButton("YouTube / SoundCloud から")
        self.btn_new_url.setObjectName("primary")
        self.btn_new_url.clicked.connect(self.new_from_url)
        self.btn_new_file = QPushButton("音源ファイルから")
        self.btn_new_file.clicked.connect(self.new_from_file)
        cards.addWidget(self._card(
            "+", "新規作成",
            "YouTube / SoundCloud の URL か、手元の音源ファイルから譜面を作ります。",
            [self.btn_new_url, self.btn_new_file], accent=True), 1)
        self.btn_open = QPushButton("ファイルを選ぶ…")
        self.btn_open.clicked.connect(self.open_requested)
        cards.addWidget(self._card(
            "▤", "TJA を開く", ".tja ファイルを選んで編集します。",
            [self.btn_open]), 1)
        v.addLayout(cards)
        v.addSpacing(28)

        rh = QHBoxLayout()
        rh.setSpacing(8)
        rh.addWidget(self._label("最近使った項目", "h2"))
        rh.addStretch(1)
        self.lbl_hidden = self._label("曲名とパスを隠しています", "dim13")
        rh.addWidget(self.lbl_hidden)
        self.btn_reveal = QPushButton("表示する")
        self.btn_reveal.setObjectName("ghost")
        self.btn_reveal.setToolTip("曲名とパスのモザイクを外します(次に起動するまで)")
        self.btn_reveal.clicked.connect(self.reveal)
        rh.addWidget(self.btn_reveal)
        v.addLayout(rh)
        v.addSpacing(8)

        self.list_frame = QFrame()
        self.list_frame.setObjectName("card")
        self.list_layout = QVBoxLayout(self.list_frame)
        self.list_layout.setContentsMargins(0, 2, 0, 2)
        self.list_layout.setSpacing(0)
        v.addWidget(self.list_frame)

        v.addStretch(3)
        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.btn_help = QPushButton("ヘルプ")
        self.btn_help.setObjectName("ghost")
        self.btn_help.clicked.connect(self.help_requested)
        foot.addWidget(self.btn_help)
        self.btn_settings = QPushButton("環境設定")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.clicked.connect(self.settings_requested)
        foot.addWidget(self.btn_settings)
        foot.addStretch(1)
        foot.addWidget(self._label(
            "ファイルを開くか新規作成すると、この画面は閉じてエディタに切り替わります", "dim"))
        v.addLayout(foot)

        self.set_recent([])
        self.refresh_theme()

    # ------------------------------------------------------------------
    @staticmethod
    def _label(text, name):
        lbl = QLabel(text)
        lbl.setObjectName(name)
        return lbl

    def _card(self, icon, title, desc, buttons, accent=False):
        f = QFrame()
        f.setObjectName("cardAccent" if accent else "card")
        f.setMinimumHeight(220)
        cv = QVBoxLayout(f)
        cv.setContentsMargins(24, 22, 24, 20)
        cv.setSpacing(6)
        cv.addWidget(self._label(icon, "icon"))
        cv.addSpacing(4)
        cv.addWidget(self._label(title, "h1"))
        d = self._label(desc, "dim13")
        d.setWordWrap(True)
        cv.addWidget(d)
        cv.addStretch(1)
        h = QHBoxLayout()
        h.setSpacing(10)
        for b in buttons:
            h.addWidget(b)
        h.addStretch(1)
        cv.addLayout(h)
        return f

    # ------------------------------------------------------------------
    def set_recent(self, paths):
        """最近使った項目を並べ直す。設定に残っている順(新しい順)のまま、
        存在するものだけ、上限 RECENT_ROWS 件。"""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []
        paths = [p for p in paths if p and os.path.exists(p)][:RECENT_ROWS]
        if not paths:
            empty = self._label("まだありません。開いた譜面がここに並びます。", "dim13")
            empty.setContentsMargins(16, 14, 16, 14)
            self.list_layout.addWidget(empty)
            self.lbl_hidden.setVisible(False)
            self.btn_reveal.setVisible(False)
            return
        for i, p in enumerate(paths):
            title, courses = scan_tja_header(p)
            date = format_mtime(p)
            row = RecentRow(p, title, courses, ("更新 " + date) if date else "",
                            last=(i == len(paths) - 1))
            row.set_revealed(self._revealed)
            row.activated.connect(self.recent_activated)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.lbl_hidden.setVisible(not self._revealed)
        self.btn_reveal.setVisible(not self._revealed)

    def reveal(self):
        """曲名とパスのモザイクを外す。戻す道は無い(起動し直すまで)。"""
        self._revealed = True
        for row in self._rows:
            row.set_revealed(True)
        self.lbl_hidden.setVisible(False)
        self.btn_reveal.setVisible(False)

    def is_revealed(self):
        return self._revealed

    def rows(self):
        return list(self._rows)

    def refresh_theme(self):
        """テーマ替え。COLORS は中身が差し替わるので、読み直して当て直す。"""
        self.setStyleSheet(self._qss())
        for row in self._rows:
            row.refresh_theme()

    @staticmethod
    def _qss():
        p = COLORS
        # 見本(start_mock.py)の数字をそのまま。アプリ全体の QSS(theme.build_qss)
        # はボタンの余白などが違うので、この画面の中だけ上書きする。
        return f"""
        QWidget {{ background-color: {p['bg']}; color: {p['fg']}; font-size: 13px; }}
        QScrollArea {{ border: none; }}
        QLabel {{ background: transparent; }}
        QFrame#card {{ background-color: {p['bg2']}; border: 1px solid {p['border']}; border-radius: 8px; }}
        QFrame#cardAccent {{ background-color: {p['bg2']}; border: 1px solid {p['accent']}; border-radius: 8px; }}
        QFrame#recentRow, QFrame#recentRowLast {{ background-color: transparent; border: none; border-bottom: 1px solid {p['border']}; }}
        QFrame#recentRowLast {{ border-bottom: none; }}
        QFrame#recentRow:hover, QFrame#recentRowLast:hover {{ background-color: {p['bg3']}; }}
        QFrame#recentRow:focus, QFrame#recentRowLast:focus {{ background-color: {p['bg3']}; border-left: 3px solid {p['accent']}; }}
        QLabel#appName {{ color: {p['fg_dim']}; font-size: 14px; letter-spacing: 1px; }}
        QLabel#appVer {{ color: {p['fg_dim']}; font-size: 12px; }}
        QLabel#h1 {{ color: {p['fg_bright']}; font-size: 16px; font-weight: bold; }}
        QLabel#h2 {{ color: {p['fg']}; font-size: 14px; font-weight: bold; }}
        QLabel#dim {{ color: {p['fg_dim']}; font-size: 12px; }}
        QLabel#dim13 {{ color: {p['fg_dim']}; font-size: 13px; }}
        QLabel#icon {{ color: {p['accent']}; font-size: 28px; font-weight: bold; }}
        QPushButton {{ background-color: {p['bg3']}; color: {p['fg']}; border: 1px solid {p['border']}; border-radius: 5px; padding: 7px 14px; font-size: 13px; }}
        QPushButton:hover {{ background-color: {p['toolbar_hover']}; }}
        QPushButton:focus {{ border: 1px solid {p['accent']}; }}
        QPushButton#primary {{ background-color: {p['accent']}; color: {p['fg_bright']}; border: 1px solid {p['accent']}; font-weight: bold; }}
        QPushButton#primary:hover {{ background-color: {p['accent2']}; border-color: {p['accent2']}; }}
        QPushButton#ghost {{ background-color: transparent; border: none; color: {p['fg_dim']}; padding: 4px 8px; }}
        QPushButton#ghost:hover {{ color: {p['fg']}; background-color: {p['bg3']}; }}
        """
