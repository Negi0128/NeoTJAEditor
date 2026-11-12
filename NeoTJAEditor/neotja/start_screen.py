"""起動したときの最初の画面(利用者の指定 2026-11-12、見本は start_style_S2
「ガラスと光」。配線と中身は前の版 start_mock_A2 のまま)。

ファイルを何も開いていない起動直後だけ、中央(TJA と再生画面の分割)の
上に被せて出す。何か開くか新規作成が決まれば消え、以後は戻ってこない
(「閉じる」は無いので、戻す道も作っていない)。

中身は 4 つ。アプリ名と版、「新規作成」「TJA を開く」の2枚のカード、
最近使った項目、ヘルプ/環境設定。ここでは**並べて合図を出すだけ**で、
実際に開く・作る・設定を出すのは MainWindow の既存の口へ繋ぐ(配線を
二重に持たないため)。

見た目は「ガラスと光」。背景に大きくやわらかい光(アクセント色)を置き、
その上に半透明の白いカード(ガラス)を並べる。カードの塗りが半透明なので、
後ろの光がカードごとに少しずつ違う明るさで透け、平らな面が並ぶより奥行き
が出る。QSS は半透明の塗りまでは書けるが、上辺のハイライトや左の色帯の
ように「角丸に沿って一部だけ描く」ことができないので、カードは自前の
paintEvent で描く(GlassCard)。ボタンと札は QSS の rgba() で足りる。

最近使った項目は、曲名とパスを**モザイクにして**出す。配信や画面共有で
起動したとき、作りかけの曲名やフォルダ名が映ってしまうため。「表示する」
で全部見えるようにする。一方通行で、設定にも残さない — 起動し直せば
また隠れる。モザイクは文字をいったん絵に描いて 8 分の 1 に縮め、
最近傍で元の大きさへ戻す(見本と同じ作り)。ぼかしでなくモザイクなのは、
ぼかしだと短い曲名が読めてしまうから。
"""

import os
import datetime

from PySide6.QtCore import Qt, Signal, QSize, QRect, QRectF, QPointF
from PySide6.QtGui import (
    QFont, QFontMetrics, QFontDatabase, QPainter, QPixmap, QColor, QPen,
    QBrush, QLinearGradient, QRadialGradient, QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
    QPushButton, QScrollArea, QSizePolicy,
)

from neotja.constants import APP_NAME, VERSION
from neotja.theme import COLORS, glass_colors, is_dark_theme
from neotja.theme import with_alpha as _c, rgba as _rgba

#: 画面の中央の列の幅。見本(1180)に合わせる。窓が狭ければ縮む。
COLUMN_WIDTH = 1180
#: 最近使った項目に出す上限。設定には 10 件まで残るが、画面には 8 件。
RECENT_ROWS = 8
#: 最近使った項目の列数(見本は 2 列のコンパクトカード)。
RECENT_COLUMNS = 2
#: 最近使った項目のカードの高さ(見本 92)。
RECENT_CARD_HEIGHT = 92
#: 新規作成 / TJA を開く のカードの高さ(見本 196)。
ACTION_CARD_HEIGHT = 196
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

#: ワードマーク(アプリ名)の書体の候補。Office が無い Windows にも入っている
#: ものだけ。先に見つかったものを使う。Segoe UI は Light/Semilight の
#: ウェイトを持つので、見本どおりの細い字になる。
WORDMARK_FAMILIES = ("Segoe UI", "Yu Gothic UI", "Meiryo")


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


# ----------------------------------------------------------------------
# 色
# ----------------------------------------------------------------------
# ガラスの色一式(glass_colors)・不透明度付きの色(_c)・QSS の rgba 文字列
# (_rgba)・暗いテーマか(is_dark_theme)は neotja.theme に置いてある。
# 統合画面の右下の段も同じ見た目にする(main_window)ので、数字を 1 か所に
# 集めた。この画面の中の名前(_c など)はそのまま使えるよう import で引く。


def wordmark_family():
    """ワードマークに使う書体。候補のうち入っているものを返す。"""
    have = set(QFontDatabase.families())
    for fam in WORDMARK_FAMILIES:
        if fam in have:
            return fam
    return ""


# ----------------------------------------------------------------------
# 部品
# ----------------------------------------------------------------------
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
        # 省略できる相手は細くしてよい。曲名は長すぎればカード幅で切れる
        # (2 列のカードは 1 行の幅が半分しか無いので、曲名にも上限を置く)。
        w = 40 if self._elide else min(fm.horizontalAdvance(self._text) + 2, 160)
        return QSize(w, fm.height() + 2)

    def set_revealed(self, on):
        self._revealed = bool(on)
        self._cache = None
        self.update()

    def refresh(self):
        self._cache = None
        self.update()

    def _shown_text(self, width):
        fm = QFontMetrics(self._font())
        if not self._elide:
            # 曲名は末尾を省略(真ん中を省くと別の曲名に見えることがある)。
            return fm.elidedText(self._text, Qt.ElideRight, max(10, width))
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


class GlassCard(QFrame):
    """半透明のカード。塗り・縁・上辺のハイライト・(あれば)左の色帯を描く。

    QSS でも rgba の塗りと縁は書けるが、上辺だけのハイライトと角丸に沿った
    色帯は書けないので自前で描く。QSS はこのクラスに背景を塗らせない
    (background: transparent)。hover は enterEvent/leaveEvent で持ち、
    focus はフォーカス枠(アクセント色の縁)として描く。"""

    def __init__(self, radius=14, accent=False, hoverable=False, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._accent = accent
        self._hoverable = hoverable
        self._hover = False
        self._bar_color = None
        self.setAttribute(Qt.WA_Hover, hoverable)

    def set_bar_color(self, color):
        """左の色帯。None なら描かない。"""
        self._bar_color = QColor(color) if color is not None else None
        self.update()

    def enterEvent(self, ev):
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(ev)

    def focusInEvent(self, ev):
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev):
        self.update()
        super().focusOutEvent(ev)

    def paintEvent(self, ev):
        g = glass_colors()
        r = self._radius
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 縁の 1px が外にはみ出さないよう半ピクセル内へ。
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = g["fill_hover"] if (self._hover or self.hasFocus()) else g["fill"]
        if self.hasFocus():
            border = QColor(COLORS["accent"])
        elif self._accent:
            border = g["accent_border"]
        else:
            border = g["border"]
        p.setPen(QPen(border, 1))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(rect, r, r)
        # 上辺のハイライト: 真ん中が明るく両端へ消える線。ガラスの縁に
        # 光が当たっているように見せる要。
        hl = QLinearGradient(rect.left(), 0, rect.right(), 0)
        hl.setColorAt(0, _c(g["highlight"], 0))
        hl.setColorAt(0.5, g["highlight"])
        hl.setColorAt(1, _c(g["highlight"], 0))
        p.setPen(QPen(QBrush(hl), 1))
        p.drawLine(QPointF(rect.left() + r, rect.top() + 1),
                   QPointF(rect.right() - r, rect.top() + 1))
        if self._bar_color is not None:
            # 色帯は角丸に沿わせる(切り抜いてから長方形を塗る)。
            clip = QPainterPath()
            clip.addRoundedRect(rect, r, r)
            p.setClipPath(clip)
            p.setPen(Qt.NoPen)
            p.setBrush(self._bar_color)
            p.drawRect(QRectF(rect.left(), rect.top(), 4, rect.height()))
        p.end()


class RecentRow(GlassCard):
    """最近使った項目の1枚(2 列に並ぶコンパクトカード)。クリックか Enter で開く。

    左の色帯はいちばん難しい難易度の色。上段に曲名と更新日時、下段に
    難易度の札とパス。名前が RecentRow のままなのは、MainWindow や試験が
    「行」として扱う口(path / title / chips / date / activated)を変えない
    ため。"""

    activated = Signal(str)

    def __init__(self, path, title, courses, date_text, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.path = path
        self.courses = list(courses)
        self.setObjectName("recentRow")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        self.setFixedHeight(RECENT_CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        # 左 22 は色帯(4)ぶん空けた位置(見本)。
        v.setContentsMargins(22, 14, 18, 12)
        v.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = MosaicText(title, "fg_bright", 16, bold=True)
        top.addWidget(self.title, 1)
        self.date = QLabel(date_text)
        self.date.setObjectName("dim12")
        self.date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.date, 0)
        v.addLayout(top)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.chips = []
        for c in courses:
            chip = QLabel(c)
            chip.setObjectName("chip")
            chip.setProperty("course", c)
            chip.setFixedHeight(20)
            bottom.addWidget(chip)
            self.chips.append(chip)
        if self.chips:
            bottom.addSpacing(6)
        self.path_label = MosaicText(path, "fg_dim", 12, elide=True)
        bottom.addWidget(self.path_label, 1)
        v.addLayout(bottom)
        v.addStretch(1)
        self._restyle()

    def _restyle(self):
        # 札の色は難易度ごとに違うので QSS の objectName では分けにくい。
        # 部品ごとに当てる。テーマ替えで呼び直す。
        g = glass_colors()
        for chip in self.chips:
            col = QColor(COLORS.get(COURSE_COLOR_KEY.get(chip.text(), "fg_dim"),
                                    COLORS["fg_dim"]))
            chip.setStyleSheet(
                "QLabel { color: %s; background-color: %s; border: 1px solid %s;"
                " border-radius: 5px; padding: 0px 7px; font-size: 12px;"
                " font-weight: bold; }"
                % (col.name(), _rgba(_c(col, g["chip_fill_alpha"])),
                   _rgba(_c(col, g["chip_border_alpha"]))))
        # 色帯は上から並べた先頭(いちばん難しい)の色。札が無ければ薄い字の色。
        top = self.courses[0] if self.courses else None
        self.set_bar_color(COLORS.get(COURSE_COLOR_KEY.get(top, "fg_dim"), COLORS["fg_dim"]))

    def bar_color(self):
        return QColor(self._bar_color) if self._bar_color is not None else None

    def set_revealed(self, on):
        self.title.set_revealed(on)
        self.path_label.set_revealed(on)

    def refresh_theme(self):
        self._restyle()
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
        # 代わりにスクロールさせる。枠は見せない。背景は StartScreen の
        # paintEvent が光ごと描くので、スクロール領域とその中身は透明に
        # して邪魔をさせない(viewport は既定で塗りつぶす)。
        self.scroll = QScrollArea()
        self.scroll.setObjectName("startScroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
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

        # ワードマーク + 版のピル。見本では版はワードマークの右、上から 14。
        head = QHBoxLayout()
        head.setSpacing(16)
        self.lbl_name = self._label(APP_NAME, "appName")
        head.addWidget(self.lbl_name, 0, Qt.AlignBottom)
        self.lbl_ver = self._label("v" + VERSION, "appVer")
        self.lbl_ver.setFixedHeight(22)
        head.addWidget(self.lbl_ver, 0, Qt.AlignVCenter)
        head.addStretch(1)
        v.addLayout(head)
        v.addSpacing(4)
        v.addWidget(self._label("はじめる", "tagline"))
        v.addSpacing(26)

        cards = QHBoxLayout()
        cards.setSpacing(24)
        self.btn_new_url = QPushButton("YouTube / SoundCloud から")
        self.btn_new_url.setObjectName("primary")
        self.btn_new_url.clicked.connect(self.new_from_url)
        self.btn_new_file = QPushButton("音源ファイルから")
        self.btn_new_file.clicked.connect(self.new_from_file)
        cards.addWidget(self._card(
            "新規作成",
            "YouTube / SoundCloud の URL か、手元の音源ファイルから譜面を作ります。",
            [self.btn_new_url, self.btn_new_file], accent=True), 1)
        self.btn_open = QPushButton("ファイルを選ぶ…")
        self.btn_open.clicked.connect(self.open_requested)
        cards.addWidget(self._card(
            "TJA を開く", ".tja ファイルを選んで編集します。",
            [self.btn_open]), 1)
        v.addLayout(cards)
        v.addSpacing(40)

        rh = QHBoxLayout()
        rh.setSpacing(8)
        rh.addWidget(self._label("最近使った項目", "h2"))
        rh.addStretch(1)
        self.lbl_hidden = self._label("曲名とパスを隠しています", "dim13")
        rh.addWidget(self.lbl_hidden)
        self.btn_reveal = QPushButton("表示する")
        self.btn_reveal.setObjectName("link")
        self.btn_reveal.setToolTip("曲名とパスのモザイクを外します(次に起動するまで)")
        self.btn_reveal.clicked.connect(self.reveal)
        rh.addWidget(self.btn_reveal)
        v.addLayout(rh)
        v.addSpacing(12)

        # 2 列のカードを並べる格子。枠は持たない(カードが各自ガラスを描く)。
        # 件数が奇数でも最後のカードが半分の幅のままになるよう、両列の
        # 伸び率をそろえる。
        self.list_frame = QWidget()
        self.list_frame.setObjectName("recentGrid")
        self.list_layout = QGridLayout(self.list_frame)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setHorizontalSpacing(16)
        self.list_layout.setVerticalSpacing(12)
        for c in range(RECENT_COLUMNS):
            self.list_layout.setColumnStretch(c, 1)
        v.addWidget(self.list_frame)

        # 低い窓で伸び代が 0 になっても、最後のカードと脚注がくっつかない
        # よう最低 24 は空ける。
        v.addSpacing(24)
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

    def _card(self, title, desc, buttons, accent=False):
        f = GlassCard(radius=14, accent=accent)
        f.setObjectName("actionCard")
        f.setMinimumHeight(ACTION_CARD_HEIGHT)
        cv = QVBoxLayout(f)
        cv.setContentsMargins(28, 26, 28, 28)
        cv.setSpacing(6)
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
    def paintEvent(self, ev):
        """背景。アプリの地色の上に、大きくやわらかい光を 2 つ。

        1 つ目はカードの後ろ(横の真ん中、上から 4 割)にアクセント色、
        2 つ目は右下に空色をもっと薄く。半径は窓の幅に比例させるので、
        窓を広げても光が小さな点に見えない。QWidget は大きさが変わると
        全体を描き直すので、resize の面倒は見なくてよい。"""
        g = glass_colors()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["bg"]))
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for (cx, cy, rad, col, a) in (
                (w * 0.5, h * 0.41, max(300.0, w * 0.43), COLORS["accent"], g["glow1_alpha"]),
                (w * 0.78, h * 0.79, max(200.0, w * 0.27), COLORS["ka"], g["glow2_alpha"])):
            grad = QRadialGradient(cx, cy, rad)
            grad.setColorAt(0, _c(col, a))
            grad.setColorAt(0.55, _c(col, a * 0.35))
            grad.setColorAt(1, _c(col, 0))
            p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.end()

    # ------------------------------------------------------------------
    def set_recent(self, paths):
        """最近使った項目を並べ直す。設定に残っている順(新しい順)のまま、
        存在するものだけ、上限 RECENT_ROWS 件。左→右、上→下の順に置く。"""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []
        paths = [p for p in paths if p and os.path.exists(p)][:RECENT_ROWS]
        if not paths:
            empty = self._label("まだありません。開いた譜面がここに並びます。", "dim13")
            empty.setContentsMargins(4, 10, 4, 10)
            self.list_layout.addWidget(empty, 0, 0, 1, RECENT_COLUMNS)
            self.lbl_hidden.setVisible(False)
            self.btn_reveal.setVisible(False)
            return
        for i, p in enumerate(paths):
            title, courses = scan_tja_header(p)
            date = format_mtime(p)
            row = RecentRow(p, title, courses, ("更新 " + date) if date else "")
            row.set_revealed(self._revealed)
            row.activated.connect(self.recent_activated)
            self.list_layout.addWidget(row, i // RECENT_COLUMNS, i % RECENT_COLUMNS)
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
        self.update()

    @staticmethod
    def _qss():
        p = COLORS
        g = glass_colors()
        grad = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
                % (g["primary_top"].name(), g["primary_bottom"].name()))
        grad_hover = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
                      % (g["primary_top"].lighter(108).name(),
                         g["primary_bottom"].lighter(108).name()))
        fam = wordmark_family()
        fam_css = ('font-family: "%s";' % fam) if fam else ""
        # 見本(start_style.py の S2)の数字をそのまま。アプリ全体の QSS
        # (theme.build_qss)はボタンの余白などが違うので、この画面の中だけ
        # 上書きする。背景は paintEvent が描くので、この中の部品はすべて
        # 透明(ガラスのカードは GlassCard が自前で塗る)。
        return f"""
        QWidget {{ background: transparent; color: {p['fg']}; font-size: 13px; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {_rgba(g['btn_border'])}; border-radius: 5px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {_rgba(g['btn_fill_hover'])}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QLabel {{ background: transparent; }}
        QLabel#appName {{ color: {p['fg_bright']}; {fam_css} font-size: 40px; font-weight: 300; letter-spacing: 2px; }}
        QLabel#appVer {{ color: {p['fg']}; font-size: 12px; letter-spacing: 0.5px; padding: 0 10px;
                        background-color: {_rgba(g['btn_fill'])}; border: 1px solid {_rgba(g['btn_border'])}; border-radius: 11px; }}
        QLabel#tagline {{ color: {p['fg_dim']}; font-size: 13px; letter-spacing: 1px; }}
        QLabel#h1 {{ color: {p['fg_bright']}; font-size: 19px; font-weight: bold; }}
        QLabel#h2 {{ color: {p['fg']}; font-size: 15px; font-weight: bold; }}
        QLabel#dim {{ color: {p['fg_dim']}; font-size: 12px; }}
        QLabel#dim12 {{ color: {p['fg_dim']}; font-size: 12px; }}
        QLabel#dim13 {{ color: {p['fg_dim']}; font-size: 13px; }}
        QPushButton {{ background-color: {_rgba(g['btn_fill'])}; color: {p['fg']}; border: 1px solid {_rgba(g['btn_border'])};
                      border-radius: 9px; padding: 9px 22px; font-size: 13px; }}
        QPushButton:hover {{ background-color: {_rgba(g['btn_fill_hover'])}; }}
        QPushButton:focus {{ border: 1px solid {p['accent']}; }}
        QPushButton#primary {{ background-color: {grad}; color: {g['primary_fg'].name()};
                              border: 1px solid {_rgba(g['primary_border'])}; font-weight: bold; }}
        QPushButton#primary:hover {{ background-color: {grad_hover}; }}
        QPushButton#primary:focus {{ border: 1px solid {p['fg_bright']}; }}
        QPushButton#ghost {{ background-color: transparent; border: 1px solid transparent; color: {p['fg_dim']}; padding: 4px 8px; border-radius: 6px; }}
        QPushButton#ghost:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
        QPushButton#ghost:focus {{ border: 1px solid {p['accent']}; }}
        QPushButton#link {{ background-color: transparent; border: 1px solid transparent; color: {p['accent']}; padding: 4px 10px; border-radius: 6px; }}
        QPushButton#link:hover {{ background-color: {_rgba(g['btn_fill'])}; }}
        QPushButton#link:focus {{ border: 1px solid {p['accent']}; }}
        """
