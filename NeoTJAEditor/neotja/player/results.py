"""結果画面(v15-design/PLAYER_IMPLEMENTATION_PLAN.md「結果」、配置の参考は
v15-design/player-approved/results-layout.png — 数字と王冠は仮の絵)。

左上にジャケットと曲名・副題、その下に半透明の小さな結果の板、右下に
「もう一度プレイする」「選曲画面に戻る」。背景・ジャケットの扱いは難易度選択と
同じ(背景なし・OFF は濃紺、ジャケットなし・OFF は No Image の枠)。

値は**再生画面の HUD と同じ計算**から譜面の終わりの時刻で引いたもの
(game_screen.result_values)。ここでは数えない。

王冠: 未クリアは無し / 銀 = クリア / 金 = フルコンボ / 虹 = 全良。絵は利用者の
TNDE の 6_Result/Crown.png(skin キャッシュの Result_Crown.png)の一番左の列。
無ければ色の丸(銀・金・虹色)を描く。
"""

import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetricsF,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QWidget

from neotja import settings as settings_mod
from neotja.player import song_lists
from neotja.player.difficulty import (BASE_H, BASE_W, BTN_RADIUS, FALLBACK_MARK, GLOW,
                                      NAVY, SLOT_NAMES, TOP_EDGE, _GlassButton,
                                      course_mark, draw_outlined, paint_glass,
                                      thin_font, title_font)

#: 王冠の素材(skin キャッシュの中の名前)と、使う左の列の切り出し(x, y, w, h)。
CROWN_FILE = "Result_Crown.png"
CROWN_RECTS = {"silver": (0, 0, 101, 92), "gold": (0, 92, 101, 92),
               "rainbow": (0, 184, 101, 92)}
CROWN_LABELS = {"silver": "クリア", "gold": "フルコンボ", "rainbow": "全良"}

_crown_cache = {}


def crown_kind(values):
    """王冠の種類。未クリアは None、クリア = silver、フルコンボ(不可 0 で全部の
    音符を叩いた)= gold、全良(全部 良)= rainbow。"""
    v = values or {}
    if not v.get("cleared"):
        return None
    n = int(v.get("note_count") or 0)
    good, ok, bad = int(v.get("good") or 0), int(v.get("ok") or 0), int(v.get("bad") or 0)
    if n > 0 and bad == 0 and good + ok >= n:
        return "rainbow" if ok == 0 else "gold"
    return "silver"


def crown_sheet_path():
    try:
        return os.path.join(str(settings_mod.skin_dir()), CROWN_FILE)
    except Exception:  # noqa: BLE001
        return ""


def crown_pixmap(kind, sheet=None):
    """王冠の絵(101x92・透明の余白ごと)。素材が無ければ None。"""
    if kind not in CROWN_RECTS:
        return None
    path = sheet or crown_sheet_path()
    key = (path, kind)
    if key in _crown_cache:
        return _crown_cache[key]
    pm = None
    if path and os.path.exists(path):
        full = QPixmap(path)
        if not full.isNull():
            x, y, w, h = CROWN_RECTS[kind]
            if full.width() >= x + w and full.height() >= y + h:
                pm = full.copy(x, y, w, h)
    _crown_cache[key] = pm
    return pm


def draw_crown_fallback(p, rect, kind):
    """素材が無いときの王冠の代わり: 銀・金・虹色の丸。"""
    r = QRectF(rect)
    d = min(r.width(), r.height()) * 0.78
    c = QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if kind == "rainbow":
        g = QConicalGradient(c.center(), 90)
        for i, col in enumerate(("#ff5a5a", "#ffb43c", "#ffe95a", "#5ae67a",
                                 "#4ac8ff", "#8a6cff", "#ff5ad2", "#ff5a5a")):
            g.setColorAt(i / 7.0, QColor(col))
        brush = QBrush(g)
    else:
        top, bot = (("#f7f9fc", "#9aa4b2") if kind == "silver" else ("#ffe58a", "#c98a12"))
        g = QLinearGradient(c.topLeft(), c.bottomLeft())
        g.setColorAt(0, QColor(top))
        g.setColorAt(1, QColor(bot))
        brush = QBrush(g)
    p.setPen(QPen(QColor(255, 255, 255, 200), max(1.0, d * 0.05)))
    p.setBrush(brush)
    p.drawEllipse(c)
    p.restore()


def _fmt(n):
    return "{:,}".format(int(n or 0))


class ResultButton(_GlassButton):
    """右下の2つ。文字だけの平らな板(主ボタンは Play と同じシアンの縁)。"""

    def __init__(self, text, primary=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.primary = bool(primary)

    def lit(self):
        return self.isEnabled() and (self._hover or self.hasFocus())

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = paint_glass(p, self.inner(), BTN_RADIUS * s, s, lit=self._hover or self.hasFocus(),
                        down=self.isDown(), focus=self.hasFocus() and not self.primary,
                        primary=self.primary, base_alpha=175,
                        edge=QColor(220, 228, 238, 150))
        f = thin_font(27 * s, QFont.Bold if self.primary else QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4 * s)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class ResultsPage(QWidget):
    retry_requested = Signal()
    library_requested = Signal()
    #: 操作音("pi" / "don")。
    sound_requested = Signal(str)

    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.setObjectName("resultsPage")
        self.cfg = config_data
        self.path = ""
        self.title = ""
        self.subtitle = ""
        self.course = "Oni"
        self.level = None
        self.values = {}
        self.crown = None
        self.gauge_pm = None
        self.judges = {}
        self._jacket = None
        self._bg = None
        self._bg_scaled = None
        #: 王冠の素材の場所(試験で差し替える)。None なら skin キャッシュ。
        self.crown_sheet = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.btn_retry = ResultButton("もう一度プレイする", primary=True, parent=self)
        self.btn_back = ResultButton("選曲画面に戻る", parent=self)
        self.btn_retry.clicked.connect(lambda: self.sound_requested.emit("don"))
        self.btn_retry.clicked.connect(self.retry_requested)
        self.btn_back.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_back.clicked.connect(self.library_requested)
        for b in (self.btn_retry, self.btn_back):
            b.installEventFilter(self)

    # ------------------------------------------------------------------
    def set_result(self, path, course, level, values, gauge=None, judges=None):
        """結果を流し込む。values は game_screen.result_values() の形。
        gauge は魂ゲージの絵(game_screen.gauge_strip)、judges は 良/可/不可 の
        絵 {"good","ok","bad"}。どちらも無ければ自前で描く。"""
        info = song_lists.song_info(path)
        self.path = path
        self.title = info.get("title", "")
        self.subtitle = info.get("subtitle", "")
        self.course = course
        self.level = level
        self.values = dict(values or {})
        self.crown = crown_kind(self.values)
        self.gauge_pm = gauge if (gauge is not None and not gauge.isNull()) else None
        self.judges = {k: v for k, v in (judges or {}).items()
                       if v is not None and not v.isNull()}

        def load(kind):
            pth = song_lists.existing_art(self.cfg, path, kind)
            if not pth:
                return None
            pm = QPixmap(pth)
            return None if pm.isNull() else pm
        self._jacket = load("jacket")
        self._bg = load("background")
        self._bg_scaled = None
        self.btn_retry.setFocus(Qt.OtherFocusReason)
        self.update()

    def crown_pixmap(self):
        return crown_pixmap(self.crown, self.crown_sheet)

    # ------------------------------------------------------------------
    # キー: Esc = 選曲画面に戻る、矢印・Tab で2つを行き来、Enter/Space で押す。
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.KeyPress and obj in (self.btn_retry, self.btn_back):
            if self._key(ev):
                return True
        return False

    def keyPressEvent(self, ev):
        self._key(ev)
        ev.accept()

    def _key(self, ev):
        k = ev.key()
        if k == Qt.Key_Escape:
            self.sound_requested.emit("pi")
            self.library_requested.emit()
            return True
        if k in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right, Qt.Key_Tab,
                 Qt.Key_Backtab):
            nxt = self.btn_back if self.btn_retry.hasFocus() else self.btn_retry
            nxt.setFocus(Qt.TabFocusReason)
            self.sound_requested.emit("pi")
            self.btn_retry.update()
            self.btn_back.update()
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            (self.btn_back if self.btn_back.hasFocus() else self.btn_retry).click()
            return True
        return False

    def focusNextPrevChild(self, nxt):
        return False         # Tab は _key で2つのあいだを行き来する

    # ------------------------------------------------------------------
    def _s(self):
        return max(0.5, min(self.width() / BASE_W, self.height() / BASE_H))

    def jacket_rect(self):
        s = self._s()
        return QRectF(62 * s, 62 * s, 190 * s, 190 * s)

    def panel_rect(self):
        s = self._s()
        return QRectF(48 * s, 296 * s, 720 * s, 494 * s)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._bg_scaled = None
        s = self._s()
        bw, bh = 430 * s, 82 * s
        g = GLOW * s
        x = self.width() - 56 * s - bw
        y2 = self.height() - 64 * s - bh
        y1 = y2 - 20 * s - bh
        for b, y in ((self.btn_retry, y1), (self.btn_back, y2)):
            b.scale = s
            b.setGeometry(int(x - g), int(y - g), int(bw + 2 * g), int(bh + 2 * g))

    def _bg_pixmap(self):
        if self._bg is None:
            return None
        size = self.size()
        if self._bg_scaled is None or self._bg_scaled.size() != size:
            sc = self._bg.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (sc.width() - size.width()) // 2
            y = (sc.height() - size.height()) // 2
            self._bg_scaled = sc.copy(x, y, size.width(), size.height())
        return self._bg_scaled

    def paintEvent(self, ev):
        s = self._s()
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        bg = self._bg_pixmap() if self.cfg.get("player_show_bg", True) else None
        if bg is not None:
            p.drawPixmap(0, 0, bg)
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, NAVY.lighter(108))
            grad.setColorAt(1, NAVY.darker(115))
            p.fillRect(self.rect(), QBrush(grad))
        # 左側は文字が読めるよう少し暗く(難易度選択の上の暗がりと同じ考え方)。
        side = QLinearGradient(0, 0, w * 0.6, 0)
        side.setColorAt(0, QColor(0, 0, 0, 120 if bg is not None else 40))
        side.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w * 0.6, h), QBrush(side))
        self._paint_header(p, s)
        self._paint_panel(p, s)
        p.end()

    def _paint_header(self, p, s):
        jr = self.jacket_rect()
        if self._jacket is not None and self.cfg.get("player_show_jacket", True):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRect(jr.translated(0, 6 * s))
            p.drawPixmap(jr, self._jacket, QRectF(self._jacket.rect()))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.6 * s))
            p.setBrush(QColor(10, 18, 32, 150))
            p.drawRect(jr)
            p.setFont(thin_font(30 * s, QFont.DemiBold))
            p.setPen(QColor(235, 240, 248))
            p.drawText(jr, Qt.AlignCenter, "No\nImage")
        x = jr.right() + 36 * s
        avail = self.width() - x - 60 * s
        size = 64 * s
        f = title_font(size)
        while QFontMetricsF(f).horizontalAdvance(self.title) > avail and size > 30 * s:
            size -= 2 * s
            f = title_font(size)
        text = QFontMetricsF(f).elidedText(self.title, Qt.ElideRight, avail)
        base = jr.top() + 100 * s
        draw_outlined(p, text, f, x, base, 9 * s)
        if self.subtitle:
            sf = title_font(30 * s)
            sub = QFontMetricsF(sf).elidedText(self.subtitle, Qt.ElideRight, avail)
            draw_outlined(p, sub, sf, x, base + 52 * s, 6 * s)

    def _paint_panel(self, p, s):
        r = self.panel_rect()
        paint_glass(p, r, BTN_RADIUS * s, s, base_alpha=190, edge=TOP_EDGE)
        pad = 40 * s
        x0, x1 = r.left() + pad, r.right() - pad
        v = self.values

        # 1行目: マーク・難易度名・★・オート再生
        cy = r.top() + 62 * s
        mark_d = 70 * s
        mr = QRectF(x0, cy - mark_d / 2, mark_d, mark_d)
        pm = course_mark(self.course)
        if pm is not None:
            if pm.width() == pm.height():
                p.drawPixmap(mr.adjusted(-6 * s, -6 * s, 6 * s, 6 * s), pm, QRectF(pm.rect()))
            else:
                p.drawPixmap(mr, pm, QRectF(pm.rect()))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(FALLBACK_MARK.get(self.course, "#e6248c")))
            p.drawEllipse(mr)
        x = mr.right() + 22 * s
        nf = title_font(36 * s)
        name = SLOT_NAMES.get(self.course, self.course)
        fm = QFontMetricsF(nf)
        draw_outlined(p, name, nf, x, cy + (fm.ascent() - fm.descent()) / 2, 5 * s)
        x += fm.horizontalAdvance(name) + 22 * s
        star = "★%d" % self.level if self.level is not None else "★—"
        sfnt = thin_font(34 * s, QFont.Bold)
        p.setFont(sfnt)
        p.setPen(QColor(255, 255, 255))
        sw = QFontMetricsF(sfnt).horizontalAdvance(star)
        p.drawText(QRectF(x, cy - 28 * s, sw + 4, 56 * s), Qt.AlignLeft | Qt.AlignVCenter, star)
        x += sw + 24 * s
        bfnt = thin_font(20 * s, QFont.Bold)
        badge = "オート再生"
        bw = QFontMetricsF(bfnt).horizontalAdvance(badge) + 30 * s
        br = QRectF(x, cy - 20 * s, bw, 40 * s)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.2 * s))
        p.setBrush(QColor(108, 116, 128, 225))
        p.drawRoundedRect(br, 4 * s, 4 * s)
        p.setFont(bfnt)
        p.setPen(QColor(255, 255, 255))
        p.drawText(br, Qt.AlignCenter, badge)

        # 2行目: 魂ゲージ
        gy = cy + 58 * s
        self._paint_gauge(p, QRectF(x0, gy, x1 - x0, 50 * s), s)

        # 3行目: スコアと王冠
        sy = gy + 80 * s
        p.setFont(thin_font(20 * s, QFont.DemiBold))
        p.setPen(QColor(205, 222, 236))
        p.drawText(QRectF(x0, sy, 200 * s, 26 * s), Qt.AlignLeft | Qt.AlignVCenter, "スコア")
        big = thin_font(66 * s, QFont.Bold)
        p.setFont(big)
        p.setPen(QColor(255, 255, 255))
        score_txt = _fmt(v.get("score"))
        score_w = QFontMetricsF(big).horizontalAdvance(score_txt)
        p.drawText(QRectF(x0, sy + 22 * s, score_w + 10 * s, 84 * s),
                   Qt.AlignLeft | Qt.AlignVCenter, score_txt)
        div_x = max(x0 + 360 * s, x0 + score_w + 34 * s)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1.2 * s))
        p.drawLine(QPointF(div_x, sy + 26 * s), QPointF(div_x, sy + 100 * s))
        if self.crown:
            cr = QRectF(div_x + 26 * s, sy + 18 * s, 101 * s * 0.9, 92 * s * 0.9)
            cpm = self.crown_pixmap()
            if cpm is not None:
                p.drawPixmap(cr, cpm, QRectF(cpm.rect()))
            else:
                draw_crown_fallback(p, cr, self.crown)
            p.setFont(thin_font(26 * s, QFont.Bold))
            p.setPen(QColor(255, 226, 140) if self.crown != "silver" else QColor(225, 232, 240))
            p.drawText(QRectF(cr.right() + 14 * s, cr.top(), x1 - cr.right(), cr.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, CROWN_LABELS[self.crown])

        # 4・5行目: 良 / 可 / 不可、連打数 / 最大コンボ数
        ry = sy + 130 * s
        row_h = 66 * s
        line = QPen(QColor(255, 255, 255, 50), 1.0 * s)
        for i in range(3):
            p.setPen(line)
            p.drawLine(QPointF(x0, ry + i * row_h), QPointF(x1, ry + i * row_h))
        third = (x1 - x0) / 3.0
        cells = (("good", "良", v.get("good")), ("ok", "可", v.get("ok")),
                 ("bad", "不可", v.get("bad")))
        num_f = thin_font(36 * s, QFont.Bold)
        for i, (key, text, num) in enumerate(cells):
            cx = x0 + i * third
            cell = QRectF(cx + 16 * s, ry, third - 32 * s, row_h)
            self._paint_judge_label(p, key, text, QRectF(cell.left(), ry, 80 * s, row_h), s)
            p.setFont(num_f)
            p.setPen(QColor(255, 255, 255))
            p.drawText(cell, Qt.AlignRight | Qt.AlignVCenter, _fmt(num))
            if i:
                p.setPen(line)
                p.drawLine(QPointF(cx, ry + 14 * s), QPointF(cx, ry + row_h - 14 * s))
        ry2 = ry + row_h
        half = (x1 - x0) / 2.0
        lf2 = thin_font(24 * s, QFont.DemiBold)
        for i, (text, num) in enumerate((("連打数", v.get("rolls")),
                                         ("最大コンボ数", v.get("max_combo")))):
            cx = x0 + i * half
            cell = QRectF(cx + 16 * s, ry2, half - 32 * s, row_h)
            p.setFont(lf2)
            p.setPen(QColor(205, 222, 236))
            p.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, text)
            p.setFont(num_f)
            p.setPen(QColor(255, 255, 255))
            p.drawText(cell, Qt.AlignRight | Qt.AlignVCenter, _fmt(num))
            if i:
                p.setPen(line)
                p.drawLine(QPointF(cx, ry2 + 14 * s), QPointF(cx, ry2 + row_h - 14 * s))

    def _paint_judge_label(self, p, key, text, rect, s):
        pm = self.judges.get(key)
        if pm is not None:
            hh = 40 * s
            ww = pm.width() * hh / max(1, pm.height())
            p.drawPixmap(QRectF(rect.left(), rect.center().y() - hh / 2, ww, hh), pm,
                         QRectF(pm.rect()))
            return
        col = {"good": QColor(255, 196, 64), "ok": QColor(240, 240, 240),
               "bad": QColor(120, 170, 255)}[key]
        f = title_font(30 * s)
        fm = QFontMetricsF(f)
        draw_outlined(p, text, f, rect.left(),
                      rect.center().y() + (fm.ascent() - fm.descent()) / 2,
                      5 * s, fill=col.name())

    def _paint_gauge(self, p, rect, s):
        ratio = max(0.0, min(1.0, float(self.values.get("gauge_ratio") or 0.0)))
        pm = self.gauge_pm
        if pm is not None:
            hh = rect.height()
            ww = pm.width() * hh / max(1, pm.height())
            if ww > rect.width():
                ww = rect.width()
                hh = pm.height() * ww / max(1, pm.width())
            p.drawPixmap(QRectF(rect.left(), rect.center().y() - hh / 2, ww, hh), pm,
                         QRectF(pm.rect()))
            return
        # 素材が無いとき: 平らな帯。クリア圏(80%)から先を明るく。
        bar = QRectF(rect.left(), rect.center().y() - 13 * s, rect.width(), 26 * s)
        path = QPainterPath()
        path.addRoundedRect(bar, 13 * s, 13 * s)
        p.save()
        p.setClipPath(path)
        p.fillRect(bar, QColor(30, 40, 58, 230))
        fill = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
        g = QLinearGradient(bar.topLeft(), bar.topRight())
        g.setColorAt(0, QColor("#ff6a5a"))
        g.setColorAt(0.79, QColor("#ffb43c"))
        g.setColorAt(0.8, QColor("#ffe24a"))
        g.setColorAt(1, QColor("#fff27a"))
        p.fillRect(fill, QBrush(g))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 160), 1.4 * s))
        cx = bar.left() + bar.width() * 0.8
        p.drawLine(QPointF(cx, bar.top() - 4 * s), QPointF(cx, bar.bottom() + 4 * s))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
