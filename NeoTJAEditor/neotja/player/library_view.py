"""Player の選曲(v15-design/PLAYER_IMPLEMENTATION_PLAN.md「選曲」)。

左に NeoTJAPlayer(TJAを開く・最近開いた楽曲・保存済み楽曲)と登録フォルダ、
右に選んだものの中身。曲を選んだら song_chosen を出すだけで、その先
(難易度選択)はここでは扱わない。

見た目はランチャー(start_screen)と同じガラスの部品(GlassCard・QSS)を使う。
譜面の見出しを読むのとフォルダを歩くのはワーカースレッドで行い、画面は
止めない(player/library.py と同じ境界: ウィジェットに触るのは GUI だけ)。
"""

import datetime
import os
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from neotja.player import song_lists
from neotja.start_screen import (
    COURSE_COLOR_KEY, ElidedText, GlassCard, wordmark_family,
)
from neotja.theme import COLORS, glass_colors
from neotja.theme import with_alpha as _c, rgba as _rgba
from neotja.worker_util import detach_worker

#: 1行の高さ。
ROW_HEIGHT = 64
#: 左の列の幅。
SIDEBAR_WIDTH = 264
#: ワーカーがまとめて送る件数・間隔(library.py と同じ考え方)。
_BATCH = 24
_BATCH_SEC = 0.15
#: 検索欄の打鍵がこれだけ止まったらフォルダの下を探し始める(ms)。
SEARCH_DEBOUNCE_MS = 200


# ----------------------------------------------------------------------
# ワーカー
# ----------------------------------------------------------------------
class InfoWorker(QThread):
    """パスの一覧、またはフォルダ一段ぶんの見出しを読む。"""

    # 先頭はワーカー自身(古い走査の結果を受け手が捨てられるように)。
    listed = Signal(object, object, object)   # (worker, ジャンル, サブフォルダ) — フォルダのときだけ
    infos = Signal(object, object)            # (worker, [(path, info)])

    def __init__(self, paths=None, directory=None, parent=None):
        super().__init__(parent)
        self._paths = list(paths or [])
        self._dir = directory
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        paths = self._paths
        if self._dir is not None:
            genres, dirs, paths = song_lists.list_dir(self._dir)
            if self._cancelled:
                return
            self.listed.emit(self, genres, dirs)
        batch, last = [], time.monotonic()
        for p in paths:
            if self._cancelled:
                return
            batch.append((p, song_lists.song_info(p)))
            now = time.monotonic()
            if len(batch) >= _BATCH or now - last >= _BATCH_SEC:
                self.infos.emit(self, batch)
                batch, last = [], now
        if batch and not self._cancelled:
            self.infos.emit(self, batch)


class SearchWorker(QThread):
    """フォルダの下(サブフォルダ・ジャンルの中も全部)から、題名・副題・
    ファイル名に query を含む曲を探す。"""

    found = Signal(object, object)   # (worker, [(path, info)])
    done = Signal(object)            # worker

    def __init__(self, root, query, parent=None):
        super().__init__(parent)
        self.root = root
        self.query = query
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        batch, last = [], time.monotonic()
        for p in song_lists.walk_tjas(self.root, lambda: self._cancelled):
            if self._cancelled:
                return
            info = song_lists.song_info(p)
            if song_lists.matches(self.query, info, p):
                batch.append((p, info))
            now = time.monotonic()
            if batch and (len(batch) >= _BATCH or now - last >= _BATCH_SEC):
                self.found.emit(self, batch)
                batch, last = [], now
        if self._cancelled:
            return
        if batch:
            self.found.emit(self, batch)
        self.done.emit(self)


# ----------------------------------------------------------------------
# 部品
# ----------------------------------------------------------------------
def _when(epoch):
    """最終に開いた日を短く。"""
    try:
        d = datetime.datetime.fromtimestamp(float(epoch))
    except (TypeError, ValueError, OSError):
        return ""
    days = (datetime.date.today() - d.date()).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    if days < 7:
        return "%d日前" % days
    return d.strftime("%Y/%m/%d")


def _short_dir(path):
    """置き場所を短く(親フォルダ2段)。全体はツールチップで見せる。"""
    parts = [s for s in os.path.normpath(os.path.dirname(path)).split(os.sep) if s]
    return "  ›  ".join(parts[-2:])


def _chip_style(color_key):
    g = glass_colors()
    col = QColor(COLORS.get(color_key, COLORS["fg_dim"]))
    return ("QLabel { color: %s; background-color: %s; border: 1px solid %s;"
            " border-radius: 5px; padding: 0px 8px; font-size: 12px;"
            " font-weight: bold; }"
            % (col.name(), _rgba(_c(col, g["chip_fill_alpha"])),
               _rgba(_c(col, g["chip_border_alpha"]))))


class SongRow(GlassCard):
    """曲の1行。左の色帯はいちばん難しい難易度の色。"""

    chosen = Signal(str)
    save_toggled = Signal(str, bool)
    remove_requested = Signal(str)
    relink_requested = Signal(str)

    def __init__(self, path, title, *, saved=None, removable=False,
                 date_text="", show_path=False, path_text=None, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.path = path
        self.missing = not os.path.isfile(path)
        self.info = {"title": title, "subtitle": "", "courses": [], "bpm": None}
        self.order = 0
        self._saved = saved
        self.setObjectName("songRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(path)
        if not self.missing:
            self.setFocusPolicy(Qt.StrongFocus)
            self.setCursor(Qt.PointingHandCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(22, 8, 12, 8)
        h.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        key = "fg_dim" if self.missing else "fg_bright"
        self.title = ElidedText(title, key, 16, bold=True)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        texts.addWidget(self.title)
        if path_text is not None:
            show_path = True
        else:
            path_text = _short_dir(path) if show_path else ""
        self.sub = ElidedText(path_text, "fg_dim", 12, elide=show_path)
        self.sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._show_path = show_path
        texts.addWidget(self.sub)
        # 2段目が空なら隠して、曲名を縦の真ん中に置く。
        self.sub.setVisible(bool(self.sub.text()))
        h.addLayout(texts, 1)

        self.chip_box = QHBoxLayout()
        self.chip_box.setSpacing(6)
        h.addLayout(self.chip_box)
        self.chips = []
        self.bpm = QLabel("")
        self.bpm.setObjectName("bpm")
        self.bpm.setMinimumWidth(70)
        self.bpm.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bpm.setVisible(False)
        h.addWidget(self.bpm)

        if date_text:
            self.date = QLabel(date_text)
            self.date.setObjectName("dim12")
            self.date.setMinimumWidth(64)
            self.date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(self.date)

        if self.missing:
            self.btn_relink = QPushButton("再指定")
            self.btn_relink.setObjectName("small")
            self.btn_relink.clicked.connect(lambda: self.relink_requested.emit(self.path))
            h.addWidget(self.btn_relink)
        if saved is not None and not self.missing:
            self.btn_star = QPushButton()
            self.btn_star.setObjectName("star")
            self.btn_star.setFixedSize(36, 32)
            self.btn_star.setCursor(Qt.PointingHandCursor)
            self.btn_star.clicked.connect(self._toggle_saved)
            h.addWidget(self.btn_star)
            self._sync_star()
        if removable:
            self.btn_remove = QPushButton("外す")
            self.btn_remove.setObjectName("small")
            self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self.path))
            h.addWidget(self.btn_remove)
        self._restyle()

    # ------------------------------------------------------------------
    def set_info(self, info):
        self.info = info
        self.title._text = info["title"]
        if not self._show_path:
            self.sub._text = info.get("subtitle", "")
            self.sub.setVisible(bool(self.sub._text))
        for chip in self.chips:
            chip.deleteLater()
        self.chips = []
        for name, level in info.get("courses", []):
            label = song_lists.COURSE_LABELS.get(name, name)
            chip = QLabel(label if level is None else "%s %d" % (label, level))
            chip.setProperty("course", name)
            chip.setFixedHeight(22)
            self.chip_box.addWidget(chip)
            self.chips.append(chip)
        bpm = info.get("bpm")
        self.bpm.setText("BPM %s" % ("%g" % bpm) if bpm else "")
        # 並び替えには使うが表示はしない(利用者の指定 2026-11-16)。
        self.bpm.setVisible(False)
        self._restyle()
        self.title.update()
        self.sub.update()

    def set_missing_chip(self):
        chip = QLabel("ファイルなし")
        chip.setProperty("course", "")
        chip.setFixedHeight(22)
        self.chip_box.addWidget(chip)
        self.chips.append(chip)
        self._restyle()

    def matches(self, q):
        return song_lists.matches(q, self.info, self.path)

    def set_chosen(self, on):
        self._accent = bool(on)
        self.update()

    def set_saved(self, on):
        self._saved = bool(on)
        self._sync_star()

    def _sync_star(self):
        b = getattr(self, "btn_star", None)
        if b is None:
            return
        b.setText("★" if self._saved else "☆")
        b.setProperty("on", bool(self._saved))
        b.setToolTip("保存済み楽曲から外す" if self._saved else "保存済み楽曲に追加")
        b.style().unpolish(b)
        b.style().polish(b)

    def _toggle_saved(self):
        self._saved = not self._saved
        self._sync_star()
        self.save_toggled.emit(self.path, self._saved)

    def _restyle(self):
        for chip in self.chips:
            chip.setStyleSheet(_chip_style(
                COURSE_COLOR_KEY.get(chip.property("course"), "fg_dim")))
        top = self.info["courses"][0][0] if self.info.get("courses") else None
        if self.missing or top is None:
            self.set_bar_color(_c(COLORS["fg_dim"], 0.5))
        else:
            self.set_bar_color(COLORS.get(COURSE_COLOR_KEY.get(top, "fg_dim"),
                                          COLORS["fg_dim"]))

    def refresh_theme(self):
        self._restyle()
        self.title.refresh()
        self.sub.refresh()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self.missing:
            self.setFocus()
            self.chosen.emit(self.path)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not self.missing:
            self.chosen.emit(self.path)
            ev.accept()
            return
        super().keyPressEvent(ev)


class FolderRow(GlassCard):
    """サブフォルダの1行。"""

    opened = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.path = path
        self.name = os.path.basename(os.path.normpath(path)) or path
        self.setObjectName("folderRow")
        self.setFixedHeight(54)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 6, 18, 6)
        h.setSpacing(14)
        h.addWidget(_FolderIcon())
        self.title = ElidedText(self.name, "fg_bright", 15)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h.addWidget(self.title, 1)
        arrow = QLabel("›")
        arrow.setObjectName("chevron")
        h.addWidget(arrow)

    def matches(self, q):
        q = song_lists.normalize(q).strip()
        return not q or q in song_lists.normalize(self.name)

    def refresh_theme(self):
        self.title.refresh()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.opened.emit(self.path)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.opened.emit(self.path)
            ev.accept()
            return
        super().keyPressEvent(ev)


class GenreRow(GlassCard):
    """ジャンル(box.def のあるフォルダ)の1行。box.def の色を左の帯と
    うっすらした地の色に使う。"""

    opened = Signal(str)

    def __init__(self, path, box, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.path = path
        self.box = box
        self.name = box["title"]
        self.setObjectName("genreRow")
        self.setFixedHeight(62)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        h = QHBoxLayout(self)
        h.setContentsMargins(24, 6, 18, 6)
        h.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = ElidedText(self.name, "fg_bright", 17, bold=True)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        texts.addWidget(self.title)
        genre = box.get("genre", "")
        if genre and genre != self.name:
            self.sub = ElidedText(genre, "fg_dim", 12)
            texts.addWidget(self.sub)
        h.addLayout(texts, 1)
        arrow = QLabel("›")
        arrow.setObjectName("chevron")
        h.addWidget(arrow)
        self._restyle()

    def color(self):
        return QColor(self.box.get("color") or COLORS["accent"])

    def _restyle(self):
        self.set_bar_color(self.color())

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 10, 10)
        p.setClipPath(clip)
        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        col = self.color()
        grad.setColorAt(0, _c(col, 0.22))
        grad.setColorAt(0.55, _c(col, 0.04))
        grad.setColorAt(1, _c(col, 0))
        p.fillRect(rect, QBrush(grad))
        p.end()

    def matches(self, q):
        q = song_lists.normalize(q).strip()
        return (not q or q in song_lists.normalize(self.name)
                or q in song_lists.normalize(self.box.get("genre", "")))

    def refresh_theme(self):
        self._restyle()
        self.title.refresh()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.opened.emit(self.path)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.opened.emit(self.path)
            ev.accept()
            return
        super().keyPressEvent(ev)


class CategoryDialog(QDialog):
    """登録フォルダのカテゴリ(シミュレーター名)を打つ小さな窓。

    候補は利用者がこれまでに付けた名前だけ。決まった名前の一覧は持たない
    (利用者の指定)。"""

    def __init__(self, names, current="", path="", heading="カテゴリ",
                 ok_text="追加", parent=None):
        super().__init__(parent)
        self.setObjectName("categoryDialog")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 26, 30, 24)
        v.setSpacing(0)
        head = QLabel(heading)
        head.setObjectName("dlgHead")
        v.addWidget(head)
        if path:
            v.addSpacing(6)
            self.lbl_path = ElidedText(path, "fg_dim", 12, elide=True)
            self.lbl_path.setToolTip(path)
            v.addWidget(self.lbl_path)
        v.addSpacing(18)
        self.combo = QComboBox()
        self.combo.setObjectName("category")
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.addItems([n for n in names if n])
        self.combo.setEditText(current)
        self.combo.lineEdit().setPlaceholderText("カテゴリ名")
        self.combo.setMinimumHeight(38)
        v.addWidget(self.combo)
        v.addSpacing(22)
        bh = QHBoxLayout()
        bh.setSpacing(10)
        bh.addStretch(1)
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self.reject)
        bh.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        bh.addWidget(self.btn_ok)
        v.addLayout(bh)
        self.combo.editTextChanged.connect(self._sync)
        self._sync()
        from neotja.start_screen import StartScreen
        self.setStyleSheet(StartScreen._qss() + library_qss())
        self.combo.lineEdit().setFocus()
        self.combo.lineEdit().selectAll()

    def value(self):
        return self.combo.currentText().strip()

    def _sync(self):
        self.btn_ok.setEnabled(bool(self.value()))

    def paintEvent(self, ev):
        g = glass_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(g["accent_border"], 1))
        p.setBrush(QColor(COLORS["bg"]))
        p.drawRoundedRect(rect, 14, 14)
        p.setBrush(g["fill"])
        p.drawRoundedRect(rect, 14, 14)
        hl = QLinearGradient(rect.left(), 0, rect.right(), 0)
        hl.setColorAt(0, _c(g["highlight"], 0))
        hl.setColorAt(0.5, g["highlight"])
        hl.setColorAt(1, _c(g["highlight"], 0))
        p.setPen(QPen(QBrush(hl), 1))
        p.drawLine(QPointF(rect.left() + 14, rect.top() + 1),
                   QPointF(rect.right() - 14, rect.top() + 1))
        p.end()


class _FolderIcon(QWidget):
    """フォルダの形(線だけ)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 22)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, ev):
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(COLORS["fg_dim"]), 1.6))
        path = QPainterPath()
        path.moveTo(2, 5)
        path.lineTo(10, 5)
        path.lineTo(12.5, 7.5)
        path.lineTo(24, 7.5)
        path.lineTo(24, 19)
        path.lineTo(2, 19)
        path.closeSubpath()
        p.drawPath(path)
        p.end()


class RowList(QScrollArea):
    """行を縦に並べるスクロール領域。空なら「なし」だけ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("rowListInner")
        self._v = QVBoxLayout(inner)
        self._v.setContentsMargins(0, 0, 8, 0)
        self._v.setSpacing(8)
        self.empty = QLabel("なし")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._v.addWidget(self.empty)
        self._v.addStretch(1)
        self.setWidget(inner)
        self._rows = []

    def rows(self):
        return list(self._rows)

    def clear(self):
        for r in self._rows:
            r.setParent(None)
            r.deleteLater()
        self._rows = []
        self.empty.setVisible(True)

    def add(self, row):
        if isinstance(row, SongRow):
            row.order = len(self._rows)
        self._v.insertWidget(self._v.count() - 1, row)
        self._rows.append(row)
        self.empty.setVisible(False)

    def sort_songs(self, mode, reverse):
        """曲の行だけ並べ替える。ジャンルとフォルダは上に、名前順のまま。"""
        songs = [r for r in self._rows if isinstance(r, SongRow)]
        if len(songs) < 2:
            return
        others = [r for r in self._rows if not isinstance(r, SongRow)]
        ordered = song_lists.sort_songs(
            [(r.path, r.info, r.order, r) for r in songs], mode, reverse)
        new = others + [t[3] for t in ordered]
        if new == self._rows:
            return
        for r in new:
            self._v.removeWidget(r)
        for r in new:
            self._v.insertWidget(self._v.count() - 1, r)
        self._rows = new

    def apply_filter(self, q):
        shown = 0
        for r in self._rows:
            ok = r.matches(q)
            r.setVisible(ok)
            shown += ok
        self.empty.setVisible(shown == 0)


class NavButton(QPushButton):
    """左の列の項目。選んでいるものは塗りと左の線で示す。"""

    def __init__(self, text, kind, parent=None):
        super().__init__(text, parent)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setCursor(Qt.PointingHandCursor)
        self.kind = kind
        self.setProperty("kind", kind)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self.isChecked():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(COLORS["accent"]), 3, Qt.SolidLine, Qt.RoundCap))
            x = 18 if self.property("sub") else 4
            p.drawLine(x, 12, x, self.height() - 12)
            p.end()


class SortBar(QWidget):
    """並べ方の切り替え(つながったボタン)と、向きの ↑↓。"""

    changed = Signal(str, bool)   # (mode, reverse)

    def __init__(self, modes, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.mode = modes[0]
        self.reverse = False
        self.buttons = {}
        for i, m in enumerate(modes):
            b = QPushButton(song_lists.SORT_LABELS[m])
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("pos", "first" if i == 0 else ("last" if i == len(modes) - 1 else "mid"))
            b.clicked.connect(lambda _=False, mode=m: self._pick(mode))
            h.addWidget(b)
            self.buttons[m] = b
        h.addSpacing(6)
        self.btn_dir = QPushButton()
        self.btn_dir.setObjectName("sortDir")
        self.btn_dir.setFixedSize(34, 32)
        self.btn_dir.setCursor(Qt.PointingHandCursor)
        self.btn_dir.clicked.connect(self._flip)
        h.addWidget(self.btn_dir)
        self.set_state(self.mode, False)

    def set_state(self, mode, reverse):
        if mode not in self.buttons:
            mode = next(iter(self.buttons))
        self.mode, self.reverse = mode, bool(reverse)
        for m, b in self.buttons.items():
            b.setChecked(m == mode)
        desc = song_lists.is_descending(mode, reverse)
        self.btn_dir.setText("↓" if desc else "↑")
        self.btn_dir.setToolTip("降順" if desc else "昇順")

    def _pick(self, mode):
        self.set_state(mode, self.reverse)
        self.changed.emit(self.mode, self.reverse)

    def _flip(self):
        self.set_state(self.mode, not self.reverse)
        self.changed.emit(self.mode, self.reverse)


class _Sidebar(QWidget):
    """左の列。少し暗くして、右端に細い線。"""

    def paintEvent(self, ev):
        p = QPainter(self)
        dark = glass_colors()["field_fill"]
        p.fillRect(self.rect(), _c(dark, dark.alphaF() * 0.6))
        p.setPen(QPen(glass_colors()["border"], 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.end()


# ----------------------------------------------------------------------
# 右の各画面
# ----------------------------------------------------------------------
class _Page(QWidget):
    """見出し(パンくず・大きな題)+ 道具の段 + 一覧。"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(40, 26, 40, 28)
        self.v.setSpacing(0)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        self.crumbs.addStretch(1)
        self.v.addLayout(self.crumbs)
        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("pageTitle")
        self.v.addWidget(self.lbl_title)
        self.v.addSpacing(18)
        self.tools = QHBoxLayout()
        self.tools.setContentsMargins(0, 0, 8, 0)
        self.tools.setSpacing(10)
        self.v.addLayout(self.tools)

    def add_search(self):
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("曲名で検索")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(420)
        self.tools.addWidget(self.search)
        self.tools.addStretch(1)
        return self.search

    def add_sort(self, modes):
        self.sort = SortBar(modes)
        self.tools.addWidget(self.sort)
        return self.sort

    def add_list(self):
        self.v.addSpacing(16)
        self.list = RowList()
        self.v.addWidget(self.list, 1)
        if hasattr(self, "search"):
            self.search.textChanged.connect(self.list.apply_filter)
        return self.list


# ----------------------------------------------------------------------
class LibraryView(QWidget):
    """選曲。左の列と右の中身。"""

    song_chosen = Signal(str)
    #: 操作音の合図("pi")。鳴らすのは PlayerHost(ui_sound)。
    sound_requested = Signal(str)

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self._save_cb = save_cb
        self._worker = None
        self._chosen = ""
        self._folder_root = ""
        self._folder_dir = ""
        self._pending_rows = {}
        self._search_worker = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._run_folder_search)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # --- 左 ----------------------------------------------------------
        side = _Sidebar()
        side.setFixedWidth(SIDEBAR_WIDTH)
        side_v = QVBoxLayout(side)
        side_v.setContentsMargins(0, 0, 0, 0)
        # 登録フォルダが増えても切れないように、列ごとスクロールさせる。
        side_scroll = QScrollArea()
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.viewport().setAutoFillBackground(False)
        side_v.addWidget(side_scroll)
        side_inner = QWidget()
        side_inner.setObjectName("sideInner")
        side_scroll.setWidget(side_inner)
        sv = QVBoxLayout(side_inner)
        sv.setContentsMargins(16, 22, 16, 22)
        sv.setSpacing(4)
        sv.addWidget(self._category_head("NeoTJAPlayer"))
        sv.addSpacing(6)
        self.nav_open = NavButton("TJAを開く", "open")
        self.nav_recent = NavButton("最近開いた楽曲", "recent")
        self.nav_saved = NavButton("保存済み楽曲", "saved")
        for b in (self.nav_open, self.nav_recent, self.nav_saved):
            b.setProperty("sub", True)
            b.clicked.connect(lambda _=False, k=b.kind: self.show_section(k))
            b.clicked.connect(self._pi)
            sv.addWidget(b)
        sv.addSpacing(24)
        # シミュレーター(カテゴリ)ごとに見出しを立て、その下に登録フォルダ。
        self.folder_box = QVBoxLayout()
        self.folder_box.setSpacing(4)
        sv.addLayout(self.folder_box)
        self.folder_navs = []
        self.category_labels = []
        sv.addSpacing(10)
        self.btn_add_folder = QPushButton("＋  フォルダを追加")
        self.btn_add_folder.setObjectName("addFolder")
        self.btn_add_folder.setCursor(Qt.PointingHandCursor)
        self.btn_add_folder.clicked.connect(self._pi)
        self.btn_add_folder.clicked.connect(self.add_folder)
        sv.addWidget(self.btn_add_folder)
        sv.addStretch(1)
        h.addWidget(side)

        # --- 右 ----------------------------------------------------------
        self.pages = QStackedWidget()
        h.addWidget(self.pages, 1)

        self.page_open = _Page("TJAを開く")
        card = GlassCard(radius=16, accent=True)
        card.setObjectName("openCard")
        card.setFixedSize(520, 200)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(30, 28, 30, 30)
        lbl = QLabel("TJA")
        lbl.setObjectName("openGlyph")
        cv.addWidget(lbl)
        cv.addStretch(1)
        self.btn_open_file = QPushButton("ファイルを選択…")
        self.btn_open_file.setObjectName("primary")
        self.btn_open_file.setCursor(Qt.PointingHandCursor)
        self.btn_open_file.clicked.connect(self._pi)
        self.btn_open_file.clicked.connect(self.open_file)
        bh = QHBoxLayout()
        bh.addWidget(self.btn_open_file)
        bh.addStretch(1)
        cv.addLayout(bh)
        self.page_open.v.addWidget(card)
        self.page_open.v.addStretch(1)
        self.pages.addWidget(self.page_open)

        self.page_recent = _Page("最近開いた楽曲")
        self.page_recent.add_search()
        self.page_recent.add_sort(song_lists.SORT_MODES)
        self.page_recent.sort.changed.connect(
            lambda m, r: self._on_sort_changed(True, m, r))
        self.recent_list = self.page_recent.add_list()
        self.pages.addWidget(self.page_recent)

        self.page_saved = _Page("保存済み楽曲")
        self.page_saved.add_search()
        self.page_saved.add_sort(song_lists.SORT_MODES[1:])
        self.page_saved.sort.changed.connect(
            lambda m, r: self._on_sort_changed(False, m, r))
        self.page_saved.tools.addSpacing(6)
        self.btn_add_saved = QPushButton("楽曲を追加")
        self.btn_add_saved.clicked.connect(self._pi)
        self.btn_add_saved.clicked.connect(self.add_saved_files)
        self.page_saved.tools.addWidget(self.btn_add_saved)
        self.saved_list = self.page_saved.add_list()
        self.pages.addWidget(self.page_saved)

        self.page_folder = _Page("")
        self.page_folder.add_search()
        self.page_folder.add_sort(song_lists.SORT_MODES[1:])
        self.page_folder.sort.changed.connect(
            lambda m, r: self._on_sort_changed(False, m, r))
        # ふつうは一段ぶんの一覧。検索欄に何か入っているあいだは、下の
        # フォルダ全部から探した曲だけの一覧に差し替える。
        self.page_folder.v.addSpacing(16)
        self.folder_stack = QStackedWidget()
        self.folder_list = RowList()
        self.search_list = RowList()
        self.folder_stack.addWidget(self.folder_list)
        self.folder_stack.addWidget(self.search_list)
        self.page_folder.list = self.folder_list
        self.page_folder.v.addWidget(self.folder_stack, 1)
        self.page_folder.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.pages.addWidget(self.page_folder)
        self._sync_sort_bars()

        if song_lists.migrate_folders(self.cfg):
            self._save()
        self._rebuild_folder_navs()
        self.refresh_theme()

    # ------------------------------------------------------------------
    @staticmethod
    def _section(text):
        lbl = QLabel(text)
        lbl.setObjectName("section")
        return lbl

    @staticmethod
    def _category_head(text):
        lbl = QLabel(text)
        lbl.setObjectName("category")
        return lbl

    # ------------------------------------------------------------------
    # 並べ方
    def _sync_sort_bars(self):
        m, r = song_lists.sort_state(self.cfg, recent_view=True)
        self.page_recent.sort.set_state(m, r)
        m, r = song_lists.sort_state(self.cfg)
        self.page_saved.sort.set_state(m, r)
        self.page_folder.sort.set_state(m, r)

    def _pi(self, *_args):
        self.sound_requested.emit("pi")

    def _on_sort_changed(self, recent_view, mode, reverse):
        self._pi()
        song_lists.set_sort_state(self.cfg, mode, reverse, recent_view=recent_view)
        self._save()
        self._sync_sort_bars()
        self._resort()

    def _resort(self):
        m, r = song_lists.sort_state(self.cfg, recent_view=True)
        self.recent_list.sort_songs(m, r)
        m, r = song_lists.sort_state(self.cfg)
        for lst in (self.saved_list, self.folder_list, self.search_list):
            lst.sort_songs(m, r)

    def _save(self):
        try:
            self._save_cb()
        except Exception:  # noqa: BLE001 — 保存に失敗しても一覧は使える
            pass

    def _all_navs(self):
        return [self.nav_open, self.nav_recent, self.nav_saved] + self.folder_navs

    def _check_nav(self, target):
        for b in self._all_navs():
            b.setChecked(b is target)

    def current_section(self):
        for b in self._all_navs():
            if b.isChecked():
                return b.kind if b.kind != "folder" else ("folder", b.path)
        return None

    def show_default(self):
        """Player に入ったとき。履歴があれば最近開いた楽曲、無ければ TJAを開く。"""
        self.show_section("recent" if song_lists.recent(self.cfg) else "open")

    def show_section(self, kind, folder=None):
        if kind == "open":
            self._check_nav(self.nav_open)
            self.pages.setCurrentWidget(self.page_open)
        elif kind == "recent":
            self._check_nav(self.nav_recent)
            self._recent_stale = False
            self._fill_recent()
            self._mark_chosen()
            self.pages.setCurrentWidget(self.page_recent)
        elif kind == "saved":
            self._check_nav(self.nav_saved)
            self._fill_saved()
            self.pages.setCurrentWidget(self.page_saved)
        elif kind == "folder" and folder:
            nav = next((b for b in self.folder_navs
                        if song_lists.path_key(b.path) == song_lists.path_key(folder)), None)
            self._check_nav(nav)
            self._folder_root = folder
            self.open_dir(folder)
            self.pages.setCurrentWidget(self.page_folder)

    # ------------------------------------------------------------------
    # 選ぶ
    def choose(self, path):
        """曲を選んだ(難易度選択へ)。選んだだけでは「最近開いた楽曲」に
        入れない — 見て回るだけで一覧が埋まってしまうため。入れるのは
        実際に演奏・録画を始めたとき(note_played)。"""
        if not path or not os.path.isfile(path):
            return
        self._chosen = path
        self._mark_chosen()
        self.song_chosen.emit(path)

    def _mark_chosen(self):
        key = song_lists.path_key(self._chosen) if self._chosen else None
        for lst in (self.recent_list, self.saved_list, self.folder_list, self.search_list):
            for r in lst.rows():
                if isinstance(r, SongRow):
                    r.set_chosen(key is not None and song_lists.path_key(r.path) == key)

    def note_played(self, path):
        """演奏・録画を始めた曲を「最近開いた楽曲」の先頭へ入れて保存する。"""
        if not path:
            return
        song_lists.add_recent(self.cfg, path)
        self._save()
        self._recent_stale = True

    #: 最近開いた楽曲が、出している一覧より新しくなっているか。
    _recent_stale = False

    def refresh_recent(self):
        """選曲へ戻ったとき。最近開いた楽曲を出していれば作り直す
        (選んだ行の印・検索の文字はそのまま)。"""
        if not self._recent_stale:
            return
        if self.pages.currentWidget() is self.page_recent:
            self._recent_stale = False
            self._fill_recent()
            self._mark_chosen()

    def open_file(self):
        start = os.path.dirname(self._chosen) if self._chosen else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "TJAを開く", start, "TJA (*.tja);;すべてのファイル (*.*)")
        if path:
            self.choose(path)

    # ------------------------------------------------------------------
    # 一覧を作る
    def _song_row(self, path, title, **kw):
        row = SongRow(path, title, **kw)
        row.chosen.connect(self._pi)
        row.chosen.connect(self.choose)
        row.save_toggled.connect(self._pi)
        row.save_toggled.connect(self._on_save_toggled)
        row.remove_requested.connect(self.remove_saved)
        row.relink_requested.connect(self.relink_saved)
        if self._chosen and song_lists.path_key(path) == song_lists.path_key(self._chosen):
            row.set_chosen(True)
        return row

    def _start_worker(self, paths=None, directory=None):
        detach_worker(self._worker)
        self._worker = None
        w = InfoWorker(paths=paths, directory=directory, parent=self)
        # 受け手は必ず self の結び付いたメソッドにする。ラムダへ繋ぐと受け手の
        # スレッドが分からず、ワーカーのスレッドでそのまま呼ばれて部品を作って
        # しまう(実際に行が欠けた)。メソッドなら GUI スレッドへ回される。
        w.infos.connect(self._on_infos)
        if directory is not None:
            w.listed.connect(self._on_listed)
        self._worker = w
        w.start()
        return w

    def _on_infos(self, worker, batch):
        if worker is not self._worker:
            return       # 古い走査の結果
        for path, info in batch:
            rows = self._pending_rows.pop(song_lists.path_key(path), None)
            if rows is None:
                # フォルダの中の曲(行はここで作る)
                row = self._song_row(path, info["title"],
                                     saved=song_lists.is_saved(self.cfg, path))
                row.set_info(info)
                self.folder_list.add(row)
                continue
            for row in rows:
                row.set_info(info)
        self._resort()
        page = self.pages.currentWidget()
        q = self._current_search()
        lst = getattr(page, "list", None)
        if q and lst is not None and page is not self.page_folder:
            lst.apply_filter(q)

    def _current_search(self):
        page = self.pages.currentWidget()
        s = getattr(page, "search", None)
        return s.text() if s is not None else ""

    def _fill_recent(self):
        self.recent_list.clear()
        self._pending_rows = {}
        paths = []
        for e in song_lists.recent(self.cfg):
            row = self._song_row(e["path"], e.get("title") or os.path.basename(e["path"]),
                                 saved=song_lists.is_saved(self.cfg, e["path"]),
                                 date_text=_when(e.get("opened")), show_path=True)
            self.recent_list.add(row)
            if row.missing:
                row.set_missing_chip()
            else:
                self._pending_rows.setdefault(song_lists.path_key(e["path"]), []).append(row)
                paths.append(e["path"])
        self.recent_list.apply_filter(self.page_recent.search.text())
        self._resort()
        if paths:
            self._start_worker(paths=paths)

    def _fill_saved(self):
        self.saved_list.clear()
        self._pending_rows = {}
        paths = []
        for e in song_lists.saved(self.cfg):
            row = self._song_row(e["path"], e.get("title") or os.path.basename(e["path"]),
                                 removable=True, show_path=True)
            self.saved_list.add(row)
            if row.missing:
                row.set_missing_chip()
            else:
                self._pending_rows.setdefault(song_lists.path_key(e["path"]), []).append(row)
                paths.append(e["path"])
        self.saved_list.apply_filter(self.page_saved.search.text())
        self._resort()
        if paths:
            self._start_worker(paths=paths)

    # ------------------------------------------------------------------
    # 保存済み
    def _on_save_toggled(self, path, on):
        if on:
            song_lists.add_saved(self.cfg, path)
        else:
            song_lists.remove_saved(self.cfg, path)
        self._save()

    def add_saved_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "楽曲を追加", "", "TJA (*.tja);;すべてのファイル (*.*)")
        added = False
        for p in paths:
            added = song_lists.add_saved(self.cfg, p) or added
        if added:
            self._save()
            self._fill_saved()

    def remove_saved(self, path):
        """保存済みから外す。元の TJA には触らない。"""
        if song_lists.remove_saved(self.cfg, path):
            self._save()
            self._fill_saved()

    def relink_saved(self, path):
        start = os.path.dirname(path)
        if not os.path.isdir(start):
            start = ""
        new, _ = QFileDialog.getOpenFileName(
            self, "再指定", start, "TJA (*.tja);;すべてのファイル (*.*)")
        if new and song_lists.relink_saved(self.cfg, path, new):
            self._save()
            self._fill_saved()

    # ------------------------------------------------------------------
    # 登録フォルダ
    def _rebuild_folder_navs(self):
        cur = self.current_section()
        while self.folder_box.count():
            item = self.folder_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.folder_navs = []
        self.category_labels = []
        for i, sim in enumerate(song_lists.simulators(self.cfg)):
            if i:
                self.folder_box.addSpacing(20)
            head = self._category_head(sim)
            head.setContextMenuPolicy(Qt.CustomContextMenu)
            head.customContextMenuRequested.connect(
                lambda pos, lbl=head, name=sim: self._category_menu(lbl, name, pos))
            self.folder_box.addWidget(head)
            self.folder_box.addSpacing(6)
            self.category_labels.append(head)
            for path in song_lists.folders_of(self.cfg, sim):
                name = os.path.basename(os.path.normpath(path)) or path
                b = NavButton(name, "folder")
                b.setProperty("sub", True)
                b.path = path
                b.simulator = sim
                b.setToolTip(path)
                if not os.path.isdir(path):
                    b.setProperty("missing", True)
                b.clicked.connect(lambda _=False, p=path: self.show_section("folder", p))
                b.clicked.connect(self._pi)
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(
                    lambda pos, btn=b: self._folder_menu(btn, pos))
                self.folder_box.addWidget(b)
                self.folder_navs.append(b)
        if isinstance(cur, tuple):
            for b in self.folder_navs:
                b.setChecked(song_lists.path_key(b.path) == song_lists.path_key(cur[1]))

    def _folder_menu(self, btn, pos):
        menu = QMenu(btn)
        act_remove = menu.addAction("外す")
        act_cat = menu.addAction("カテゴリを変更")
        act = menu.exec(btn.mapToGlobal(pos))
        if act is act_remove:
            self.remove_folder(btn.path)
        elif act is act_cat:
            self.change_folder_category(btn.path)

    def _category_menu(self, label, name, pos):
        menu = QMenu(label)
        act = menu.addAction("名前を変更")
        if menu.exec(label.mapToGlobal(pos)) is act:
            self.rename_category(name)

    def ask_category(self, current="", path="", heading="カテゴリ", ok_text="追加"):
        """カテゴリ名を打ってもらう。やめたら None。"""
        dlg = CategoryDialog(song_lists.simulators(self.cfg), current=current,
                             path=path, heading=heading, ok_text=ok_text,
                             parent=self.window())
        if dlg.exec() == QDialog.Accepted and dlg.value():
            return dlg.value()
        return None

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "フォルダを追加", "")
        if not path:
            return
        name = self.ask_category(current="", path=os.path.normpath(path))
        if name:
            self.add_folder_path(path, name)

    def add_folder_path(self, path, simulator=song_lists.OTHER_SIMULATOR):
        if not os.path.isdir(path):
            return
        if song_lists.add_folder(self.cfg, path, simulator):
            self._save()
            self._rebuild_folder_navs()
        self.show_section("folder", os.path.normpath(os.path.abspath(path)))

    def change_folder_category(self, path):
        cur = song_lists.folder_simulator(self.cfg, path) or ""
        name = self.ask_category(current=cur, path=path, ok_text="変更")
        if name and song_lists.set_folder_simulator(self.cfg, path, name):
            self._save()
            self._rebuild_folder_navs()

    def rename_category(self, old):
        name = self.ask_category(current=old, heading="名前を変更", ok_text="変更")
        if name and song_lists.rename_simulator(self.cfg, old, name):
            self._save()
            self._rebuild_folder_navs()

    def remove_folder(self, path):
        """登録を外すだけ(フォルダの中身には触らない)。"""
        was = self.current_section()
        if song_lists.remove_folder(self.cfg, path):
            self._save()
            self._rebuild_folder_navs()
            if isinstance(was, tuple) and song_lists.path_key(was[1]) == song_lists.path_key(path):
                self.show_default()

    def open_dir(self, directory):
        """登録フォルダの中の一段を開く。"""
        self._folder_dir = directory
        page = self.page_folder
        page.lbl_title.setText(song_lists.display_name(directory))
        self._build_crumbs(directory)
        page.search.blockSignals(True)
        page.search.clear()
        page.search.blockSignals(False)
        self._stop_search()
        self.folder_list.clear()
        self._pending_rows = {}
        if os.path.isdir(directory):
            self._start_worker(directory=directory)

    def _build_crumbs(self, directory):
        lay = self.page_folder.crumbs
        while lay.count() > 1:
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        root = self._folder_root or directory
        parts = [root]
        rel = os.path.relpath(directory, root) if directory != root else "."
        if rel != "." and not rel.startswith(".."):
            cur = root
            for seg in rel.split(os.sep):
                cur = os.path.join(cur, seg)
                parts.append(cur)
        for i, p in enumerate(parts):
            if i:
                sep = QLabel("›")
                sep.setObjectName("crumbSep")
                lay.insertWidget(lay.count() - 1, sep)
            b = QPushButton(song_lists.display_name(p))
            b.setObjectName("crumbCur" if i == len(parts) - 1 else "crumb")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=p: self.open_dir(d))
            b.clicked.connect(self._pi)
            lay.insertWidget(lay.count() - 1, b)

    def _on_listed(self, worker, genres, dirs):
        if worker is not self._worker:
            return
        for d, box in genres:
            row = GenreRow(d, box)
            row.opened.connect(self._pi)
            row.opened.connect(self.open_dir)
            self.folder_list.add(row)
        for d in dirs:
            row = FolderRow(d)
            row.opened.connect(self._pi)
            row.opened.connect(self.open_dir)
            self.folder_list.add(row)

    # ------------------------------------------------------------------
    # 登録フォルダの中の検索(下のフォルダも全部)
    def _stop_search(self):
        self._search_timer.stop()
        detach_worker(self._search_worker)
        self._search_worker = None
        self.search_list.clear()
        self.folder_stack.setCurrentWidget(self.folder_list)

    def _run_folder_search(self):
        q = self.page_folder.search.text().strip()
        if not q or not self._folder_dir:
            self._stop_search()
            return
        detach_worker(self._search_worker)
        self.search_list.clear()
        self.folder_stack.setCurrentWidget(self.search_list)
        self._search_names = {}
        w = SearchWorker(self._folder_dir, q, parent=self)
        w.found.connect(self._on_search_found)
        self._search_worker = w
        w.start()

    def _on_search_found(self, worker, batch):
        if worker is not self._search_worker:
            return       # 打ち直す前の検索の結果
        root = worker.root
        names = self._search_names

        def name(d):
            # ジャンルは box.def の題で見せる。同じフォルダは何度も読まない。
            if d not in names:
                names[d] = song_lists.display_name(d)
            return names[d]

        for path, info in batch:
            rel = os.path.relpath(os.path.dirname(path), root)
            dirs = [root]
            if rel != ".":
                for seg in rel.split(os.sep):
                    dirs.append(os.path.join(dirs[-1], seg))
            text = "  ›  ".join(name(d) for d in dirs)
            row = self._song_row(path, info["title"],
                                 saved=song_lists.is_saved(self.cfg, path),
                                 path_text=text)
            row.set_info(info)
            self.search_list.add(row)
        m, r = song_lists.sort_state(self.cfg)
        self.search_list.sort_songs(m, r)

    # ------------------------------------------------------------------
    def refresh_theme(self):
        for lst in (self.recent_list, self.saved_list, self.folder_list, self.search_list):
            for r in lst.rows():
                r.refresh_theme()
        self.update()

    def shutdown(self):
        detach_worker(self._worker)
        self._worker = None
        self._search_timer.stop()
        detach_worker(self._search_worker)
        self._search_worker = None


def library_qss():
    """選曲の画面だけの QSS(ランチャーの QSS に足す)。"""
    p = COLORS
    g = glass_colors()
    fam = wordmark_family()
    fam_css = ('font-family: "%s";' % fam) if fam else ""
    return f"""
    QLabel#section {{ color: {p['fg_dim']}; {fam_css} font-size: 12px; font-weight: bold;
                      letter-spacing: 2px; padding: 0 12px; }}
    QPushButton {{ font-weight: 600; }}
    QPushButton#nav {{ text-align: left; background-color: transparent; border: 1px solid transparent;
                       border-radius: 9px; padding: 10px 14px 10px 18px; font-size: 14px; color: {p['fg']};
                       font-weight: 600; }}
    QPushButton#nav:hover {{ background-color: {_rgba(g['btn_fill'])}; }}
    QPushButton#nav:checked {{ background-color: {_rgba(g['btn_fill_hover'])};
                               border: 1px solid {_rgba(g['accent_border'])}; color: {p['fg_bright']}; font-weight: bold; }}
    QPushButton#nav:focus {{ border: 1px solid {p['accent']}; }}
    QPushButton#nav[missing="true"] {{ color: {p['fg_dim']}; }}
    QPushButton#addFolder {{ text-align: left; background-color: transparent; border: 1px dashed {_rgba(g['btn_border'])};
                             border-radius: 9px; padding: 9px 14px; color: {p['fg_dim']}; font-size: 13px; }}
    QPushButton#addFolder:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
    QLabel#pageTitle {{ color: {p['fg_bright']}; {fam_css} font-size: 34px; font-weight: 600; letter-spacing: 1px; }}
    QLabel#openGlyph {{ color: {p['accent']}; {fam_css} font-size: 44px; font-weight: 200; letter-spacing: 6px; }}
    QLabel#empty {{ color: {p['fg_dim']}; font-size: 14px; font-weight: 600; padding-top: 40px; }}
    QLabel#chevron {{ color: {p['fg_dim']}; font-size: 26px; padding-bottom: 3px; }}
    QLabel#crumbSep {{ color: {p['fg_dim']}; font-size: 13px; }}
    QPushButton#crumb, QPushButton#crumbCur {{ background-color: transparent; border: 1px solid transparent;
                       padding: 2px 6px; border-radius: 6px; font-size: 13px; color: {p['fg_dim']}; }}
    QPushButton#crumb:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
    QPushButton#crumbCur {{ color: {p['fg']}; }}
    QLineEdit#search {{ background-color: {_rgba(g['field_fill'])}; border: 1px solid {_rgba(g['field_border'])};
                        border-radius: 9px; padding: 8px 12px; font-size: 13px; color: {p['fg']}; }}
    QLineEdit#search:focus {{ border: 1px solid {p['accent']}; }}
    QPushButton#small {{ padding: 5px 14px; border-radius: 7px; font-size: 12px; }}
    QPushButton#star {{ background-color: transparent; border: 1px solid transparent; padding: 0;
                        font-size: 18px; color: {p['fg_dim']}; }}
    QPushButton#star:hover {{ background-color: {_rgba(g['btn_fill'])}; color: {p['fg']}; }}
    QPushButton#star[on="true"] {{ color: {p['kusudama']}; }}
    QWidget#rowListInner, QWidget#sideInner {{ background: transparent; }}
    QLabel#dlgHead {{ color: {p['fg_bright']}; {fam_css} font-size: 20px; font-weight: 600; letter-spacing: 1px; }}
    QComboBox#category {{ background-color: {_rgba(g['field_fill'])}; border: 1px solid {_rgba(g['field_border'])};
                          border-radius: 9px; padding: 4px 12px; font-size: 14px; color: {p['fg']}; }}
    QComboBox#category:focus {{ border: 1px solid {p['accent']}; }}
    QComboBox#category QLineEdit {{ background: transparent; border: none; color: {p['fg']}; font-size: 14px; }}
    QComboBox#category::drop-down {{ border: none; width: 26px; }}
    QComboBox#category QAbstractItemView {{ background-color: {p['bg']}; color: {p['fg']};
                          border: 1px solid {_rgba(g['btn_border'])}; selection-background-color: {_rgba(g['btn_fill_hover'])}; }}
    QPushButton:disabled {{ color: {p['fg_dim']}; }}
    QLabel#category {{ color: {p['fg_bright']}; {fam_css} font-size: 15px; font-weight: bold;
                       letter-spacing: 1px; padding: 1px 0 1px 10px; margin-left: 2px;
                       border-left: 3px solid {p['accent']}; }}
    QPushButton#nav[sub="true"] {{ padding-left: 30px; font-size: 13px; }}
    QLabel#bpm {{ color: {p['fg_dim']}; font-size: 12px; }}
    QPushButton#seg {{ background-color: {_rgba(g['btn_fill'])}; border: 1px solid {_rgba(g['btn_border'])};
                       border-radius: 0; padding: 6px 14px; font-size: 12px; color: {p['fg_dim']}; }}
    QPushButton#seg[pos="first"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
    QPushButton#seg[pos="last"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
    QPushButton#seg[pos="mid"], QPushButton#seg[pos="last"] {{ border-left: none; }}
    QPushButton#seg:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill_hover'])}; }}
    QPushButton#seg:checked {{ color: {g['primary_fg'].name()}; background-color: {_rgba(_c(p['accent'], 0.55))};
                               font-weight: bold; }}
    QPushButton#sortDir {{ padding: 0; border-radius: 8px; font-size: 15px; font-weight: bold; color: {p['fg']}; }}
    """
