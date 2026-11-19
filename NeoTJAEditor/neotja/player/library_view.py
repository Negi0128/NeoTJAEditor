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
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from neotja.player import playlists, song_lists, thumbs
from neotja.player.playlist_view import (
    AddModeBar, CourseMarkIcon, CoursePicker, PlaylistDialog, PlaylistRow,
    playlist_jacket_pixmap,
)
from neotja.start_screen import (
    COURSE_COLOR_KEY, ElidedText, GlassCard, wordmark_family,
)
from neotja.theme import COLORS, glass_colors
from neotja.theme import with_alpha as _c, rgba as _rgba
from neotja.worker_util import detach_worker

#: 曲の表の1行の高さ。
ROW_HEIGHT = 40
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
    #: 行の左のジャケット(infos のすぐあとに同じ曲の分を送る)。
    thumbs = Signal(object, object)           # (worker, [(path, 鍵, QImage or None)])

    def __init__(self, paths=None, directory=None, parent=None,
                 art_cfg=None, known=frozenset()):
        super().__init__(parent)
        self._paths = list(paths or [])
        self._dir = directory
        self._cancelled = False
        #: 曲別の絵の設定の写し(thumbs.art_snapshot)。None ならジャケットは読まない。
        self._art_cfg = art_cfg
        self._known = known

    def cancel(self):
        self._cancelled = True

    def run(self):
        paths = self._paths
        if self._dir is not None:
            genres, dirs, paths = song_lists.list_dir(self._dir)
            if self._cancelled:
                return
            self.listed.emit(self, genres, dirs)
        batch, tb, last = [], [], time.monotonic()
        for p in paths:
            if self._cancelled:
                return
            batch.append((p, song_lists.song_info(p)))
            if self._art_cfg is not None:
                tb.append((p,) + thumbs.thumb_for_song(self._art_cfg, p, self._known))
            now = time.monotonic()
            if len(batch) >= _BATCH or now - last >= _BATCH_SEC:
                self.infos.emit(self, batch)
                if tb:
                    self.thumbs.emit(self, tb)
                batch, tb, last = [], [], now
        if batch and not self._cancelled:
            self.infos.emit(self, batch)
            if tb:
                self.thumbs.emit(self, tb)


class SearchWorker(QThread):
    """フォルダの下(サブフォルダ・ジャンルの中も全部)から、題名・副題・
    ファイル名に query を含む曲を探す。"""

    found = Signal(object, object)   # (worker, [(path, info)])
    thumbs = Signal(object, object)  # (worker, [(path, 鍵, QImage or None)])
    done = Signal(object)            # worker

    def __init__(self, root, query, parent=None, art_cfg=None, known=frozenset()):
        super().__init__(parent)
        self.root = root
        self.query = query
        self._cancelled = False
        self._art_cfg = art_cfg
        self._known = known

    def cancel(self):
        self._cancelled = True

    def run(self):
        batch, tb, last = [], [], time.monotonic()
        for p in song_lists.walk_tjas(self.root, lambda: self._cancelled):
            if self._cancelled:
                return
            info = song_lists.song_info(p)
            if song_lists.matches(self.query, info, p):
                batch.append((p, info))
                if self._art_cfg is not None:
                    tb.append((p,) + thumbs.thumb_for_song(self._art_cfg, p, self._known))
            now = time.monotonic()
            if batch and (len(batch) >= _BATCH or now - last >= _BATCH_SEC):
                self.found.emit(self, batch)
                if tb:
                    self.thumbs.emit(self, tb)
                batch, tb, last = [], [], now
        if self._cancelled:
            return
        if batch:
            self.found.emit(self, batch)
            if tb:
                self.thumbs.emit(self, tb)
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


def _is_under(path, root):
    """path が root そのものか、その下か。"""
    try:
        a = song_lists.path_key(path)
        b = song_lists.path_key(root)
        return a == b or os.path.commonpath([a, b]) == b
    except ValueError:
        return False


class SongThumb(QWidget):
    """行の左のジャケット(角を丸めた正方形)。絵が無ければ空の枠だけ描く
    (題名の位置をそろえるため、枠はいつも出す)。"""

    RADIUS = 4

    def __init__(self, parent=None, size=None, tint=None):
        super().__init__(parent)
        side = int(size or thumbs.THUMB_PX)
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.pixmap = None
        #: 絵が無いときにうっすら塗るジャンルの色(box.def)。無ければ今までの暗い枠。
        self.tint = QColor(tint) if tint else None

    def set_pixmap(self, pm):
        pm = pm if (pm is not None and not pm.isNull()) else None
        if pm is self.pixmap:
            return
        self.pixmap = pm
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.RADIUS
        if self.pixmap is not None:
            path = QPainterPath()
            path.addRoundedRect(r, rad, rad)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r.toRect(), self.pixmap)
            p.restore()
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        else:
            p.setBrush(_c(self.tint, 0.18) if self.tint is not None else QColor(10, 20, 36, 150))
            p.setPen(QPen(QColor(127, 227, 255, 70), 1.0))
        p.drawRoundedRect(r, rad, rad)
        p.end()


class PlayMark(QWidget):
    """選んでいる(試聴している)行の右端の印。流しているあいだは水色の
    棒が揺れ、止めると丸の中の ⏸。押すと止める・流し直す(行の押下には
    しない)。選んでいない行では何も描かず、押しても行へ通す。"""

    clicked = Signal()
    SIZE = 32
    #: 棒を揺らす間合い(ms)。
    TICK_MS = 90

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.state = None        # None / "playing" / "paused"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
        self.setAttribute(Qt.WA_TransparentForMouseEvents, state is None)
        self.setCursor(Qt.PointingHandCursor if state else Qt.ArrowCursor)
        self._sync_timer()
        self.update()

    def _sync_timer(self):
        if self.state == "playing" and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._sync_timer()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()

    def _tick(self):
        self._phase += self.TICK_MS / 1000.0
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.state is not None:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # 速い2回押しも止める/流すの2回(行へ回して難易度選択へ進ませない)。
        self.mousePressEvent(ev)

    def paintEvent(self, ev):
        if self.state is None:
            return
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cyan = QColor(127, 227, 255)
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        if self.state == "playing":
            p.setPen(Qt.NoPen)
            p.setBrush(_c(cyan, 0.16))
            p.drawEllipse(c, 15.0, 15.0)
            p.setBrush(cyan)
            w, gap, top = 3.4, 2.6, 16.0
            x0 = c.x() - (3 * w + 2 * gap) / 2.0
            base = c.y() + top / 2.0
            for i, (sp, off) in enumerate(((7.0, 0.0), (9.5, 1.7), (6.0, 3.1))):
                k = 0.5 + 0.5 * math.sin(self._phase * sp + off)
                hgt = 5.0 + (top - 5.0) * k
                p.drawRoundedRect(QRectF(x0 + i * (w + gap), base - hgt, w, hgt), 1.5, 1.5)
        else:
            p.setPen(QPen(_c(cyan, 0.75), 1.5))
            p.setBrush(QColor(10, 20, 36, 150))
            p.drawEllipse(c, 14.0, 14.0)
            p.setPen(Qt.NoPen)
            p.setBrush(_c(cyan, 0.9))
            for dx in (-4.2, 1.4):
                p.drawRoundedRect(QRectF(c.x() + dx, c.y() - 6.5, 2.8, 13.0), 1.2, 1.2)
        p.end()


#: 曲の表の列の幅(px)。曲名だけが伸びる。行と見出しで同じ数字を使う。
COLW = {"num": 34, "thumb": 32, "folder": 300, "course": 156, "date": 84,
        "action": 64, "star": 30, "mark": 30}
#: 行の左右の余白と列のあいだ。
ROW_MARGINS = (10, 0, 6, 0)
ROW_SPACING = 14
#: 表の幅の上限(広い窓で列が離れすぎないように)。
TABLE_MAX_W = 1400
#: 選んでいる行の左の縁・押した合図の色(Player のシアン)。
CYAN = QColor(127, 227, 255)

_GENRE_COLOR = {}


def genre_color(path):
    """曲の入っているジャンル(近い box.def)の色。無ければ None。"""
    d = os.path.dirname(os.path.normpath(path or ""))
    seen = []
    for _ in range(5):
        if not d:
            break
        key = song_lists.path_key(d)
        if key in _GENRE_COLOR:
            col = _GENRE_COLOR[key]
            break
        seen.append(key)
        box = song_lists.parse_box_def(d) if os.path.isdir(d) else None
        if box is not None:
            col = box.get("color") or None
            break
        parent = os.path.dirname(d)
        if parent == d:
            col = None
            break
        d = parent
    else:
        col = None
    for k in seen:
        _GENRE_COLOR[k] = col
    return col


_DISPLAY_NAME = {}


def _display_name(d):
    key = song_lists.path_key(d)
    if key not in _DISPLAY_NAME:
        try:
            _DISPLAY_NAME[key] = song_lists.display_name(d)
        except Exception:  # noqa: BLE001
            _DISPLAY_NAME[key] = os.path.basename(d)
    return _DISPLAY_NAME[key]


def folder_text(path):
    """フォルダの欄: 曲の置き場所の親を2段(ジャンルは box.def の題で)。曲の
    フォルダが曲と同じ名前ならそれは省く(同じ言葉が2回並ぶだけなので)。"""
    folder = os.path.dirname(os.path.normpath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    if song_lists.normalize(os.path.basename(folder)) == song_lists.normalize(stem):
        folder = os.path.dirname(folder)
    dirs = []
    d = folder
    for _ in range(2):
        if not d or os.path.dirname(d) == d:
            break
        dirs.insert(0, d)
        d = os.path.dirname(d)
    return "  ›  ".join(_display_name(x) for x in dirs)


class _TextCell(QWidget):
    """1行の字(長ければ省略)。曲名の欄は副題を同じ行に淡い色で続ける。"""

    def __init__(self, text, px, bold=False, color=None, sub="", sub_px=12,
                 elide=Qt.ElideRight, align=Qt.AlignLeft, parent=None):
        super().__init__(parent)
        self._text = text
        self.sub = sub
        self.px, self.bold, self.sub_px = px, bold, sub_px
        self.color = color
        self.elide = elide
        self.align = align
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def text(self):
        return self._text

    def set_text(self, text, sub=None):
        self._text = text
        if sub is not None:
            self.sub = sub
        self.update()

    def refresh(self):
        self.update()

    def _font(self, px, bold):
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def paintEvent(self, ev):
        from PySide6.QtGui import QFontMetrics
        p = QPainter(self)
        r = self.rect()
        f = self._font(self.px, self.bold)
        fm = QFontMetrics(f)
        col = self.color() if callable(self.color) else (self.color or COLORS["fg_bright"])
        text = fm.elidedText(self._text, self.elide, max(10, r.width()))
        p.setFont(f)
        p.setPen(QColor(col))
        flags = self.align | Qt.AlignVCenter
        p.drawText(r, flags, text)
        if self.sub and text == self._text and self.align == Qt.AlignLeft:
            used = fm.horizontalAdvance(text) + 12
            left = r.width() - used
            if left > 30:
                sf = self._font(self.sub_px, False)
                sm = QFontMetrics(sf)
                p.setFont(sf)
                p.setPen(QColor(secondary_color()))
                p.drawText(r.adjusted(used, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                           sm.elidedText(self.sub, Qt.ElideRight, left))
        p.end()


class CourseCell(QWidget):
    """難易度の欄: コースのマーク(小さく)・名前・★。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(COLW["course"])
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.icon = CourseMarkIcon(None, px=24)
        h.addWidget(self.icon, 0, Qt.AlignVCenter)
        self.label = QLabel("")
        self.label.setObjectName("courseText")
        h.addWidget(self.label, 1)
        self.course = None
        self.level = None

    def set_course(self, course, level):
        self.course, self.level = course, level
        self.icon.set_course(course)
        self.icon.setVisible(bool(course))
        if not course:
            self.label.setText("")
            return
        name = song_lists.COURSE_LABELS.get(course, course)
        self.label.setText(name if level is None else "%s  ★%d" % (name, level))
        self.label.setProperty("missing", False)

    def set_missing(self):
        self.course, self.level = None, None
        self.icon.hide()
        self.label.setText("ファイルなし")
        self.label.setProperty("missing", True)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)


def list_columns(num=False, date=False, action=False):
    """表の列の組み合わせ(行と見出しで同じものを使う)。"""
    return {"num": bool(num), "date": bool(date), "action": bool(action)}


class SongRow(QWidget):
    """曲の1行(表の1行。40px・角なし・下に細い線)。

    列: [#] [ジャケット 32px] [曲名(副題)] [フォルダ] [難易度] [日付] [外す/再指定]
    [☆] [▶]。選んでいる行は一段明るく、左の縁がシアン。"""

    chosen = Signal(str)
    #: 右端の印(試聴を止める・流し直す)を押した。
    mark_clicked = Signal(str)
    save_toggled = Signal(str, bool)
    remove_requested = Signal(str)
    relink_requested = Signal(str)
    #: 右クリック (曲, 画面の位置)。
    menu_requested = Signal(str, object)

    #: 追加したときに光らせる長さ(ms)。
    FLASH_MS = 650
    _flash = False

    def __init__(self, path, title, *, saved=None, removable=False,
                 date_text="", show_path=False, path_text=None, cols=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.missing = not os.path.isfile(path)
        self.info = {"title": title, "subtitle": "", "courses": [], "bpm": None}
        self.order = 0
        self._saved = saved
        self._accent = False
        self._hover = False
        self.cols = dict(cols or list_columns())
        self.setObjectName("songRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(path)
        if not self.missing:
            self.setFocusPolicy(Qt.StrongFocus)
            self.setCursor(Qt.PointingHandCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(*ROW_MARGINS)
        h.setSpacing(ROW_SPACING)
        if self.cols["num"]:
            self.num = QLabel("")
            self.num.setObjectName("rowNum")
            self.num.setFixedWidth(COLW["num"])
            self.num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(self.num)
        self.thumb = SongThumb(size=COLW["thumb"], tint=genre_color(path))
        h.addWidget(self.thumb, 0, Qt.AlignVCenter)
        self.title = _TextCell(title, 15, bold=True,
                               color=(lambda: COLORS["fg_dim"]) if self.missing
                               else (lambda: COLORS["fg_bright"]))
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h.addWidget(self.title, 1)
        if path_text is None:
            path_text = folder_text(path)
        self.sub = _TextCell(path_text, 13, color=secondary_color, elide=Qt.ElideMiddle)
        self.sub.setFixedWidth(COLW["folder"])
        h.addWidget(self.sub)
        self.course_cell = CourseCell()
        h.addWidget(self.course_cell)
        self.chips = []
        if self.cols["date"]:
            self.date = QLabel(date_text)
            self.date.setObjectName("rowDate")
            self.date.setFixedWidth(COLW["date"])
            self.date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(self.date)
        if self.cols["action"]:
            slot = QWidget()
            slot.setFixedWidth(COLW["action"])
            sh = QHBoxLayout(slot)
            sh.setContentsMargins(0, 0, 0, 0)
            if self.missing:
                self.btn_relink = QPushButton("再指定")
                self.btn_relink.setObjectName("small")
                self.btn_relink.clicked.connect(lambda: self.relink_requested.emit(self.path))
                sh.addWidget(self.btn_relink)
            if removable:
                self.btn_remove = QPushButton("外す")
                self.btn_remove.setObjectName("small")
                self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self.path))
                sh.addWidget(self.btn_remove)
            h.addWidget(slot)
        star_slot = QWidget()
        star_slot.setFixedWidth(COLW["star"])
        st = QHBoxLayout(star_slot)
        st.setContentsMargins(0, 0, 0, 0)
        if saved is not None and not self.missing:
            self.btn_star = QPushButton()
            self.btn_star.setObjectName("star")
            self.btn_star.setFixedSize(COLW["star"], 30)
            self.btn_star.setCursor(Qt.PointingHandCursor)
            self.btn_star.clicked.connect(self._toggle_saved)
            st.addWidget(self.btn_star)
            self._sync_star()
        h.addWidget(star_slot)
        # 試聴の印(選んでいる行だけ描く。場所はいつも取っておく)。
        self.mark = PlayMark()
        self.mark.setFixedSize(COLW["mark"], COLW["mark"])
        self.mark.clicked.connect(lambda: self.mark_clicked.emit(self.path))
        h.addWidget(self.mark, 0, Qt.AlignVCenter)
        self._show_course()

    # ------------------------------------------------------------------
    def _show_course(self):
        """難易度の欄: いちばん難しいコース(プレイリストの行は保存したコース)。"""
        if self.missing:
            return
        courses = self.info.get("courses") or []
        if courses:
            self.course_cell.set_course(courses[0][0], courses[0][1])
        else:
            self.course_cell.set_course(None, None)

    def set_info(self, info):
        self.info = info
        self.title.set_text(info["title"], info.get("subtitle", ""))
        self._show_course()

    def set_thumb(self, pm):
        """左のジャケット。None ならジャンルの色をうっすら塗った枠。"""
        self.thumb.set_pixmap(pm)

    def set_missing_chip(self):
        self.course_cell.set_missing()
        self.chips = [self.course_cell.label]

    def matches(self, q):
        return song_lists.matches(q, self.info, self.path)

    def set_chosen(self, on, playing=True):
        self._accent = bool(on)
        self.mark.set_state(None if not on else ("playing" if playing else "paused"))
        self.update()

    def flash(self):
        """追加した合図に、しばらく光らせる。"""
        self._flash = True
        self.update()

        def off():
            try:
                self._flash = False
                self.update()
            except RuntimeError:
                pass
        QTimer.singleShot(self.FLASH_MS, off)

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def focusInEvent(self, ev):
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev):
        self.update()
        super().focusOutEvent(ev)

    def contextMenuEvent(self, ev):
        self.menu_requested.emit(self.path, ev.globalPos())
        ev.accept()

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.rect()
        if self._flash:
            p.fillRect(r, _c(CYAN, 0.20))
        elif self._accent:
            p.fillRect(r, _c(CYAN, 0.10))
        elif self._hover and not self.missing:
            p.fillRect(r, QColor(255, 255, 255, 10))
        # 下の細い線(表の区切り)。
        p.fillRect(QRectF(0, r.height() - 1, r.width(), 1), QColor(255, 255, 255, 18))
        if self._accent or self._flash:
            p.fillRect(QRectF(0, 0, 3, r.height()), CYAN)
        if self.hasFocus() and not self._accent:
            p.setPen(QPen(_c(COLORS["accent"], 0.9), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(r).adjusted(0.5, 0.5, -1.5, -1.5))
        p.end()

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

    def refresh_theme(self):
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


class ListHeader(QWidget):
    """表の見出しの行(一覧の上に固定)。押すとその列で並べ、もう一度で向きを
    逆に(▴/▾)。並べられない一覧(プレイリスト)ではただの見出し。

    今までの SortBar と同じ口(mode・reverse・set_state・_pick・changed)。"""

    changed = Signal(str, bool)   # (mode, reverse)

    #: 列 → 並べ方。BPM は表に出さない(利用者の指定 2026-11-16)ので、見出しの
    #: 右端に静かな「BPM」だけ置く。
    SORT_OF = {"title": "title", "folder": "name", "course": "level", "date": "recent"}

    def __init__(self, cols, modes=(), parent=None):
        super().__init__(parent)
        self.setObjectName("listHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.cols = dict(cols)
        self.modes = tuple(modes)
        self.mode = self.modes[0] if self.modes else ""
        self.reverse = False
        self.buttons = {}
        h = QHBoxLayout(self)
        h.setContentsMargins(*ROW_MARGINS)
        h.setSpacing(ROW_SPACING)

        def head(key, text, width=None, align=Qt.AlignLeft):
            mode = self.SORT_OF.get(key)
            if mode in self.modes:
                b = QPushButton(text)
                b.setObjectName("colHead")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, m=mode: self._pick(m))
                b.setProperty("align", "right" if align == Qt.AlignRight else "left")
                self.buttons[mode] = (b, text)
                w = b
            else:
                w = QLabel(text)
                w.setObjectName("colLabel")
                w.setAlignment(align | Qt.AlignVCenter)
            if width:
                w.setFixedWidth(width)
            return w

        if self.cols.get("num"):
            h.addWidget(head("num", "#", COLW["num"], Qt.AlignRight))
        spacer = QWidget()
        spacer.setFixedWidth(COLW["thumb"])
        h.addWidget(spacer)
        h.addWidget(head("title", "曲名"), 1)
        h.addWidget(head("folder", "フォルダ", COLW["folder"]))
        h.addWidget(head("course", "難易度", COLW["course"]))
        if self.cols.get("date"):
            h.addWidget(head("date", "最近", COLW["date"], Qt.AlignRight))
        if self.cols.get("action"):
            sp = QWidget()
            sp.setFixedWidth(COLW["action"])
            h.addWidget(sp)
        tail = COLW["star"] + ROW_SPACING + COLW["mark"]
        if "bpm" in self.modes:
            b = QPushButton("BPM")
            b.setObjectName("colHead")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False: self._pick("bpm"))
            b.setFixedWidth(tail)
            self.buttons["bpm"] = (b, "BPM")
            h.addWidget(b)
        else:
            sp = QWidget()
            sp.setFixedWidth(tail)
            h.addWidget(sp)
        self.set_state(self.mode, False)

    def follow(self, *lists):
        """一覧(RowList)の縦の帯と右の余白ぶん、右を詰めて列をそろえる。"""
        self._lists = [l for l in lists if l is not None]
        for l in self._lists:
            l.installEventFilter(self)
            l.verticalScrollBar().rangeChanged.connect(lambda *_a: self._sync_margin())
        self._sync_margin()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            self._sync_margin()
        return False

    def _sync_margin(self):
        lists = [l for l in getattr(self, "_lists", []) if l.isVisible()] or             getattr(self, "_lists", [])
        if not lists:
            return
        l = lists[0]
        sb = l.verticalScrollBar()
        extra = sb.sizeHint().width() if sb.isVisible() else 0
        right = ROW_MARGINS[2] + l.widget().layout().contentsMargins().right() + extra
        m = self.layout().contentsMargins()
        if m.right() != right:
            self.layout().setContentsMargins(ROW_MARGINS[0], 0, right, 0)

    def set_state(self, mode, reverse):
        if self.modes and mode not in self.modes:
            mode = self.modes[0]
        self.mode, self.reverse = mode, bool(reverse)
        desc = song_lists.is_descending(mode, reverse)
        for m, (b, text) in self.buttons.items():
            on = m == mode
            b.setText(("%s %s" % (text, "▾" if desc else "▴")) if on else text)
            b.setProperty("on", on)
            b.setToolTip(song_lists.SORT_LABELS.get(m, ""))
            b.style().unpolish(b)
            b.style().polish(b)

    def _pick(self, mode):
        """押した列で並べる。いま並べている列ならもう一度で向きを逆に。"""
        if mode == self.mode:
            self.set_state(mode, not self.reverse)
        else:
            self.set_state(mode, False)
        self.changed.emit(self.mode, self.reverse)


#: プレイリストの中の並べ替えで運ぶもの(何番目か)。
PLAYLIST_MIME = "application/x-neotja-playlist-index"


class PlaylistSongRow(SongRow):
    """プレイリストの中の曲の1行。左に何番目か、右に保存したコースのマーク。
    押して離すと選ぶ(いつもの行と同じ)。押したまま動かすと並べ替え。"""

    #: 右クリック (何番目か, 画面の位置)。
    item_menu = Signal(int, object)
    #: 押して離した(選ぶ)。何番目か。
    item_clicked = Signal(int)

    def __init__(self, index, item, title, **kw):
        self.index = index
        self.course = item.get("course")
        super().__init__(item["path"], title, cols=list_columns(num=True), **kw)
        self.num.setText(str(index + 1))
        #: 保存したコースのマーク(難易度の欄)。
        self.course_icon = self.course_cell.icon
        self._show_course()
        self._press = None
        # 見つからない曲も、外す・並べ替えはできるように押せる形にしておく。
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press = ev.position().toPoint()
            if not self.missing:
                self.setFocus()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        from PySide6.QtWidgets import QApplication
        if (self._press is not None and ev.buttons() & Qt.LeftButton
                and (ev.position().toPoint() - self._press).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press = None
            self.start_drag()
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._press is not None:
            self._press = None
            if not self.missing:
                self.item_clicked.emit(self.index)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _show_course(self):
        """難易度の欄はプレイリストに保存したコース(★はその譜面のレベル)。"""
        if self.missing or getattr(self, "course_cell", None) is None:
            return
        levels = dict(self.info.get("courses") or [])
        course = getattr(self, "course", None)
        if course:
            self.course_cell.set_course(course, levels.get(course))
        else:
            super()._show_course()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not self.missing:
            self.item_clicked.emit(self.index)
            ev.accept()
            return
        super().keyPressEvent(ev)

    def contextMenuEvent(self, ev):
        self.item_menu.emit(self.index, ev.globalPos())
        ev.accept()

    def start_drag(self):
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag
        mime = QMimeData()
        mime.setData(PLAYLIST_MIME, str(self.index).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self.grab()
        if not pm.isNull():
            pm = pm.scaledToWidth(min(pm.width(), 560), Qt.SmoothTransformation)
            drag.setPixmap(pm)
            drag.setHotSpot(self._drag_hot(pm))
        drag.exec(Qt.MoveAction)

    @staticmethod
    def _drag_hot(pm):
        from PySide6.QtCore import QPoint
        return QPoint(24, pm.height() // 2)


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


class _DropCard(GlassCard):
    """「TJAを開く」の板。TJA を Player の上へ引きずっているあいだは
    縁をアクセント色で太くし、ここへ落とせることを見せる。"""

    drop_hot = False

    def set_drop_hot(self, on):
        on = bool(on)
        if on != self.drop_hot:
            self.drop_hot = on
            self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.drop_hot:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setPen(QPen(QColor(COLORS["accent"]), 3))
        p.setBrush(_c(COLORS["accent"], 0.10))
        p.drawRoundedRect(rect, 16, 16)
        p.end()


class _GlassDialog(QDialog):
    """選曲で出す小さな窓の見た目(縁のない平らな板)。"""

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


class ConfirmDialog(_GlassDialog):
    """見出しと「キャンセル/実行」だけの確かめの窓(CategoryDialog と同じ見た目)。"""

    def __init__(self, heading, ok_text, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmDialog")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(400)
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 26, 30, 24)
        v.setSpacing(0)
        head = QLabel(heading)
        head.setObjectName("dlgHead")
        v.addWidget(head)
        v.addSpacing(26)
        bh = QHBoxLayout()
        bh.setSpacing(10)
        bh.addStretch(1)
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self.reject)
        bh.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setObjectName("primary")
        self.btn_ok.clicked.connect(self.accept)
        bh.addWidget(self.btn_ok)
        v.addLayout(bh)
        from neotja.start_screen import StartScreen
        self.setStyleSheet(StartScreen._qss() + library_qss())
        # 消すほうへ Enter 一つで進まないよう、最初はキャンセルに置く。
        self.btn_cancel.setDefault(True)
        self.btn_cancel.setFocus()


class CategoryDialog(_GlassDialog):
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

    def __init__(self, parent=None, spacing=0):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("rowListInner")
        self._v = QVBoxLayout(inner)
        self._v.setContentsMargins(0, 0, 8, 0)
        # 曲の表は行どうしを詰める(区切りは行の下の細い線)。カードを並べる
        # 一覧(プレイリスト)は間を空ける。
        self._v.setSpacing(spacing)
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


class PlaylistRowList(RowList):
    """プレイリストの曲の一覧。行を引きずって落とすと並べ替え(moved)。"""

    moved = Signal(int, int)   # (元の番目, 新しい番目)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._line = QFrame(self.widget())
        self._line.setObjectName("dropLine")
        self._line.setFixedHeight(3)
        self._line.hide()

    def _target(self, pos):
        """viewport の位置 → 落とす先(元の並びでの「この前」の番目)。"""
        y = pos.y() + self.verticalScrollBar().value()
        rows = [r for r in self._rows if r.isVisible()]
        for r in rows:
            g = r.geometry()
            if y < g.center().y():
                return r.index, g.top() - 5
        if rows:
            last = rows[-1]
            return last.index + 1, last.geometry().bottom() + 3
        return 0, 0

    def _accepts(self, ev):
        return ev.mimeData().hasFormat(PLAYLIST_MIME)

    def dragEnterEvent(self, ev):
        if self._accepts(ev):
            ev.acceptProposedAction()
            return
        super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if not self._accepts(ev):
            super().dragMoveEvent(ev)
            return
        ev.acceptProposedAction()
        _i, y = self._target(ev.position().toPoint())
        inner = self.widget()
        self._line.setGeometry(0, int(y), inner.width() - 8, 3)
        self._line.show()
        self._line.raise_()
        # 縁に近ければ送る。
        vp = self.viewport().height()
        py = ev.position().y()
        sb = self.verticalScrollBar()
        if py < 30:
            sb.setValue(sb.value() - 18)
        elif py > vp - 30:
            sb.setValue(sb.value() + 18)

    def dragLeaveEvent(self, ev):
        self._line.hide()
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev):
        self._line.hide()
        if not self._accepts(ev):
            super().dropEvent(ev)
            return
        try:
            src = int(bytes(ev.mimeData().data(PLAYLIST_MIME)).decode("ascii"))
        except ValueError:
            return
        before, _y = self._target(ev.position().toPoint())
        dst = before - 1 if before > src else before
        ev.acceptProposedAction()
        if dst != src:
            self.moved.emit(src, dst)


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
        #: ジャンル(box.def)の色。あれば文字の左に小さな丸を出す。
        self.dot_color = None

    def set_dot_color(self, color):
        self.dot_color = QColor(color) if color else None
        self.setProperty("dot", self.dot_color is not None)
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self.dot_color is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(self.dot_color)
            p.drawEllipse(QPointF(34.0, self.height() / 2.0), 4.0, 4.0)
            p.end()
        # 選んでいるものは塗りだけで示す(横の線は描かない)。


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


class CategoryHead(QLabel):
    """左の列のシミュレーター(カテゴリ)の見出し。押すとその下のジャンルを
    畳む・開く。右端に小さな ▸/▾。"""

    toggled = Signal(str, bool)   # (名前, 畳んだか)

    def __init__(self, name, collapsed=False, parent=None):
        super().__init__(name, parent)
        self.name = name
        self.setObjectName("category")
        self.setCursor(Qt.PointingHandCursor)
        self.collapsed = bool(collapsed)
        #: この見出しの下の項目(NavButton)。
        self.navs = []

    def set_collapsed(self, on):
        self.collapsed = bool(on)
        for b in self.navs:
            b.setVisible(not self.collapsed)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.set_collapsed(not self.collapsed)
            self.toggled.emit(self.name, self.collapsed)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["fg_dim"]))
        cx, cy, r = self.width() - 12.0, self.height() / 2.0, 4.5
        path = QPainterPath()
        if self.collapsed:          # ▸
            path.moveTo(cx - r * 0.6, cy - r)
            path.lineTo(cx + r * 0.8, cy)
            path.lineTo(cx - r * 0.6, cy + r)
        else:                       # ▾
            path.moveTo(cx - r, cy - r * 0.6)
            path.lineTo(cx + r, cy - r * 0.6)
            path.lineTo(cx, cy + r * 0.8)
        path.closeSubpath()
        p.drawPath(path)
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
    """パンくず + [題・検索 …… 右端に静かな道具] + 表(見出しの行 + 一覧)。"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(40, 18, 40, 20)
        self.v.setSpacing(0)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        self.crumbs.addStretch(1)
        self.v.addLayout(self.crumbs)
        self.v.addSpacing(4)
        self.tools = QHBoxLayout()
        self.tools.setContentsMargins(0, 0, 8, 0)
        self.tools.setSpacing(10)
        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("pageTitle")
        self.tools.addWidget(self.lbl_title)
        self.tools.addSpacing(18)
        self.v.addLayout(self.tools)
        self.header = None

    def add_search(self):
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("曲名で検索")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(360)
        self.tools.addWidget(self.search)
        self.tools.addStretch(1)
        return self.search

    def add_sort(self, modes, cols):
        """並べ方は表の見出しの行で(押した列で並べる)。"""
        self.sort = ListHeader(cols, modes)
        self.header = self.sort
        return self.sort

    def add_table(self, body, cols=None):
        """見出しの行(add_sort で作ったもの、無ければ並べない見出し)と body を、
        幅の上限つきで題と左をそろえて置く。"""
        if self.header is None and cols is not None:
            self.header = ListHeader(cols)
        self.v.addSpacing(14)
        box = QWidget()
        box.setObjectName("tableBox")
        box.setMaximumWidth(TABLE_MAX_W)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)
        if self.header is not None:
            bv.addWidget(self.header)
            if isinstance(body, QStackedWidget):
                self.header.follow(*[body.widget(i) for i in range(body.count())])
            else:
                self.header.follow(body)
        bv.addWidget(body, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(box, 1)
        row.addStretch(0)
        self.v.addLayout(row, 1)
        return body

    def add_list(self, cols=None, spacing=0):
        self.list = RowList(spacing=spacing)
        if cols is None and self.header is None:
            self.v.addSpacing(16)
            self.v.addWidget(self.list, 1)
        else:
            self.add_table(self.list, cols)
        if hasattr(self, "search"):
            self.search.textChanged.connect(self.list.apply_filter)
        return self.list


#: 一覧ごとの列。最近は日付と再指定、保存済みは外す、プレイリストは番号。
RECENT_COLS = list_columns(date=True, action=True)
SAVED_COLS = list_columns(action=True)
FOLDER_COLS = list_columns()
PLAYLIST_COLS = list_columns(num=True)


# ----------------------------------------------------------------------
class LibraryView(QWidget):
    """選曲。左の列と右の中身。"""

    song_chosen = Signal(str)
    #: 曲の行を1回押した(選んだだけ。試聴を始める合図)。もう一度押すと
    #: song_chosen(難易度選択へ)。
    song_selected = Signal(str)
    #: 「TJAを開く」でファイルを選んだ(開いた曲の画面へ)。
    file_opened = Signal(str)
    #: 操作音の合図("pi")。鳴らすのは PlayerHost(ui_sound)。
    sound_requested = Signal(str)
    #: 右に出す一覧を替えた(カテゴリ・ジャンル・フォルダの段・検索の結果)。
    #: 試聴を止める合図(PlayerHost)。
    list_switched = Signal()
    #: 選んでいる行の右の印を押した (曲, 流すか)。
    preview_toggled = Signal(str, bool)
    #: 開いているプレイリストの「連続再生」の「開始」(プレイリストの id,
    #: {"start", "end", "auto", "wait"})。
    playlist_play_requested = Signal(str, object)

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
        sv.setContentsMargins(14, 20, 14, 22)
        sv.setSpacing(2)
        # 上の4つは見出しなしの1つのまとまり(選んだものは塗り)。その下に細い線、
        # 続いてシミュレーターごとの小さな見出しと登録フォルダ。
        self.nav_open = NavButton("TJAを開く", "open")
        self.nav_recent = NavButton("最近開いた楽曲", "recent")
        self.nav_saved = NavButton("保存済み楽曲", "saved")
        self.nav_playlists = NavButton("プレイリスト", "playlists")
        for b in (self.nav_open, self.nav_recent, self.nav_saved, self.nav_playlists):
            b.setProperty("top", True)
            b.clicked.connect(lambda _=False, k=b.kind: self.show_section(k))
            b.clicked.connect(self._pi)
            sv.addWidget(b)
        sv.addSpacing(16)
        self.side_rule = QFrame()
        self.side_rule.setObjectName("sideRule")
        self.side_rule.setFixedHeight(1)
        sv.addWidget(self.side_rule)
        sv.addSpacing(14)
        # シミュレーター(カテゴリ)ごとに見出しを立て、その下に登録フォルダ。
        self.folder_box = QVBoxLayout()
        self.folder_box.setSpacing(4)
        sv.addLayout(self.folder_box)
        self.folder_navs = []
        self.category_labels = []
        #: 登録フォルダごとの左の列の並び(song_lists.sidebar_entries)。
        #: 足す・外すときに読み直す。
        self._sidebar_cache = {}
        #: ジャンルを左の列に出している登録フォルダを「その他」で開いたとき、
        #: その一段ではジャンルの行を出さない(左に並んでいるので)。
        self._hide_genres_in = ""
        sv.addSpacing(6)
        self.btn_add_folder = QPushButton("＋  フォルダを追加")
        self.btn_add_folder.setObjectName("addFolder")
        self.btn_add_folder.setCursor(Qt.PointingHandCursor)
        self.btn_add_folder.clicked.connect(self._pi)
        self.btn_add_folder.clicked.connect(self.add_folder)
        sv.addWidget(self.btn_add_folder)
        sv.addStretch(1)
        h.addWidget(side)

        # --- 右 ----------------------------------------------------------
        right = QWidget()
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(0)
        #: 「曲を追加」のあいだだけ出す帯(追加先の題と「完了」)。
        self.add_bar = AddModeBar()
        self.add_bar.done.connect(self._pi)
        self.add_bar.done.connect(self.end_add_mode)
        self.add_bar.hide()
        right_v.addWidget(self.add_bar)
        #: プレイリストを録画しているあいだの帯。
        from neotja.player.playlist_view import RecordBar
        self.record_bar = RecordBar()
        self.record_bar.cancel_requested.connect(self._pi)
        self.record_bar.cancel_requested.connect(self.cancel_playlist_record)
        self.record_bar.open_requested.connect(self._pi)
        self.record_bar.open_requested.connect(self._open_record_folder)
        self.record_bar.close_requested.connect(self._pi)
        self.record_bar.close_requested.connect(self.record_bar.hide)
        self.record_bar.hide()
        right_v.addWidget(self.record_bar)
        #: 録画の係(PlaylistRecorder)と、終わったときの結果。
        self._plrec = None
        self._plrec_result = None
        #: 書き出したフォルダを開く口(試験はエクスプローラーを出さない)。
        self.open_folder = None
        self.pages = QStackedWidget()
        right_v.addWidget(self.pages, 1)
        h.addWidget(right, 1)

        self.page_open = _Page("TJAを開く")
        card = _DropCard(radius=16, accent=True)
        card.setObjectName("openCard")
        self.open_card = card
        #: TJA を引きずっているあいだ、元に戻す画面 (ページ, 左の項目)。
        self._drop_restore = None
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
        self.page_recent.add_sort(song_lists.SORT_MODES, RECENT_COLS)
        self.page_recent.sort.changed.connect(
            lambda m, r: self._on_sort_changed(True, m, r))
        # 「履歴を消す」は右の端に小さく(並べ方の見出しから離して)。
        self.btn_clear_recent = QPushButton("履歴を消す")
        self.btn_clear_recent.setObjectName("quiet")
        self.btn_clear_recent.clicked.connect(self._pi)
        self.btn_clear_recent.clicked.connect(self.ask_clear_recent)
        self.page_recent.tools.addWidget(self.btn_clear_recent)
        self._clear_dialog = None
        self.recent_list = self.page_recent.add_list()
        self.pages.addWidget(self.page_recent)

        self.page_saved = _Page("保存済み楽曲")
        self.page_saved.add_search()
        self.page_saved.add_sort(song_lists.SORT_MODES[1:], SAVED_COLS)
        self.page_saved.sort.changed.connect(
            lambda m, r: self._on_sort_changed(False, m, r))
        self.btn_add_saved = QPushButton("楽曲を追加")
        self.btn_add_saved.setObjectName("quiet")
        self.btn_add_saved.clicked.connect(self._pi)
        self.btn_add_saved.clicked.connect(self.add_saved_files)
        self.page_saved.tools.addWidget(self.btn_add_saved)
        self.saved_list = self.page_saved.add_list()
        self.pages.addWidget(self.page_saved)

        # プレイリストの一覧と、開いた1つの曲の一覧。
        #: プレイリストの保存(最初に開いたときに読む)。
        self._playlist_store = None
        #: 開いているプレイリストの id。
        self._playlist_id = ""
        #: 作成/編集の窓と削除の確かめの差し替え口(試験は窓を出さない)。
        #: playlist_editor(pl または None) → (題, 説明, 絵, 絵を外すか) / None。
        #: playlist_confirm(見出し, 実行の字, はいのときの呼び出し)。
        self.playlist_editor = None
        self.playlist_confirm = None
        self.page_playlists = _Page("プレイリスト")
        self.btn_new_playlist = QPushButton("＋  プレイリストを作成")
        self.btn_new_playlist.setObjectName("flat")
        self.btn_new_playlist.setCursor(Qt.PointingHandCursor)
        self.btn_new_playlist.clicked.connect(self._pi)
        self.btn_new_playlist.clicked.connect(self.create_playlist)
        self.page_playlists.tools.addWidget(self.btn_new_playlist)
        self.page_playlists.tools.addStretch(1)
        self.playlists_list = self.page_playlists.add_list(spacing=8)
        self.pages.addWidget(self.page_playlists)

        self.page_playlist = _Page("")
        self.page_playlist.add_search()
        self.btn_add_songs = QPushButton("＋  曲を追加")
        self.btn_add_songs.setObjectName("flat")
        self.btn_add_songs.setCursor(Qt.PointingHandCursor)
        self.btn_add_songs.clicked.connect(self._pi)
        self.btn_add_songs.clicked.connect(lambda _=False: self.begin_add_mode())
        self.page_playlist.tools.addWidget(self.btn_add_songs)
        self.btn_play_all = QPushButton("▶  連続再生")
        self.btn_play_all.setObjectName("flat")
        self.btn_play_all.setCursor(Qt.PointingHandCursor)
        self.btn_play_all.clicked.connect(self._pi)
        self.btn_play_all.clicked.connect(lambda _=False: self.open_play_panel())
        #: 「連続再生」の板(押したときに作る)。
        self.play_panel = None
        self.page_playlist.tools.addWidget(self.btn_play_all)
        self.playlist_song_list = PlaylistRowList()
        self.playlist_song_list.moved.connect(self.move_playlist_item)
        self.page_playlist.list = self.playlist_song_list
        self.page_playlist.add_table(self.playlist_song_list, PLAYLIST_COLS)
        self.page_playlist.search.textChanged.connect(self.playlist_song_list.apply_filter)
        self.pages.addWidget(self.page_playlist)
        #: 「曲を追加」のあいだの追加先(プレイリストの id)。空なら普段どおり。
        self._add_target = ""
        #: プレイリストの中で選んでいる行(何番目か)と、そのコース。
        #: 同じ曲が2回入っていても、押した行だけを選ぶ。
        self._chosen_index = -1
        self._chosen_course = None
        #: コース選びの差し替え口(試験は窓を出さない)。
        #: course_picker(題, [(コース, レベル)], 今のコース) → コース / None。
        self.course_picker = None

        self.page_folder = _Page("")
        self.page_folder.add_search()
        self.page_folder.add_sort(song_lists.SORT_MODES[1:], FOLDER_COLS)
        self.page_folder.sort.changed.connect(
            lambda m, r: self._on_sort_changed(False, m, r))
        # ふつうは一段ぶんの一覧。検索欄に何か入っているあいだは、下の
        # フォルダ全部から探した曲だけの一覧に差し替える。
        self.folder_stack = QStackedWidget()
        self.folder_list = RowList()
        self.search_list = RowList()
        self.folder_stack.addWidget(self.folder_list)
        self.folder_stack.addWidget(self.search_list)
        self.page_folder.list = self.folder_list
        self.page_folder.add_table(self.folder_stack)
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
        return ([self.nav_open, self.nav_recent, self.nav_saved, self.nav_playlists]
                + self.folder_navs)

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

    def _list_key(self):
        """いま右に出している一覧を表すもの(替わったかを見る)。"""
        page = self.pages.currentWidget()
        if page is self.page_folder:
            q = self.page_folder.search.text().strip()
            return (page, song_lists.path_key(self._folder_dir) if self._folder_dir else "", q)
        if page is self.page_playlist:
            return (page, self._playlist_id)
        return (page,)

    def _note_switch(self, before):
        if self._list_key() != before:
            self.list_switched.emit()

    def show_section(self, kind, folder=None):
        before = self._list_key()
        self._show_section(kind, folder)
        self._note_switch(before)

    def _show_section(self, kind, folder=None):
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
        elif kind == "playlists":
            self._leave_add_mode()
            self._check_nav(self.nav_playlists)
            self._fill_playlists()
            self.pages.setCurrentWidget(self.page_playlists)
        elif kind == "playlist" and folder:
            self._leave_add_mode()
            self._check_nav(self.nav_playlists)
            self._open_playlist(folder)
        elif kind == "folder" and folder:
            nav = next((b for b in self.folder_navs
                        if song_lists.path_key(b.path) == song_lists.path_key(folder)), None)
            self._check_nav(nav)
            # パンくずは開いた所(ジャンルならジャンル)から始める。
            self._folder_root = folder
            self._hide_genres_in = (song_lists.path_key(folder)
                                    if getattr(nav, "entry", "") == "other" else "")
            self._open_dir(folder)
            self.pages.setCurrentWidget(self.page_folder)

    # ------------------------------------------------------------------
    # 選ぶ
    #: 選んだ直後の2回目を無視する間合い(秒)。ダブルクリックや連打で、
    #: 試聴を聞く前に難易度選択へ飛ばないように。
    SELECT_DEBOUNCE_SEC = 0.15
    _last_select = 0.0

    def click_song(self, path):
        """曲の行を押した(Enter も同じ)。選んでいない曲なら選ぶだけ
        (song_selected。試聴が始まる)。選んでいる曲をもう一度押したら
        難易度選択へ(choose)。"""
        import time
        if not path or not os.path.isfile(path):
            return
        if self._add_target:
            # 「曲を追加」のあいだは、押した曲をプレイリストへ(試聴・難易度選択はしない)。
            self.add_song_to_playlist(self._add_target, path)
            return
        now = time.monotonic()
        same = (bool(self._chosen) and self._chosen_index < 0
                and song_lists.path_key(path) == song_lists.path_key(self._chosen))
        self._chosen_index = -1
        self._chosen_course = None
        if same:
            if now - self._last_select < self.SELECT_DEBOUNCE_SEC:
                return
            self.choose(path)
            return
        self._chosen = path
        self._last_select = now
        self._preview_playing = True
        self._mark_chosen()
        self.song_selected.emit(path)

    #: 選んでいる曲の試聴を流しているか(行の右の印)。
    _preview_playing = True

    def preview_playing(self):
        return bool(self._chosen) and self._preview_playing

    def set_preview_playing(self, on):
        """試聴を流している/止めたを印へ(PlayerHost から)。"""
        on = bool(on)
        if on != self._preview_playing:
            self._preview_playing = on
            self._mark_chosen()

    def toggle_preview(self, path=None):
        """選んでいる行の右の印を押した。止める ⇔ 流し直す。"""
        if not self._chosen:
            return
        if path and song_lists.path_key(path) != song_lists.path_key(self._chosen):
            return
        self._preview_playing = not self._preview_playing
        self._mark_chosen()
        self.preview_toggled.emit(self._chosen, self._preview_playing)

    def selected_path(self):
        return self._chosen

    def choose(self, path):
        """曲を選んだ(難易度選択へ)。選んだだけでは「最近開いた楽曲」に
        入れない — 見て回るだけで一覧が埋まってしまうため。入れるのは
        実際に演奏・録画を始めたとき(note_played)。"""
        if not path or not os.path.isfile(path):
            return
        self._chosen = path
        self._mark_chosen()
        self.song_chosen.emit(path)

    def chosen_course(self):
        """選んでいる行がプレイリストの曲なら、保存したコース(難易度選択で
        最初に選んでおく)。それ以外は None。"""
        return self._chosen_course if self._chosen_index >= 0 else None

    def _mark_chosen(self):
        key = song_lists.path_key(self._chosen) if self._chosen else None
        for lst in self._song_lists():
            for r in lst.rows():
                if not isinstance(r, SongRow):
                    continue
                on = key is not None and song_lists.path_key(r.path) == key
                if isinstance(r, PlaylistSongRow) and self._chosen_index >= 0:
                    on = on and r.index == self._chosen_index
                r.set_chosen(on, self._preview_playing)

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
        if path and self._add_target:
            self.add_song_to_playlist(self._add_target, path)
        elif path:
            self.file_opened.emit(path)

    def mark_chosen(self, path):
        """path を選んでいる曲にする(難易度選択へは進めない)。"""
        if path:
            self._chosen = path
            self._mark_chosen()

    # ------------------------------------------------------------------
    # 一覧を作る
    def _song_row(self, path, title, **kw):
        row = SongRow(path, title, **kw)
        row.chosen.connect(self._pi)
        row.chosen.connect(self.click_song)
        row.mark_clicked.connect(self._pi)
        row.mark_clicked.connect(self.toggle_preview)
        row.save_toggled.connect(self._pi)
        row.save_toggled.connect(self._on_save_toggled)
        row.remove_requested.connect(self.remove_saved)
        row.relink_requested.connect(self.relink_saved)
        row.menu_requested.connect(self._song_menu)
        if self._chosen and song_lists.path_key(path) == song_lists.path_key(self._chosen):
            row.set_chosen(True, self._preview_playing)
        return row

    def _start_worker(self, paths=None, directory=None):
        detach_worker(self._worker)
        self._worker = None
        w = InfoWorker(paths=paths, directory=directory, parent=self,
                       art_cfg=thumbs.art_snapshot(self.cfg),
                       known=thumbs.CACHE.keys())
        # 受け手は必ず self の結び付いたメソッドにする。ラムダへ繋ぐと受け手の
        # スレッドが分からず、ワーカーのスレッドでそのまま呼ばれて部品を作って
        # しまう(実際に行が欠けた)。メソッドなら GUI スレッドへ回される。
        w.infos.connect(self._on_infos)
        w.thumbs.connect(self._on_thumbs)
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
                                     saved=song_lists.is_saved(self.cfg, path),
                                     cols=FOLDER_COLS)
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

    def _song_rows_by_key(self):
        out = {}
        for lst in self._song_lists():
            for row in lst.rows():
                if isinstance(row, SongRow):
                    out.setdefault(song_lists.path_key(row.path), []).append(row)
        return out

    def _on_thumbs(self, worker, items):
        """ワーカーが読んだジャケットを行へ(QPixmap にするのはここ)。"""
        if worker is not self._worker and worker is not getattr(self, "_search_worker", None):
            return
        rows = self._song_rows_by_key()
        for path, key, image in items:
            if key is None:
                pm = None
            elif image is not None:
                pm = thumbs.CACHE.put(key, image)
            else:
                pm = thumbs.CACHE.get(key)
                if pm is None:
                    # 送る前は覚えていたが、そのあいだに捨てられた。読み直す。
                    pm = self._load_thumb_now(path)
            for row in rows.get(song_lists.path_key(path), ()):
                row.set_thumb(pm)

    def _load_thumb_now(self, path):
        key, image = thumbs.thumb_for_song(self.cfg, path)
        if key is None:
            return None
        return thumbs.CACHE.put(key, image) if image is not None else thumbs.CACHE.get(key)

    def refresh_thumb(self, path):
        """1曲の行のジャケットを今の設定で出し直す(背景・ジャケットを
        指定したあと)。"""
        pm = self._load_thumb_now(path)
        for row in self._song_rows_by_key().get(song_lists.path_key(path), ()):
            row.set_thumb(pm)

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
                                 date_text=_when(e.get("opened")), show_path=True,
                                 cols=RECENT_COLS)
            self.recent_list.add(row)
            if row.missing:
                row.set_missing_chip()
            else:
                self._pending_rows.setdefault(song_lists.path_key(e["path"]), []).append(row)
                paths.append(e["path"])
        self.recent_list.apply_filter(self.page_recent.search.text())
        self.btn_clear_recent.setEnabled(bool(song_lists.recent(self.cfg)))
        self._resort()
        if paths:
            self._start_worker(paths=paths)

    # ------------------------------------------------------------------
    # TJA を引きずっているあいだ(PlayerHost の dragEnter/dragLeave/drop)
    def begin_drop_hint(self):
        """「TJAを開く」を出して板を光らせる。離れたら end_drop_hint で戻す。"""
        if self._drop_restore is None:
            nav = next((b for b in self._all_navs() if b.isChecked()), None)
            self._drop_restore = (self.pages.currentWidget(), nav)
        self._check_nav(self.nav_open)
        self.pages.setCurrentWidget(self.page_open)
        self.open_card.set_drop_hot(True)

    def end_drop_hint(self):
        """引きずるのをやめた・落とした。元の画面へ戻す(一覧は作り直さない)。"""
        self.open_card.set_drop_hot(False)
        restore, self._drop_restore = self._drop_restore, None
        if restore is None:
            return
        page, nav = restore
        try:
            self.pages.setCurrentWidget(page)
            self._check_nav(nav)
        except RuntimeError:
            pass

    def drop_hint_active(self):
        return self._drop_restore is not None

    def ask_clear_recent(self):
        """「履歴を消す」。確かめの窓を出し(閉じるのを待たない)、
        「消す」なら最近開いた楽曲を空にする。元の TJA には触らない。"""
        if self._clear_dialog is not None or not song_lists.recent(self.cfg):
            return
        dlg = ConfirmDialog("履歴を消す", "消す", self)
        self._clear_dialog = dlg
        dlg.accepted.connect(self.clear_recent)
        dlg.finished.connect(self._on_clear_dialog_closed)
        dlg.open()

    def _on_clear_dialog_closed(self, *_a):
        dlg, self._clear_dialog = self._clear_dialog, None
        if dlg is not None:
            dlg.deleteLater()
        self._pi()

    def clear_recent(self):
        song_lists.clear_recent(self.cfg)
        self._save()
        self._recent_stale = False
        if self.pages.currentWidget() is self.page_recent:
            self._fill_recent()

    def _fill_saved(self):
        self.saved_list.clear()
        self._pending_rows = {}
        paths = []
        for e in song_lists.saved(self.cfg):
            row = self._song_row(e["path"], e.get("title") or os.path.basename(e["path"]),
                                 removable=True, show_path=True, cols=SAVED_COLS)
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
    # プレイリスト
    def playlist_store(self):
        if self._playlist_store is None:
            self._playlist_store = playlists.PlaylistStore()
        return self._playlist_store

    def _fill_playlists(self):
        self.playlists_list.clear()
        for pl in self.playlist_store().items:
            row = PlaylistRow(pl)
            row.jacket.set_pixmap(playlist_jacket_pixmap(pl, self.cfg))
            row.opened.connect(self._pi)
            row.opened.connect(self.open_playlist)
            row.menu_requested.connect(self._playlist_menu)
            self.playlists_list.add(row)

    def open_playlist(self, pid):
        """プレイリストを開く(いつものジャンルと同じ曲の一覧)。"""
        self.show_section("playlist", pid)

    def _open_playlist(self, pid):
        pl = self.playlist_store().get(pid)
        if pl is None:
            self._fill_playlists()
            self.pages.setCurrentWidget(self.page_playlists)
            return
        if pid != self._playlist_id:
            self._chosen_index = -1
            self._chosen_course = None
        self._playlist_id = pid
        page = self.page_playlist
        page.lbl_title.setText(pl["title"])
        self._build_playlist_crumbs(pl)
        page.search.blockSignals(True)
        page.search.clear()
        page.search.blockSignals(False)
        self._fill_playlist_songs(pl)
        self.pages.setCurrentWidget(page)

    def _fill_playlist_songs(self, pl):
        """開いているプレイリストの曲の行を作り直す(並びはプレイリストの順)。"""
        lst = self.playlist_song_list
        keep = lst.verticalScrollBar().value()
        lst.clear()
        self._pending_rows = {}
        paths = []
        for i, item in enumerate(pl["items"]):
            path = item["path"]
            row = PlaylistSongRow(i, item, os.path.splitext(os.path.basename(path))[0],
                                  saved=song_lists.is_saved(self.cfg, path))
            row.item_clicked.connect(self._pi)
            row.item_clicked.connect(self.click_playlist_item)
            row.item_menu.connect(self._playlist_item_menu)
            row.mark_clicked.connect(self._pi)
            row.mark_clicked.connect(self.toggle_preview)
            row.save_toggled.connect(self._pi)
            row.save_toggled.connect(self._on_save_toggled)
            lst.add(row)
            if row.missing:
                row.set_missing_chip()
            else:
                key = song_lists.path_key(path)
                if key not in self._pending_rows:
                    paths.append(path)       # 同じ曲は1回だけ読む
                self._pending_rows.setdefault(key, []).append(row)
        lst.apply_filter(self.page_playlist.search.text())
        self.btn_play_all.setEnabled(bool(paths))
        self._mark_chosen()
        if paths:
            self._start_worker(paths=paths)
        def restore():
            try:
                lst.verticalScrollBar().setValue(keep)
            except RuntimeError:
                pass             # もう閉じた
        QTimer.singleShot(0, restore)

    #: 自動の待ち時間(秒)の既定と幅(PlayerHost と同じ)。
    PLAYLIST_WAIT_DEFAULT = 10
    PLAYLIST_WAIT_RANGE = (3, 60)

    def open_play_panel(self):
        """「連続再生」: 範囲・手動/自動・待ち時間の板を出す。"""
        from neotja.player.playlist_view import PlaylistPlayPanel
        pl = self._current_playlist()
        if pl is None or not pl["items"]:
            return None
        if self.play_panel is not None:
            self.play_panel.setParent(None)
            self.play_panel.deleteLater()
        panel = PlaylistPlayPanel(
            len(pl["items"]), auto=bool(self.cfg.get("player_playlist_auto", False)),
            wait=self.cfg.get("player_playlist_wait", self.PLAYLIST_WAIT_DEFAULT),
            wait_range=self.PLAYLIST_WAIT_RANGE, parent=self,
            rec_mode=self.cfg.get("player_playlist_rec_mode", "both"),
            rec_quality=self.cfg.get("player_playlist_rec_quality"))
        panel.set_record_enabled(not self.playlist_recording())
        pid = pl["id"]
        panel.started.connect(lambda opts: self._on_play_panel_start(pid, opts))
        panel.record_requested.connect(lambda opts: self.start_playlist_record(pid, opts))
        panel.closed.connect(self._pi)
        self.play_panel = panel
        panel.open_at(self.btn_play_all)
        return panel

    def _on_play_panel_start(self, pid, opts):
        # 手動/自動と待ち時間は次も同じに(設定に残す)。
        self.cfg["player_playlist_auto"] = bool(opts.get("auto"))
        self.cfg["player_playlist_wait"] = int(opts.get("wait") or self.PLAYLIST_WAIT_DEFAULT)
        self._save()
        self.playlist_play_requested.emit(pid, dict(opts))

    # --- 録画 ---------------------------------------------------------------
    def playlist_recording(self):
        return self._plrec is not None and self._plrec.is_running()

    def start_playlist_record(self, pid, opts, out_dir=None, quality=None, card_sec=None):
        """範囲の曲を録画する(まとめて / 曲ごと / 両方)。置き場所は
        ユーザーデータ/録画/<プレイリストの題>。始めたら PlaylistRecorder。"""
        from neotja.dialogs.record_dialog import QUALITY_PRESETS
        from neotja.player import playlist_record
        if self.playlist_recording():
            return None
        pl = self.playlist_store().get(pid)
        if pl is None:
            return None
        start = max(0, int(opts.get("start", 0)))
        end = opts.get("end")
        stop = len(pl["items"]) if end is None else int(end) + 1
        items = [dict(i) for i in pl["items"][start:stop]]
        if not any(os.path.isfile(i["path"]) for i in items):
            return None
        mode = opts.get("mode", "both")
        qi = opts.get("quality")
        try:
            qi = max(0, min(len(QUALITY_PRESETS) - 1, int(qi)))
        except (TypeError, ValueError):
            qi = None
        self.cfg["player_playlist_rec_mode"] = mode
        if qi is not None:
            self.cfg["player_playlist_rec_quality"] = qi
        self._save()
        if quality is None and qi is not None:
            quality = QUALITY_PRESETS[qi][1]
        kw = {}
        if card_sec is not None:
            kw["card_sec"] = card_sec
        rec = playlist_record.PlaylistRecorder(
            self.cfg, items, out_dir or playlist_record.playlist_out_dir(pl["title"]),
            mode=mode, quality=quality, title=pl["title"], parent=self, **kw)
        rec.progress.connect(self.record_bar.set_progress)
        rec.finished.connect(self._on_playlist_record_done)
        self._plrec = rec
        self._plrec_result = None
        self.record_bar.set_running(pl["title"])
        self.record_bar.show()
        if self.play_panel is not None:
            self.play_panel.set_record_enabled(False)
        rec.start()
        return rec

    def cancel_playlist_record(self):
        if self._plrec is not None:
            self._plrec.cancel()

    def _on_playlist_record_done(self, result):
        self._plrec_result = result
        ok = len(result.get("songs") or []) + (1 if result.get("combined") else 0)
        self.record_bar.set_done(ok, len(result.get("failed") or []),
                                 bool(result.get("cancelled")))
        if self.play_panel is not None:
            try:
                self.play_panel.set_record_enabled(True)
            except RuntimeError:
                pass

    def _open_record_folder(self):
        res = self._plrec_result or {}
        d = res.get("out_dir") or ""
        if not d or not os.path.isdir(d):
            return
        if self.open_folder is not None:
            self.open_folder(d)
            return
        try:
            os.startfile(d)          # noqa: S606
        except Exception:  # noqa: BLE001
            pass

    def _current_playlist(self):
        return self.playlist_store().get(self._playlist_id) if self._playlist_id else None

    def click_playlist_item(self, index):
        """プレイリストの曲の行を押した。いつもの行と同じく、1回目は選んで
        試聴、選んでいる行をもう一度押すと難易度選択(保存したコースを選んで)。"""
        import time
        pl = self._current_playlist()
        if pl is None or not 0 <= index < len(pl["items"]):
            return
        item = pl["items"][index]
        path = item["path"]
        if not os.path.isfile(path):
            return
        now = time.monotonic()
        same = (self._chosen_index == index and bool(self._chosen)
                and song_lists.path_key(path) == song_lists.path_key(self._chosen))
        self._chosen_course = item.get("course")
        if same:
            if now - self._last_select < self.SELECT_DEBOUNCE_SEC:
                return
            self.choose(path)
            return
        self._chosen = path
        self._chosen_index = index
        self._last_select = now
        self._preview_playing = True
        self._mark_chosen()
        self.song_selected.emit(path)

    # --- 曲を入れる・外す・並べ替える -----------------------------------
    def _pick_course(self, title, courses, current=None):
        """コースを選んでもらう。1つしか無ければ聞かない。やめたら None。"""
        courses = list(courses or [])
        if not courses:
            return None
        if len(courses) == 1:
            return courses[0][0]
        if self.course_picker is not None:
            return self.course_picker(title, courses, current)
        dlg = CoursePicker(title, courses, current, parent=self.window())
        try:
            return dlg.course if dlg.exec() == QDialog.Accepted else None
        finally:
            dlg.deleteLater()

    def add_song_to_playlist(self, pid, path):
        """曲をプレイリストの最後へ(コースを選んでから)。入れたら True。"""
        if not path or not os.path.isfile(path):
            return False
        store = self.playlist_store()
        if store.get(pid) is None:
            return False
        info = song_lists.song_info(path)
        courses = info.get("courses") or []
        course = self._pick_course(info.get("title", ""), courses,
                                   playlists.default_course(courses))
        if course is None and courses:
            return False
        try:
            ok = store.add_item(pid, path, course)
        except OSError:
            return False
        if ok:
            page = self.pages.currentWidget()
            lst = getattr(page, "list", None)
            if page is self.page_folder:
                lst = self.folder_stack.currentWidget()
            if lst is not None:
                key = song_lists.path_key(path)
                for r in lst.rows():
                    if isinstance(r, SongRow) and song_lists.path_key(r.path) == key:
                        r.flash()
            if page is self.page_playlist and self._playlist_id == pid:
                self._fill_playlist_songs(store.get(pid))
            # 入ったことが分かるように知らせる(連打して何度も入れてしまう、
            # という利用者の報告 2026-11-19)。続けて入れたら置き換える。
            from neotja.player.playlist_view import Toast, added_message
            if self.toast is None:
                self.toast = Toast(self)
            title = info.get("title") or os.path.splitext(os.path.basename(path))[0]
            self.toast.show_message(added_message(title, store.get(pid)["title"]))
        return ok

    #: 「入れました」の知らせ(初めて入れたときに作る)。
    toast = None

    def remove_playlist_item(self, index):
        pid = self._playlist_id
        if not pid or not self.playlist_store().remove_item(pid, index):
            return False
        if self._chosen_index == index:
            self._chosen_index = -1
            self._chosen_course = None
        elif self._chosen_index > index:
            self._chosen_index -= 1
        self._fill_playlist_songs(self._current_playlist())
        return True

    def move_playlist_item(self, src, dst):
        pid = self._playlist_id
        if not pid or not self.playlist_store().move_item(pid, src, dst):
            return False
        c = self._chosen_index
        if c >= 0:
            if c == src:
                c = dst
            else:
                if src < c:
                    c -= 1
                if dst <= c:
                    c += 1
            self._chosen_index = c
        self._fill_playlist_songs(self._current_playlist())
        return True

    def change_playlist_item_course(self, index):
        pl = self._current_playlist()
        if pl is None or not 0 <= index < len(pl["items"]):
            return False
        item = pl["items"][index]
        info = song_lists.song_info(item["path"])
        courses = info.get("courses") or []
        if not courses:
            return False
        course = self._pick_course(info.get("title", ""), courses, item.get("course"))
        if course is None or course == item.get("course"):
            return False
        self.playlist_store().set_item_course(pl["id"], index, course)
        if self._chosen_index == index:
            self._chosen_course = course
        self._fill_playlist_songs(pl)
        return True

    def _playlist_item_menu(self, index, global_pos):
        menu = QMenu(self)
        row = next((r for r in self.playlist_song_list.rows()
                    if getattr(r, "index", -1) == index), None)
        act_course = menu.addAction("難易度を変更")
        act_course.setEnabled(row is not None and not row.missing)
        act_remove = menu.addAction("外す")
        act = self.exec_menu(menu, global_pos)
        if act is act_course:
            self.change_playlist_item_course(index)
        elif act is act_remove:
            self.remove_playlist_item(index)

    def exec_menu(self, menu, global_pos):
        """右クリックの一覧を出して、選んだ項目を返す(試験は差し替える)。"""
        return menu.exec(global_pos)

    def _song_menu(self, path, global_pos):
        """いつもの曲の行の右クリック。「プレイリストに追加」の下に一覧。"""
        if not os.path.isfile(path):
            return
        menu = QMenu(self)
        sub = menu.addMenu("プレイリストに追加")
        items = self.playlist_store().items
        sub.setEnabled(bool(items))
        acts = {}
        for pl in items:
            acts[sub.addAction(pl["title"])] = pl["id"]
        act = self.exec_menu(menu, global_pos)
        if act in acts:
            self.add_song_to_playlist(acts[act], path)

    # --- 「曲を追加」のあいだ ---------------------------------------------
    def add_mode_target(self):
        return self._add_target

    def begin_add_mode(self, pid=None):
        """「曲を追加」: いつもの一覧を見て回り、押した曲をプレイリストへ入れる。"""
        pid = pid or self._playlist_id
        pl = self.playlist_store().get(pid)
        if pl is None:
            return
        self._add_target = pid
        self.add_bar.set_title(pl["title"])
        self.add_bar.show()
        self.show_default()

    def _leave_add_mode(self):
        if self._add_target:
            self._add_target = ""
            self.add_bar.hide()

    def end_add_mode(self):
        """「完了」: 追加先のプレイリストへ戻る。"""
        pid = self._add_target
        self._leave_add_mode()
        if pid and self.playlist_store().get(pid) is not None:
            self.open_playlist(pid)
        else:
            self.show_section("playlists")

    def _build_playlist_crumbs(self, pl):
        lay = self.page_playlist.crumbs
        while lay.count() > 1:
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        b = QPushButton("プレイリスト")
        b.setObjectName("crumb")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self._pi)
        b.clicked.connect(lambda _=False: self.show_section("playlists"))
        lay.insertWidget(lay.count() - 1, b)
        sep = QLabel("›")
        sep.setObjectName("crumbSep")
        lay.insertWidget(lay.count() - 1, sep)
        cur = QPushButton(pl["title"])
        cur.setObjectName("crumbCur")
        lay.insertWidget(lay.count() - 1, cur)

    def _edit_values(self, pl):
        """作成/編集の窓。やめたら None。"""
        if self.playlist_editor is not None:
            return self.playlist_editor(pl)
        dlg = PlaylistDialog(pl, parent=self.window(), cfg=self.cfg)
        try:
            if dlg.exec() == QDialog.Accepted:
                return dlg.values()
            return None
        finally:
            dlg.deleteLater()

    def create_playlist(self):
        got = self._edit_values(None)
        if not got or not str(got[0]).strip():
            return None
        title, desc, jacket, _clear = got
        title = str(title).strip()
        try:
            pl = self.playlist_store().create(title, desc, jacket)
        except OSError:
            return None
        if self.pages.currentWidget() is self.page_playlists:
            self._fill_playlists()
        return pl

    def edit_playlist(self, pid):
        pl = self.playlist_store().get(pid)
        if pl is None:
            return None
        got = self._edit_values(pl)
        if not got or not str(got[0]).strip():
            return None
        title, desc, jacket, clear = got
        title = str(title).strip()
        try:
            pl = self.playlist_store().update(pid, title, desc, jacket, clear)
        except OSError:
            return None
        self._refresh_playlist_views()
        return pl

    def ask_delete_playlist(self, pid):
        """削除。確かめの窓で「削除」なら消す(閉じるのを待たない)。"""
        pl = self.playlist_store().get(pid)
        if pl is None:
            return
        heading = "「%s」を削除" % pl["title"]
        if self.playlist_confirm is not None:
            self.playlist_confirm(heading, "削除", lambda: self.delete_playlist(pid))
            return
        dlg = ConfirmDialog(heading, "削除", self.window())
        dlg.accepted.connect(lambda: self.delete_playlist(pid))
        dlg.finished.connect(lambda *_a: (dlg.deleteLater(), self._pi()))
        dlg.open()

    def delete_playlist(self, pid):
        if not self.playlist_store().delete(pid):
            return False
        if self.pages.currentWidget() is self.page_playlist and self._playlist_id == pid:
            self.show_section("playlists")
        else:
            self._refresh_playlist_views()
        return True

    def _refresh_playlist_views(self):
        page = self.pages.currentWidget()
        if page is self.page_playlists:
            self._fill_playlists()
        elif page is self.page_playlist and self._playlist_id:
            pl = self.playlist_store().get(self._playlist_id)
            if pl is not None:
                page.lbl_title.setText(pl["title"])
                self._build_playlist_crumbs(pl)

    def _playlist_menu(self, pid, global_pos):
        menu = QMenu(self)
        act_edit = menu.addAction("編集")
        act_del = menu.addAction("削除")
        act = self.exec_menu(menu, global_pos)
        if act is act_edit:
            self.playlist_action(pid, "edit")
        elif act is act_del:
            self.playlist_action(pid, "delete")

    def playlist_action(self, pid, key):
        """プレイリストの右クリックの中身("edit" / "delete")。"""
        if key == "edit":
            self.edit_playlist(pid)
        elif key == "delete":
            self.ask_delete_playlist(pid)

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
                self.folder_box.addSpacing(14)
            head = CategoryHead(sim, collapsed=sim in self._collapsed())
            head.toggled.connect(self._on_category_toggled)
            head.toggled.connect(self._pi)
            head.setContextMenuPolicy(Qt.CustomContextMenu)
            head.customContextMenuRequested.connect(
                lambda pos, lbl=head, name=sim: self._category_menu(lbl, name, pos))
            self.folder_box.addWidget(head)
            self.folder_box.addSpacing(2)
            self.category_labels.append(head)
            for root in song_lists.folders_of(self.cfg, sim):
                for kind, path, name, color in self._sidebar_entries(root):
                    b = NavButton(name, "folder")
                    b.setProperty("sub", True)
                    b.path = path
                    #: 右クリックの「外す」「カテゴリを変更」は登録フォルダへ。
                    b.root = root
                    b.entry = kind
                    b.simulator = sim
                    b.setToolTip(path)
                    if kind == "genre":
                        b.set_dot_color(color or "#8a96a8")
                    if not os.path.isdir(path):
                        b.setProperty("missing", True)
                    b.clicked.connect(lambda _=False, p=path: self.show_section("folder", p))
                    b.clicked.connect(self._pi)
                    b.setContextMenuPolicy(Qt.CustomContextMenu)
                    b.customContextMenuRequested.connect(
                        lambda pos, btn=b: self._folder_menu(btn, pos))
                    self.folder_box.addWidget(b)
                    self.folder_navs.append(b)
                    head.navs.append(b)
            head.set_collapsed(head.collapsed)
        if isinstance(cur, tuple):
            for b in self.folder_navs:
                b.setChecked(song_lists.path_key(b.path) == song_lists.path_key(cur[1]))

    def _collapsed(self):
        """畳んでいるシミュレーターの名前(設定 player_collapsed)。"""
        got = self.cfg.get("player_collapsed")
        return set(got) if isinstance(got, list) else set()

    def _on_category_toggled(self, name, collapsed):
        names = self._collapsed()
        if collapsed:
            names.add(name)
        else:
            names.discard(name)
        self.cfg["player_collapsed"] = sorted(names)
        self._save()

    def _sidebar_entries(self, root):
        key = song_lists.path_key(root)
        got = self._sidebar_cache.get(key)
        if got is None:
            got = song_lists.sidebar_entries(root)
            self._sidebar_cache[key] = got
        return got

    def refresh_sidebar(self):
        """登録フォルダの中を読み直して左の列を作り直す。"""
        self._sidebar_cache = {}
        self._rebuild_folder_navs()

    def _folder_menu(self, btn, pos):
        menu = QMenu(btn)
        act_remove = menu.addAction("外す")
        act_cat = menu.addAction("カテゴリを変更")
        act = menu.exec(btn.mapToGlobal(pos))
        if act is act_remove:
            self.folder_action(btn, "remove")
        elif act is act_cat:
            self.folder_action(btn, "category")

    def folder_action(self, btn, key):
        """左の列の項目の右クリックの中身。ジャンルや「その他」から選んでも、
        相手は登録フォルダ(btn.root)。"""
        root = getattr(btn, "root", btn.path)
        if key == "remove":
            self.remove_folder(root)
        elif key == "category":
            self.change_folder_category(root)

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
            self._sidebar_cache.pop(song_lists.path_key(path), None)
            self._rebuild_folder_navs()
        path = os.path.normpath(os.path.abspath(path))
        # ジャンルに分けて並べたなら、その最初を開く。
        first = next((b for b in self.folder_navs
                      if song_lists.path_key(getattr(b, "root", "")) == song_lists.path_key(path)),
                     None)
        self.show_section("folder", first.path if first is not None else path)

    def change_folder_category(self, path):
        cur = song_lists.folder_simulator(self.cfg, path) or ""
        name = self.ask_category(current=cur, path=path, ok_text="変更")
        if name and song_lists.set_folder_simulator(self.cfg, path, name):
            self._save()
            self._rebuild_folder_navs()

    def rename_category(self, old):
        name = self.ask_category(current=old, heading="名前を変更", ok_text="変更")
        if name and song_lists.rename_simulator(self.cfg, old, name):
            names = self._collapsed()
            if old in names:
                # 畳んでいた見出しは、名前を変えても畳んだまま。
                names.discard(old)
                names.add(name)
                self.cfg["player_collapsed"] = sorted(names)
            self._save()
            self._rebuild_folder_navs()

    def remove_folder(self, path):
        """登録を外すだけ(フォルダの中身には触らない)。"""
        was = self.current_section()
        if song_lists.remove_folder(self.cfg, path):
            self._save()
            self._sidebar_cache.pop(song_lists.path_key(path), None)
            self._rebuild_folder_navs()
            if isinstance(was, tuple) and _is_under(was[1], path):
                self.show_default()

    def open_dir(self, directory):
        """登録フォルダの中の一段を開く。"""
        before = self._list_key()
        self._open_dir(directory)
        self._note_switch(before)

    def _open_dir(self, directory):
        self._folder_dir = directory
        page = self.page_folder
        page.lbl_title.setText(song_lists.display_name(directory))
        self._build_crumbs(directory)
        page.search.blockSignals(True)
        page.search.clear()
        page.search.blockSignals(False)
        self._stop_search()
        self._searched = ""
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
        if (self._hide_genres_in
                and song_lists.path_key(self._folder_dir) == self._hide_genres_in):
            genres = []          # 左の列にジャンルとして並んでいる
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

    #: 最後に一覧を出した検索の文字(替わったら試聴を止める)。
    _searched = ""

    def _run_folder_search(self):
        q = self.page_folder.search.text().strip()
        if q != self._searched:
            self._searched = q
            if self.pages.currentWidget() is self.page_folder:
                self.list_switched.emit()
        if not q or not self._folder_dir:
            self._stop_search()
            return
        detach_worker(self._search_worker)
        self.search_list.clear()
        self.folder_stack.setCurrentWidget(self.search_list)
        self._search_names = {}
        w = SearchWorker(self._folder_dir, q, parent=self,
                         art_cfg=thumbs.art_snapshot(self.cfg),
                         known=thumbs.CACHE.keys())
        w.found.connect(self._on_search_found)
        w.thumbs.connect(self._on_thumbs)
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
                                 path_text=text, cols=FOLDER_COLS)
            row.set_info(info)
            self.search_list.add(row)
        m, r = song_lists.sort_state(self.cfg)
        self.search_list.sort_songs(m, r)

    # ------------------------------------------------------------------
    def _song_lists(self):
        return (self.recent_list, self.saved_list, self.folder_list, self.search_list,
                self.playlist_song_list)

    def refresh_theme(self):
        for lst in self._song_lists() + (self.playlists_list,):
            for r in lst.rows():
                r.refresh_theme()
        self.update()

    def shutdown(self):
        # 録画中に閉じたら、書きかけは捨てて止める(ffmpeg を残さない)。
        if self.playlist_recording():
            self._plrec.cancel()
        detach_worker(self._worker)
        self._worker = None
        self._search_timer.stop()
        detach_worker(self._search_worker)
        self._search_worker = None


def secondary_color():
    """小さな字(副題・置き場所・日付・見出し)の色。地の紺に対して 4.5:1 を
    十分に超える、前景の青みを残した色(暗いテーマで約 7.4:1)。"""
    from neotja.theme import is_dark_theme
    return "#93a3bf" if is_dark_theme(COLORS) else "#4a5568"


def library_qss():
    """選曲の画面だけの QSS(ランチャーの QSS に足す)。"""
    p = COLORS
    SECONDARY = secondary_color()
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
    QPushButton#addFolder {{ text-align: left; background-color: transparent; border: 1px solid transparent;
                             border-radius: 9px; padding: 7px 14px 7px 30px; color: {SECONDARY}; font-size: 12px;
                             font-weight: 600; }}
    QPushButton#addFolder:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
    QLabel#pageTitle {{ color: {p['fg_bright']}; {fam_css} font-size: 24px; font-weight: 600; letter-spacing: 1px; }}
    QPushButton#quiet {{ background-color: transparent; border: 1px solid transparent; border-radius: 6px;
                         padding: 5px 10px; font-size: 12px; font-weight: 600; color: {SECONDARY}; }}
    QPushButton#quiet:hover {{ color: {p['fg']}; background-color: {_rgba(g['btn_fill'])}; }}
    QPushButton#quiet:focus {{ border: 1px solid {p['accent']}; }}
    QWidget#listHeader {{ background-color: transparent; border-bottom: 1px solid {_rgba(g['border'])}; }}
    QPushButton#colHead {{ background-color: transparent; border: none; padding: 0; text-align: left;
                           font-size: 12px; font-weight: bold; color: {SECONDARY}; }}
    QPushButton#colHead[align="right"] {{ text-align: right; }}
    QPushButton#colHead:hover {{ color: {p['fg_bright']}; }}
    QPushButton#colHead[on="true"] {{ color: #7fe3ff; }}
    QLabel#colLabel {{ font-size: 12px; font-weight: bold; color: {SECONDARY}; }}
    QLabel#rowNum {{ color: {SECONDARY}; font-size: 13px; font-weight: bold; }}
    QLabel#rowDate {{ color: {SECONDARY}; font-size: 13px; }}
    QLabel#courseText {{ color: {p['fg']}; font-size: 13px; font-weight: bold; }}
    QLabel#courseText[missing="true"] {{ color: {SECONDARY}; font-weight: 600; }}
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
    QLabel#category {{ color: {SECONDARY}; {fam_css} font-size: 12px; font-weight: bold;
                       letter-spacing: 1px; padding: 6px 0 4px 14px; }}
    QPushButton#nav[top="true"] {{ padding-left: 14px; font-size: 14px; }}
    QFrame#sideRule {{ background-color: {_rgba(g['border'])}; border: none; margin: 0 6px; }}
    QPushButton#nav[sub="true"] {{ padding-left: 30px; font-size: 13px; }}
    QPushButton#nav[dot="true"] {{ padding-left: 46px; }}
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
    QPushButton#flat {{ background-color: rgba(16, 28, 44, 170); border: 1px solid rgba(139, 217, 238, 150);
                        border-radius: 3px; padding: 8px 18px; font-family: "Yu Gothic UI"; font-size: 13px;
                        font-weight: bold; color: #e5edf7; }}
    QPushButton#flat:hover {{ background-color: rgba(35, 61, 84, 200); border-color: #8ad9ee; }}
    QPushButton#flat:pressed {{ background-color: rgba(16, 29, 44, 220); }}
    QPushButton#flat:focus {{ border-color: {p['accent']}; }}
    QWidget#addModeBar {{ background-color: {_rgba(_c(p['accent'], 0.20))};
                          border-bottom: 1px solid {_rgba(_c(p['accent'], 0.75))}; }}
    QLabel#addModePlus {{ color: {p['accent']}; font-size: 20px; font-weight: bold; }}
    QLabel#plIndex {{ color: {p['fg_dim']}; font-size: 14px; font-weight: bold; }}
    QWidget#recordBar {{ background-color: rgba(120, 24, 32, 70);
                         border-bottom: 1px solid rgba(236, 80, 80, 150); }}
    QLabel#recDot {{ color: #ec3c3c; font-size: 18px; }}
    QLabel#recDot[done="true"] {{ color: {p['accent']}; }}
    QLabel#recCount {{ color: {p['fg']}; font-size: 14px; font-weight: bold; }}
    QProgressBar#recProgress {{ background-color: rgba(255, 255, 255, 30); border: none; border-radius: 3px; }}
    QProgressBar#recProgress::chunk {{ background-color: {p['accent']}; border-radius: 3px; }}
    QFrame#dropLine {{ background-color: {p['accent']}; border: none; border-radius: 1px; }}
    """
