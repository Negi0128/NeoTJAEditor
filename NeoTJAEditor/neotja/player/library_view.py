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

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
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


# ----------------------------------------------------------------------
# ワーカー
# ----------------------------------------------------------------------
class InfoWorker(QThread):
    """パスの一覧、またはフォルダ一段ぶんの見出しを読む。"""

    listed = Signal(object, object)   # (サブフォルダ, TJA) — フォルダのときだけ
    infos = Signal(object)            # [(path, info)]

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
            dirs, paths = song_lists.list_dir(self._dir)
            if self._cancelled:
                return
            self.listed.emit(dirs, paths)
        batch, last = [], time.monotonic()
        for p in paths:
            if self._cancelled:
                return
            batch.append((p, song_lists.song_info(p)))
            now = time.monotonic()
            if len(batch) >= _BATCH or now - last >= _BATCH_SEC:
                self.infos.emit(batch)
                batch, last = [], now
        if batch and not self._cancelled:
            self.infos.emit(batch)


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
                 date_text="", show_path=False, parent=None):
        super().__init__(radius=10, hoverable=True, parent=parent)
        self.path = path
        self.missing = not os.path.isfile(path)
        self.info = {"title": title, "subtitle": "", "courses": []}
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
        self.sub = ElidedText(_short_dir(path) if show_path else "", "fg_dim", 12,
                              elide=show_path)
        self.sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._show_path = show_path
        texts.addWidget(self.sub)
        h.addLayout(texts, 1)

        self.chip_box = QHBoxLayout()
        self.chip_box.setSpacing(6)
        h.addLayout(self.chip_box)
        self.chips = []

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
        if not q:
            return True
        return (q in self.info["title"].lower()
                or q in self.info.get("subtitle", "").lower()
                or q in os.path.basename(self.path).lower())

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
        return not q or q in self.name.lower()

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
        self._v.insertWidget(self._v.count() - 1, row)
        self._rows.append(row)
        self.empty.setVisible(False)

    def apply_filter(self, q):
        q = q.strip().lower()
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
            p.drawLine(4, 12, 4, self.height() - 12)
            p.end()


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

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self._save_cb = save_cb
        self._worker = None
        self._chosen = ""
        self._folder_root = ""
        self._folder_dir = ""
        self._pending_rows = {}

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # --- 左 ----------------------------------------------------------
        side = _Sidebar()
        side.setFixedWidth(SIDEBAR_WIDTH)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 22, 16, 22)
        sv.setSpacing(4)
        sv.addWidget(self._section("NeoTJAPlayer"))
        sv.addSpacing(6)
        self.nav_open = NavButton("TJAを開く", "open")
        self.nav_recent = NavButton("最近開いた楽曲", "recent")
        self.nav_saved = NavButton("保存済み楽曲", "saved")
        for b in (self.nav_open, self.nav_recent, self.nav_saved):
            b.clicked.connect(lambda _=False, k=b.kind: self.show_section(k))
            sv.addWidget(b)
        sv.addSpacing(22)
        sv.addWidget(self._section("フォルダ"))
        sv.addSpacing(6)
        self.folder_box = QVBoxLayout()
        self.folder_box.setSpacing(4)
        sv.addLayout(self.folder_box)
        self.folder_navs = []
        sv.addSpacing(6)
        self.btn_add_folder = QPushButton("＋  フォルダを追加")
        self.btn_add_folder.setObjectName("addFolder")
        self.btn_add_folder.setCursor(Qt.PointingHandCursor)
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
        self.recent_list = self.page_recent.add_list()
        self.pages.addWidget(self.page_recent)

        self.page_saved = _Page("保存済み楽曲")
        self.page_saved.add_search()
        self.btn_add_saved = QPushButton("楽曲を追加")
        self.btn_add_saved.clicked.connect(self.add_saved_files)
        self.page_saved.tools.addWidget(self.btn_add_saved)
        self.saved_list = self.page_saved.add_list()
        self.pages.addWidget(self.page_saved)

        self.page_folder = _Page("")
        self.page_folder.add_search()
        self.folder_list = self.page_folder.add_list()
        self.pages.addWidget(self.page_folder)

        self._rebuild_folder_navs()
        self.refresh_theme()

    # ------------------------------------------------------------------
    @staticmethod
    def _section(text):
        lbl = QLabel(text)
        lbl.setObjectName("section")
        return lbl

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
            self._fill_recent()
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
        if not path or not os.path.isfile(path):
            return
        self._chosen = path
        song_lists.add_recent(self.cfg, path)
        self._save()
        for lst in (self.recent_list, self.saved_list, self.folder_list):
            for r in lst.rows():
                if isinstance(r, SongRow):
                    r.set_chosen(song_lists.path_key(r.path) == song_lists.path_key(path))
        self.song_chosen.emit(path)

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
        row.chosen.connect(self.choose)
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
        w.infos.connect(lambda batch, w=w: self._on_infos(w, batch))
        if directory is not None:
            w.listed.connect(lambda d, t, w=w: self._on_listed(w, d, t))
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
        q = self._current_search()
        lst = getattr(self.pages.currentWidget(), "list", None)
        if q and lst is not None:
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
        for b in self.folder_navs:
            b.setParent(None)
            b.deleteLater()
        self.folder_navs = []
        for path in song_lists.folders(self.cfg):
            name = os.path.basename(os.path.normpath(path)) or path
            b = NavButton(name, "folder")
            b.path = path
            b.setToolTip(path)
            if not os.path.isdir(path):
                b.setProperty("missing", True)
            b.clicked.connect(lambda _=False, p=path: self.show_section("folder", p))
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
        act = menu.addAction("外す")
        if menu.exec(btn.mapToGlobal(pos)) is act:
            self.remove_folder(btn.path)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "フォルダを追加", "")
        if path:
            self.add_folder_path(path)

    def add_folder_path(self, path):
        if not os.path.isdir(path):
            return
        if song_lists.add_folder(self.cfg, path):
            self._save()
            self._rebuild_folder_navs()
        self.show_section("folder", os.path.normpath(os.path.abspath(path)))

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
        page.lbl_title.setText(os.path.basename(os.path.normpath(directory)) or directory)
        self._build_crumbs(directory)
        page.search.blockSignals(True)
        page.search.clear()
        page.search.blockSignals(False)
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
            b = QPushButton(os.path.basename(os.path.normpath(p)) or p)
            b.setObjectName("crumbCur" if i == len(parts) - 1 else "crumb")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=p: self.open_dir(d))
            lay.insertWidget(lay.count() - 1, b)

    def _on_listed(self, worker, dirs, tjas):
        if worker is not self._worker:
            return
        for d in dirs:
            row = FolderRow(d)
            row.opened.connect(self.open_dir)
            self.folder_list.add(row)

    # ------------------------------------------------------------------
    def refresh_theme(self):
        for lst in (self.recent_list, self.saved_list, self.folder_list):
            for r in lst.rows():
                r.refresh_theme()
        self.update()

    def shutdown(self):
        detach_worker(self._worker)
        self._worker = None


def library_qss():
    """選曲の画面だけの QSS(ランチャーの QSS に足す)。"""
    p = COLORS
    g = glass_colors()
    fam = wordmark_family()
    fam_css = ('font-family: "%s";' % fam) if fam else ""
    return f"""
    QLabel#section {{ color: {p['fg_dim']}; {fam_css} font-size: 12px; font-weight: bold;
                      letter-spacing: 2px; padding: 0 12px; }}
    QPushButton#nav {{ text-align: left; background-color: transparent; border: 1px solid transparent;
                       border-radius: 9px; padding: 10px 14px 10px 18px; font-size: 14px; color: {p['fg']}; }}
    QPushButton#nav:hover {{ background-color: {_rgba(g['btn_fill'])}; }}
    QPushButton#nav:checked {{ background-color: {_rgba(g['btn_fill_hover'])};
                               border: 1px solid {_rgba(g['accent_border'])}; color: {p['fg_bright']}; font-weight: bold; }}
    QPushButton#nav:focus {{ border: 1px solid {p['accent']}; }}
    QPushButton#nav[missing="true"] {{ color: {p['fg_dim']}; }}
    QPushButton#addFolder {{ text-align: left; background-color: transparent; border: 1px dashed {_rgba(g['btn_border'])};
                             border-radius: 9px; padding: 9px 14px; color: {p['fg_dim']}; font-size: 13px; }}
    QPushButton#addFolder:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
    QLabel#pageTitle {{ color: {p['fg_bright']}; {fam_css} font-size: 34px; font-weight: 300; letter-spacing: 1px; }}
    QLabel#openGlyph {{ color: {p['accent']}; {fam_css} font-size: 44px; font-weight: 200; letter-spacing: 6px; }}
    QLabel#empty {{ color: {p['fg_dim']}; font-size: 14px; padding-top: 40px; }}
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
    QWidget#rowListInner {{ background: transparent; }}
    """
