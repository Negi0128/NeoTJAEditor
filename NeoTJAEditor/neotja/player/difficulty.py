"""Player の難易度選択(v15-design/PLAYER_IMPLEMENTATION_PLAN.md「難易度選択」、
確定画像は v15-design/player-approved/difficulty-*.png と settings-final.png)。

上は背景を広く見せて、ジャケット・曲名・副題。下の帯に かんたん/ふつう/
むずかしい/おに の4枚と、右に「プレイ」「録画」。上の隅に「戻る」「設定」。

- 譜面に無いコースは暗く、レベルは「—」、選べない。
- おに と 裏 は同じ1枚を分け合う。名前はどちらも「おに」で、マークの色
  (表=桃・裏=紫)で見分ける。裏があるときだけ、カードの右上の角から
  はみ出す丸い「⇔」で切り替える。切り替えるとマーク・★・プレイ/録画の
  対象が一緒に変わる。マークの位置と大きさは動かさない。
- 裏しか無い譜面は、最初から裏を選んでおく。
- 設定は右から半透明で重ねる(後ろの並びは縮めない)。

マークは System 素材(Select_Cards.png = TNDE の Difficulty_Bar)の絵の上側を
切り出して使う。素材が無ければ色の丸で代わりに描く。曲名・副題・難易度名は
FOT-大江戸勘亭流 Std E(System の Kanteiryu.otf)。★・ボタンは細い通常の書体。

いまは音を出さない(DEMOSTART の試聴は次の段)。プレイ/録画は合図を出すだけ。
"""

import os

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from neotja import settings as settings_mod
from neotja.player import song_lists
from neotja.theme import COLORS

#: 4枚の並び。最後の1枚は おに と 裏 で分け合う。
SLOTS = ("Easy", "Normal", "Hard", "Oni")
#: カードに出す名前(裏も「おに」)。
SLOT_NAMES = {"Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい",
              "Oni": "おに", "Edit": "おに"}
#: 素材がないときのマークの色。
FALLBACK_MARK = {"Easy": "#ff5a3c", "Normal": "#8cc63f", "Hard": "#2fa7c9",
                 "Oni": "#e6248c", "Edit": "#7a3ce6"}

#: Select_Cards.png(Difficulty_Bar)の中のカード(実測)。select_screen.py と同じ数字。
_CARD_X0, _CARD_PITCH = 176, 143.25
_CARD_INDEX = {"Easy": 0, "Normal": 1, "Hard": 2, "Oni": 3, "Edit": 4}
#: カードの中の絵の部分(縁の内側から、名前の上まで)。
_MARK_CROP = (8, 10, 115, 125)

#: 曲名などの書体の候補。読み込んだファミリー名で照合する(ファイル名だけで
#: 決めつけない)。無ければ代わりの太めの書体。
KANTEI_FAMILIES = ("FOT-大江戸勘亭流 Std E", "FOT-OedKtr Std E")
KANTEI_FALLBACK = ("Yu Gothic UI", "Meiryo")
#: ★・ボタン用の細い書体。
UI_FAMILIES = ("Yu Gothic UI", "Segoe UI", "Meiryo")

#: 背景が無いとき・切ったときの地色(濃紺)。
NAVY = QColor("#13223d")
#: 選んでいるカードの縁の色。
SELECT_COLOR = QColor("#7fe3ff")

#: 設計の基準の大きさ。窓がこれより小さければ全体を縮める。
BASE_W, BASE_H = 1600, 900

_font_cache = {}


def _available(fams):
    have = set(QFontDatabase.families())
    for f in fams:
        if f in have:
            return f
    return None


def kantei_family():
    """勘亭流のファミリー名。System の Kanteiryu.otf を読み込んでから照合する。"""
    if "kantei" in _font_cache:
        return _font_cache["kantei"]
    fam = _available(KANTEI_FAMILIES)
    if fam is None:
        try:
            f = os.path.join(str(settings_mod.skin_dir()), "Kanteiryu.otf")
            if os.path.exists(f):
                QFontDatabase.addApplicationFont(f)
        except Exception:  # noqa: BLE001
            pass
        fam = _available(KANTEI_FAMILIES)
    _font_cache["kantei"] = fam
    return fam


def ui_family():
    if "ui" not in _font_cache:
        _font_cache["ui"] = _available(UI_FAMILIES) or ""
    return _font_cache["ui"]


def title_font(px):
    fam = kantei_family()
    f = QFont(fam or (_available(KANTEI_FALLBACK) or ""))
    f.setPixelSize(max(6, int(px)))
    if not fam:
        f.setBold(True)
    return f


def thin_font(px, weight=QFont.DemiBold):
    f = QFont(ui_family())
    f.setPixelSize(max(6, int(px)))
    f.setWeight(weight)
    return f


def outlined_path(text, font, x, baseline):
    path = QPainterPath()
    path.addText(QPointF(x, baseline), font, text)
    return path


def draw_outlined(p, text, font, x, baseline, outline_px, fill="#ffffff",
                  outline="#141414"):
    """白抜き + 黒縁の文字(縁を先に太く描いてから中を塗る)。"""
    path = outlined_path(text, font, x, baseline)
    pen = QPen(QColor(outline), outline_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.strokePath(path, pen)
    p.fillPath(path, QColor(fill))


_marks = {}


#: Select_Marks.png(TNDE の Difficulty_Mark.png)の並び。上の段に5つ。
_MARK_SHEET_COLS = 5
#: 1コマの中で使う正方形(1コマ 458px のとき)。5つのマークの不透明な範囲を
#: すべて含む大きさで、どのコースでも同じ所を切るので、おに と 裏 の
#: 位置と大きさがずれない。
_MARK_SQUARE = (48, 50, 362)


def course_mark(key):
    """コースのマーク(背景なしの絵)。正方形。無ければ None。

    System の 3_SongSelect/Difficulty_Select/Difficulty_Mark.png
    (キャッシュの Select_Marks.png)から切り出す。それが無い古いキャッシュでは
    カードの絵(Select_Cards.png)の上側を切ったもの(背景つき)に落ちる。"""
    if key in _marks:
        return _marks[key]
    pm = None
    try:
        sheet = os.path.join(str(settings_mod.skin_dir()), "Select_Marks.png")
        if os.path.exists(sheet):
            full = QPixmap(sheet)
            if not full.isNull():
                cw = full.width() / float(_MARK_SHEET_COLS)
                k = cw / 458.0
                x0, y0, side = _MARK_SQUARE
                pm = full.copy(int(round(_CARD_INDEX[key] * cw + x0 * k)),
                               int(round(y0 * k)), int(round(side * k)),
                               int(round(side * k)))
                pm = None if pm.isNull() else pm
    except Exception:  # noqa: BLE001
        pm = None
    if pm is not None:
        _marks[key] = pm
        return pm
    try:
        src = os.path.join(str(settings_mod.skin_dir()), "Select_Cards.png")
        if os.path.exists(src):
            full = QPixmap(src)
            if not full.isNull():
                x = int(round(_CARD_X0 + _CARD_PITCH * _CARD_INDEX[key]))
                cx, cy, cw, ch = _MARK_CROP
                pm = full.copy(x + cx, cy, cw, ch)
    except Exception:  # noqa: BLE001
        pm = None
    _marks[key] = pm
    return pm


# ----------------------------------------------------------------------
# 部品
# ----------------------------------------------------------------------
class CourseCard(QWidget):
    """難易度の1枚。"""

    clicked = Signal(str)   # 何番目の枠か(SLOTS の名前)

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.key = slot          # いま見せているコース(おにの枠は Oni / Edit)
        self.level = None
        self.available = False
        self.selected = False
        self._hover = False
        self.scale = 1.0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def set_state(self, key, available, level, selected):
        self.key, self.available, self.level, self.selected = key, available, level, selected
        self.setCursor(Qt.PointingHandCursor if available else Qt.ArrowCursor)
        self.update()

    def level_text(self):
        return "★%d" % self.level if (self.available and self.level is not None) else "—"

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.available:
            self.clicked.emit(self.slot)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # 選んでいる枠の光が外へ出るぶん、内側に描く。
        pad = 8 * s
        r = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        rad = RADIUS * s
        if not self.available:
            p.setOpacity(0.38)
        paint_glass(p, r, rad, s, lit=self._hover and self.available,
                    selected=self.selected, base_alpha=190)

        # マーク(位置と大きさは表/裏で同じ)
        mw, mh = 112 * s, 112 * s
        mr = QRectF(r.center().x() - mw / 2, r.top() + 10 * s, mw, mh)
        pm = course_mark(self.key)
        if pm is not None and pm.width() == pm.height():
            # 背景なしのマーク(正方形)。そのまま置く。
            p.drawPixmap(mr, pm, QRectF(pm.rect()))
        elif pm is not None:
            # 古いキャッシュ: カードの絵を切ったもの(背景つき)を角丸で。
            clip = QPainterPath()
            clip.addRoundedRect(mr, 12 * s, 12 * s)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(mr, pm, QRectF(pm.rect()))
            p.restore()
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(FALLBACK_MARK.get(self.key, "#888")))
            d = min(mw, mh) * 0.8
            p.drawEllipse(mr.center(), d / 2, d / 2)

        # 名前(勘亭流・白抜き)
        f = title_font(30 * s)
        name = SLOT_NAMES.get(self.key, self.key)
        fm = QFontMetricsF(f)
        x = r.center().x() - fm.horizontalAdvance(name) / 2
        base = mr.bottom() + 36 * s
        draw_outlined(p, name, f, x, base, 5 * s)

        # ★レベル(細い書体・名前より小さく)
        lf = thin_font(20 * s, QFont.DemiBold)
        p.setFont(lf)
        p.setPen(QColor(235, 242, 250))
        p.drawText(QRectF(r.left(), base + 5 * s, r.width(), 28 * s),
                   Qt.AlignHCenter | Qt.AlignTop, self.level_text())
        p.end()


# ----------------------------------------------------------------------
# ガラスの描き方(この画面のボタン・カード・切り替えを1つの見た目にそろえる)
# ----------------------------------------------------------------------
#: カード・プレイ/録画の角の丸み(倍率 1 のとき)。
RADIUS = 12
#: ボタン(戻る・設定・プレイ・録画)の角。見本どおり、ほぼ四角。
BTN_RADIUS = 3
#: 戻る・設定の縁(薄いシアン)。
TOP_EDGE = QColor(150, 215, 235, 190)
#: 光のために部品の外側へ取っておく余白(倍率 1 のとき)。
GLOW = 10


def _accent():
    return QColor(COLORS["accent"])


def paint_glass(p, r, rad, s, *, lit=False, down=False, focus=False,
                primary=False, selected=False, shadow=False, base_alpha=160,
                edge=None):
    """平らな半透明の板を1枚描く(利用者の見本 2026-11-16 に合わせる)。

    暗い半透明の塗りと細い縁だけ。つや・グラデーション・重い影は使わない。
    乗せたとき: 縁が少し明るく、塗りがわずかに持ち上がる。
    フォーカス: シアンの縁。押しているあいだ: 塗りが少し暗くなる。
    主ボタン(primary): 塗りはほかと同じ暗さで、縁を明るいシアンにする。
    選んだカード(selected): シアンの縁にうっすら光。"""
    r = QRectF(r)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if selected:
        glow = QColor(SELECT_COLOR)
        for i, a in ((6, 0.06), (3, 0.14)):
            glow.setAlphaF(a)
            p.setPen(QPen(glow, i * s))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-i * s / 2, -i * s / 2, i * s / 2, i * s / 2),
                              rad + i * s / 2, rad + i * s / 2)
    fill = QColor(10, 20, 36, base_alpha)
    if down:
        fill = QColor(4, 10, 20, min(255, base_alpha + 30))
    elif lit or selected:
        fill = QColor(26, 42, 66, min(255, base_alpha + 15))
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(r, rad, rad)
    if focus or selected:
        col, width = QColor(SELECT_COLOR), (2.0 if selected else 1.6) * s
    elif primary:
        col = QColor(SELECT_COLOR).lighter(112 if lit else 100)
        width = 1.8 * s
    else:
        col = QColor(edge) if edge is not None else QColor(255, 255, 255, 120)
        if lit:
            col.setAlpha(min(255, col.alpha() + 80))
        width = 1.3 * s
    p.setPen(QPen(col, width))
    p.setBrush(Qt.NoBrush)
    half = width / 2
    p.drawRoundedRect(r.adjusted(half, half, -half, -half), rad, rad)
    p.restore()
    return r


class _GlassButton(QAbstractButton):
    """ガラスのボタンの共通部分(乗せた状態・押せるカーソル)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale = 1.0
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def lit(self):
        return self.isEnabled() and self._hover

    def inner(self):
        g = GLOW * self.scale
        return QRectF(self.rect()).adjusted(g, g, -g, -g)


class GlassPill(_GlassButton):
    """上の隅の「戻る」「設定」。丸い端の板に、線で描いた記号と細い字。"""

    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_kind = icon

    def _font(self):
        f = thin_font(22 * self.scale, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3 * self.scale)
        return f

    def sizeHint(self):
        s = self.scale
        tw = QFontMetricsF(self._font()).horizontalAdvance(self.text())
        w = 22 * s + 20 * s + 12 * s + tw + 26 * s
        return QSize(int(w + 2 * GLOW * s), int(52 * s + 2 * GLOW * s))

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = paint_glass(p, self.inner(), BTN_RADIUS * s, s,
                        lit=self.lit(), down=self.isDown(), focus=self.hasFocus(),
                        base_alpha=150, edge=TOP_EDGE)
        cx = r.left() + 22 * s + 10 * s
        cy = r.center().y()
        pen = QPen(QColor(255, 255, 255), 2.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.icon_kind == "back":
            h = 8 * s
            p.drawPolyline(QPolygonF([QPointF(cx + h * 0.5, cy - h), QPointF(cx - h * 0.5, cy),
                                      QPointF(cx + h * 0.5, cy + h)]))
        else:
            _draw_gear(p, QPointF(cx, cy), 10 * s, s)
        p.setFont(self._font())
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(cx + 10 * s + 12 * s, r.top(), r.width(), r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


def _draw_gear(p, c, rad, s):
    """歯車(線)。8枚の歯と中の穴。"""
    import math
    path = QPainterPath()
    teeth = 8
    for i in range(teeth * 2):
        a0 = math.pi * 2 * i / (teeth * 2)
        a1 = math.pi * 2 * (i + 1) / (teeth * 2)
        rr = rad if i % 2 == 0 else rad * 0.76
        pts = (QPointF(c.x() + rr * math.cos(a0), c.y() + rr * math.sin(a0)),
               QPointF(c.x() + rr * math.cos(a1), c.y() + rr * math.sin(a1)))
        if i == 0:
            path.moveTo(pts[0])
        else:
            path.lineTo(pts[0])
        path.lineTo(pts[1])
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(c, rad * 0.32, rad * 0.32)


class GlassCircle(_GlassButton):
    """丸いガラスのボタン。「⇔」(swap)と「×」(close)。記号は線で描く。"""

    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.icon_kind = icon

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ir = self.inner()
        d = min(ir.width(), ir.height())
        ir = QRectF(ir.center().x() - d / 2, ir.center().y() - d / 2, d, d)
        r = paint_glass(p, ir, d / 2, s, lit=self.lit(), down=self.isDown(),
                        focus=self.hasFocus(), base_alpha=225 if self.icon_kind == "swap" else 110,
                        edge=QColor(255, 255, 255, 200) if self.icon_kind == "swap" else TOP_EDGE)
        c = r.center()
        pen = QPen(QColor(255, 255, 255), 2.3 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        if self.icon_kind == "close":
            k = d * 0.17
            p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
            p.drawLine(QPointF(c.x() - k, c.y() + k), QPointF(c.x() + k, c.y() - k))
        else:
            w = d * 0.28
            h = d * 0.075
            p.drawLine(QPointF(c.x() - w + h, c.y() - h), QPointF(c.x() + w - h, c.y() - h))
            p.drawLine(QPointF(c.x() - w + h, c.y() + h), QPointF(c.x() + w - h, c.y() + h))
            for sgn in (-1, 1):
                tip = QPointF(c.x() + sgn * w, c.y())
                p.drawLine(tip, QPointF(c.x() + sgn * (w - h * 2.6), c.y() - h * 2.6))
                p.drawLine(tip, QPointF(c.x() + sgn * (w - h * 2.6), c.y() + h * 2.6))
        p.end()


class SwapButton(GlassCircle):
    """おに/裏 を切り替える丸い「⇔」。"""

    def __init__(self, parent=None):
        super().__init__("swap", parent)
        self.setToolTip("おに / 裏")
        self.setFocusPolicy(Qt.NoFocus)


class ActionButton(_GlassButton):
    """「▶ プレイ」(主・アクセント色)と「● 録画」(従・ガラスに小さな赤い点)。
    ウィジェットは光のぶん GLOW だけ外側に余白を持つ。"""

    GLOW = GLOW

    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_kind = icon
        self.primary = icon == "play"

    def lit(self):
        return self.isEnabled() and (self._hover or self.hasFocus())

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        r = paint_glass(p, self.inner(), BTN_RADIUS * s, s, lit=self._hover and self.isEnabled(),
                        down=self.isDown(), focus=self.hasFocus() and not self.primary,
                        primary=self.primary, base_alpha=175,
                        edge=QColor(220, 228, 238, 150))
        px = (28 if self.primary else 25) * s
        f = thin_font(px, QFont.Bold if self.primary else QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, (6 if self.primary else 4) * s)
        fm = QFontMetricsF(f)
        icon_w = (20 if self.primary else 12) * s
        gap = (22 if self.primary else 16) * s
        text_w = fm.horizontalAdvance(self.text())
        x0 = r.center().x() - (icon_w + gap + text_w) / 2
        cy = r.center().y()
        if self.primary:
            h = 11 * s
            tri = QPainterPath()
            tri.moveTo(x0, cy - h)
            tri.lineTo(x0 + icon_w, cy)
            tri.lineTo(x0, cy + h)
            tri.closeSubpath()
            p.setPen(QPen(QColor(255, 255, 255), 2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QColor(255, 255, 255))
            p.drawPath(tri)      # 太い丸い縁で角を丸くする
        else:
            c = QPointF(x0 + icon_w / 2, cy)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(236, 34, 34))
            p.drawEllipse(c, 7 * s, 7 * s)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x0 + icon_w + gap, r.top(), text_w + 20 * s, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


class ToggleSwitch(_GlassButton):
    """ON/OFF の切り替え。ON はアクセント色の板、OFF はガラス。つまみは白に影。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)

    def sizeHint(self):
        g = GLOW * self.scale
        return QSize(int(92 * self.scale + 2 * g), int(36 * self.scale + 2 * g))

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        r = QRectF(self.inner())
        p.setPen(QPen(QColor(SELECT_COLOR) if on else QColor(255, 255, 255, 140 if self.lit() else 100),
                      1.3 * s))
        on_fill = QColor(SELECT_COLOR)
        on_fill.setAlpha(80 if not self.lit() else 105)
        p.setBrush(on_fill if on else QColor(10, 20, 36, 150))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 8 * s
        kx = r.right() - 4 * s - d if on else r.left() + 4 * s
        knob = QRectF(kx, r.top() + 4 * s, d, d)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255) if on else QColor(200, 210, 222))
        p.drawEllipse(knob)
        f = thin_font(15 * s, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5 * s)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 235 if on else 170))
        tr = QRectF(r.left() + 6 * s, r.top(), kx - r.left() - 6 * s, r.height()) if on else \
            QRectF(knob.right(), r.top(), r.right() - knob.right() - 4 * s, r.height())
        p.drawText(tr, Qt.AlignCenter, "ON" if on else "OFF")
        p.end()


class Stepper(_GlassButton):
    """数を1つずつ増減する小さな板(ToggleSwitch と同じ作り)。
    左の「−」・右の「＋」を押す(押し続けると連続)、矢印キー・ホイールでも動く。"""

    valueChanged = Signal(int)

    def __init__(self, lo, hi, suffix="", parent=None):
        super().__init__(parent)
        self.lo, self.hi = int(lo), int(hi)
        self.suffix = suffix
        self._value = self.lo
        self._press = 0          # 押している側(-1 / +1)
        self._repeat = QTimer(self)
        self._repeat.timeout.connect(self._on_repeat)

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = self.lo
        v = max(self.lo, min(self.hi, v))
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def step(self, d):
        self.setValue(self._value + d, emit=True)

    def sizeHint(self):
        g = GLOW * self.scale
        return QSize(int(176 * self.scale + 2 * g), int(36 * self.scale + 2 * g))

    def _side(self, x):
        r = self.inner()
        zone = r.height() * 1.15
        if x <= r.left() + zone:
            return -1
        if x >= r.right() - zone:
            return 1
        return 0

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return super().mousePressEvent(ev)
        side = self._side(ev.position().x())
        self.setFocus(Qt.MouseFocusReason)
        if side:
            self._press = side
            self.step(side)
            self._repeat.start(380)
            self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        self._press = 0
        self._repeat.stop()
        self.update()
        ev.accept()

    def _on_repeat(self):
        if not self._press:
            self._repeat.stop()
            return
        self._repeat.setInterval(55)
        self.step(self._press)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key_Left, Qt.Key_Down, Qt.Key_Minus):
            self.step(-1)
        elif k in (Qt.Key_Right, Qt.Key_Up, Qt.Key_Plus):
            self.step(1)
        else:
            return super().keyPressEvent(ev)
        ev.accept()

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        if d:
            self.step(1 if d > 0 else -1)
        ev.accept()

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.inner())
        lit = self.lit() or self.hasFocus()
        p.setPen(QPen(QColor(255, 255, 255, 140 if lit else 100), 1.3 * s))
        p.setBrush(QColor(10, 20, 36, 150))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        zone = r.height() * 1.15
        pen = QPen(QColor(255, 255, 255), 2.2 * s, Qt.SolidLine, Qt.RoundCap)
        for side in (-1, 1):
            cx = (r.left() + zone / 2) if side < 0 else (r.right() - zone / 2)
            c = QPointF(cx, r.center().y())
            at_end = self._value <= self.lo if side < 0 else self._value >= self.hi
            if self._press == side:
                p.setPen(Qt.NoPen)
                on_fill = QColor(SELECT_COLOR)
                on_fill.setAlpha(110)
                p.setBrush(on_fill)
                p.drawEllipse(c, r.height() / 2 - 3 * s, r.height() / 2 - 3 * s)
            pen.setColor(QColor(255, 255, 255, 80 if at_end else 235))
            p.setPen(pen)
            k = 6 * s
            p.drawLine(QPointF(c.x() - k, c.y()), QPointF(c.x() + k, c.y()))
            if side > 0:
                p.drawLine(QPointF(c.x(), c.y() - k), QPointF(c.x(), c.y() + k))
        p.setFont(thin_font(16 * s, QFont.Bold))
        p.setPen(QColor(255, 255, 255, 235))
        text = "%d%s" % (self._value, (" " + self.suffix) if self.suffix else "")
        p.drawText(QRectF(r.left() + zone, r.top(), r.width() - 2 * zone, r.height()),
                   Qt.AlignCenter, text)
        p.end()


class SettingsAction(_GlassButton):
    """設定パネル内の曲別素材への入口。"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setText(text)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        paint_glass(p, self.inner(), BTN_RADIUS * self.scale, self.scale,
                    lit=self.lit() and self.isEnabled(), down=self.isDown(),
                    focus=self.hasFocus(), base_alpha=175, edge=TOP_EDGE)
        p.setFont(thin_font(21 * self.scale, QFont.DemiBold))
        p.setPen(QColor(235, 242, 250))
        p.drawText(self.inner(), Qt.AlignCenter, self.text())
        p.end()


class SettingsPanel(QFrame):
    """右から重ねる設定。後ろの並びは動かさない。"""

    closed = Signal()
    changed = Signal(str, bool)   # (設定キー, 値)
    #: 数の設定が変わった (設定キー, 値)。
    number_changed = Signal(str, int)
    #: 音量が変わった (設定キー, 0.0〜1.0)。保存と反映は PlayerHost。
    volume_changed = Signal(str, float)
    art_requested = Signal()

    #: 数の設定 (キー, 名前, 下限, 上限, 既定, 単位)。連打秒速は Editor の
    #: 「連打秒速」(roll_speed)と同じ値を共有する。次に譜面を読むときに効く。
    NUMBERS = (("roll_speed", "連打秒速", 1, 100, 45, "打/s"),)

    ROWS = (("player_show_jacket", "ジャケット"),
            ("player_show_bg", "背景"),
            ("player_show_lyric", "LYRIC"),
            ("player_show_fps", "FPS表示"))
    #: 既定が OFF の項目(ほかは ON)。
    DEFAULT_OFF = ("player_show_fps",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("diffSettings")
        self.scale = 1.0
        v = QVBoxLayout(self)
        self._v = v
        head = QHBoxLayout()
        self.lbl_head = QLabel("設定")
        self.lbl_head.setObjectName("setHead")
        head.addWidget(self.lbl_head)
        head.addStretch(1)
        self.btn_close = GlassCircle("close")
        self.btn_close.setToolTip("閉じる")
        self.btn_close.clicked.connect(self.closed)
        head.addWidget(self.btn_close)
        v.addLayout(head)
        self.grid = QGridLayout()
        v.addLayout(self.grid)
        self.btn_art = SettingsAction("背景・ジャケットを指定")
        self.btn_art.clicked.connect(self.art_requested)
        v.addWidget(self.btn_art)
        v.addStretch(1)
        self.labels = []
        lbl = QLabel("オート再生")
        lbl.setObjectName("setLabel")
        val = QLabel("ON（固定）")
        val.setObjectName("setValue")
        self.grid.addWidget(lbl, 0, 0)
        self.grid.addWidget(val, 0, 1, Qt.AlignRight | Qt.AlignVCenter)
        self.labels += [lbl, val]
        self.switches = {}
        for i, (key, text) in enumerate(self.ROWS, start=1):
            lbl = QLabel(text)
            lbl.setObjectName("setLabel")
            sw = ToggleSwitch()
            sw.toggled.connect(lambda on, k=key: self.changed.emit(k, on))
            self.grid.addWidget(lbl, i, 0)
            self.grid.addWidget(sw, i, 1, Qt.AlignRight | Qt.AlignVCenter)
            self.labels.append(lbl)
            self.switches[key] = sw
        self.steppers = {}
        self._number_defaults = {}
        row = len(self.ROWS) + 1
        for i, (key, text, lo, hi, default, unit) in enumerate(self.NUMBERS, start=row):
            lbl = QLabel(text)
            lbl.setObjectName("setLabel")
            st = Stepper(lo, hi, unit)
            st.valueChanged.connect(lambda v, k=key: self.number_changed.emit(k, v))
            self.grid.addWidget(lbl, i, 0)
            self.grid.addWidget(st, i, 1, Qt.AlignRight | Qt.AlignVCenter)
            self.labels.append(lbl)
            self.steppers[key] = st
            self._number_defaults[key] = default
        # 音量(全体・曲・効果音)。Editor と同じ設定キー。
        from neotja.player.volume import VOLUME_KEYS, VolumeSlider
        self.volume_sliders = {}
        row = len(self.ROWS) + len(self.NUMBERS) + 1
        for i, (key, text, _d) in enumerate(VOLUME_KEYS, start=row):
            lbl = QLabel(text)
            lbl.setObjectName("setLabel")
            sl = VolumeSlider()
            sl.valueChanged.connect(lambda v, k=key: self.volume_changed.emit(k, v / 100.0))
            self.grid.addWidget(lbl, i, 0)
            self.grid.addWidget(sl, i, 1, Qt.AlignRight | Qt.AlignVCenter)
            self.labels.append(lbl)
            self.volume_sliders[key] = sl

    def set_volume_values(self, cfg):
        from neotja.player.volume import level_of
        for key, sl in self.volume_sliders.items():
            sl.setValue(level_of(cfg, key) * 100.0)

    def set_values(self, cfg):
        for key, sw in self.switches.items():
            sw.blockSignals(True)
            sw.setChecked(bool(cfg.get(key, key not in self.DEFAULT_OFF)))
            sw.blockSignals(False)
            sw.update()
        for key, st in self.steppers.items():
            st.setValue(cfg.get(key, self._number_defaults[key]))
        self.set_volume_values(cfg)

    def apply_scale(self, s):
        self.scale = s
        self._v.setContentsMargins(int(38 * s), int(30 * s), int(30 * s), int(30 * s))
        self._v.setSpacing(int(18 * s))
        self.grid.setVerticalSpacing(int(10 * s))
        self.grid.setColumnStretch(0, 1)
        f = thin_font(30 * s, QFont.DemiBold)
        self.lbl_head.setFont(f)
        for lbl in self.labels:
            lbl.setFont(thin_font(25 * s, QFont.DemiBold))
        self.btn_close.scale = s
        self.btn_close.setFixedSize(int(48 * s + 2 * GLOW * s), int(48 * s + 2 * GLOW * s))
        self.btn_art.scale = s
        self.btn_art.setFixedHeight(int(64 * s))
        self.btn_art.update()
        for sw in (list(self.switches.values()) + list(self.steppers.values())
                   + list(self.volume_sliders.values())):
            sw.scale = s
            sw.setFixedSize(sw.sizeHint())

    def paintEvent(self, ev):
        """すりガラス風の板。上から下へ少し濃く、左の縁に細い光。
        項目のあいだに薄い区切り線。"""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(12, 24, 44, 222))
        p.setPen(QPen(TOP_EDGE, 1.3))
        p.drawLine(0, 0, 0, self.height())
        # 項目の区切り(ラベルの行のあいだ)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        rows = [lbl for lbl in self.labels if lbl.objectName() == "setLabel"]
        m = int(38 * self.scale)
        for a, b in zip(rows, rows[1:]):
            y = (a.geometry().bottom() + b.geometry().top()) // 2
            p.drawLine(m, y, self.width() - m, y)
        p.end()


# ----------------------------------------------------------------------
class DifficultyPage(QWidget):
    """難易度選択。set_song で曲を流し込む。"""

    back_requested = Signal()
    play_requested = Signal(str, str)     # (tja, コースキー)
    record_requested = Signal(str, str)
    art_requested = Signal(str, str)
    #: 操作音の合図。"don"(Play)/ "pi"(それ以外: カード・戻る・設定・録画…)。
    sound_requested = Signal(str)

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("difficultyPage")
        self.cfg = config_data
        self._save_cb = save_cb
        self.path = ""
        self.title = ""
        self.subtitle = ""
        self.levels = {}           # コースキー -> レベル(None 可)
        self.selected = None       # SLOTS の名前
        self.oni_side = "Oni"      # おにの枠でいま見せているもの(Oni / Edit)
        self._jacket = None
        self._bg = None
        self._video_bg = None
        self._bg_scaled = None
        self.scale = 1.0
        self.setFocusPolicy(Qt.StrongFocus)

        self.btn_back = GlassPill("戻る", "back", self)
        self.btn_back.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_back.clicked.connect(self.back_requested)
        self.btn_settings = GlassPill("設定", "gear", self)
        self.btn_settings.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_settings.clicked.connect(self.toggle_settings)

        self.cards = {}
        for slot in SLOTS:
            c = CourseCard(slot, self)
            # 押せるのは譜面にあるコースだけ(無いカードは clicked を出さない)。
            c.clicked.connect(lambda _slot: self.sound_requested.emit("pi"))
            c.clicked.connect(self.select_slot)
            self.cards[slot] = c
        self.btn_swap = SwapButton(self)
        self.btn_swap.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_swap.clicked.connect(self.toggle_ura)
        self.btn_play = ActionButton("Play", "play", self)
        self.btn_play.clicked.connect(lambda: self.sound_requested.emit("don"))
        self.btn_play.clicked.connect(
            lambda: self.current_course() and self.play_requested.emit(self.path, self.current_course()))
        self.btn_record = ActionButton("録画", "record", self)
        self.btn_record.clicked.connect(lambda: self.sound_requested.emit("pi"))
        self.btn_record.clicked.connect(
            lambda: self.current_course() and self.record_requested.emit(self.path, self.current_course()))
        self.lbl_auto = QLabel("オート再生", self)
        self.lbl_auto.setObjectName("autoBadge")
        self.lbl_auto.setAlignment(Qt.AlignCenter)

        self.settings = SettingsPanel(self)
        self.settings.hide()
        self.settings.closed.connect(lambda: self.sound_requested.emit("pi"))
        self.settings.closed.connect(self.close_settings)
        self.settings.changed.connect(lambda *_: self.sound_requested.emit("pi"))
        self.settings.changed.connect(self._on_setting)
        self.settings.number_changed.connect(self._on_number_setting)
        self.settings.art_requested.connect(lambda: self.sound_requested.emit("pi"))
        self.settings.art_requested.connect(
            lambda: self.current_course() and self.art_requested.emit(self.path, self.current_course()))
        self._anim = QPropertyAnimation(self.settings, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)
        self._closing = False
        self._number_save = QTimer(self)
        self._number_save.setSingleShot(True)
        self._number_save.setInterval(400)
        self._number_save.timeout.connect(self._flush_number_save)

        self.setStyleSheet(self._qss(1.0))

    # ------------------------------------------------------------------
    # 曲
    def set_song(self, path):
        self.path = path
        info = song_lists.song_info(path)
        self.title = info.get("title", "")
        self.subtitle = info.get("subtitle", "")
        self.levels = dict(info.get("courses") or [])
        has_oni, has_ura = "Oni" in self.levels, "Edit" in self.levels
        self.oni_side = "Edit" if (has_ura and not has_oni) else "Oni"
        # 最初は おに(裏だけなら裏)。おにの枠が空なら、入っている中で難しいもの。
        if has_oni or has_ura:
            self.selected = "Oni"
        else:
            have = [s for s in SLOTS if s in self.levels]
            self.selected = have[-1] if have else None
        self._load_art()
        self.settings.set_values(self.cfg)
        self._sync()
        self.update()

    def _load_art(self):
        def load(kind):
            p = song_lists.existing_art(self.cfg, self.path, kind)
            if not p:
                return None
            pm = QPixmap(p)
            return None if pm.isNull() else pm
        self._jacket = load("jacket")
        self._bg = load("background")
        self._video_bg = None
        self._bg_scaled = None

    def set_video_frame(self, image):
        """GUIスレッドで受け取った無音動画のフレームを表示する。

        再生時計・音声・古い要求の破棄は呼び出し側が管理する。
        静止画像は残し、動画終了時に同じ背景へ戻せるようにする。
        """
        if image is None or image.isNull():
            return
        self._video_bg = QPixmap.fromImage(image)
        self._bg_scaled = None
        if self.show_bg():
            self.update()

    def clear_video_frame(self):
        """動画の表示だけを解除し、読み込み済みの静止背景へ戻す。"""
        if self._video_bg is not None:
            self._video_bg = None
            self._bg_scaled = None
            self.update()

    # ------------------------------------------------------------------
    # コース
    def slot_key(self, slot):
        return self.oni_side if slot == "Oni" else slot

    def slot_available(self, slot):
        return self.slot_key(slot) in self.levels

    def has_ura_toggle(self):
        return "Oni" in self.levels and "Edit" in self.levels

    def current_course(self):
        if self.selected is None or not self.slot_available(self.selected):
            return None
        return self.slot_key(self.selected)

    def select_slot(self, slot):
        if slot in SLOTS and self.slot_available(slot):
            self.selected = slot
            self._sync()

    def toggle_ura(self):
        """おに ⇔ 裏。対象もおにの枠へ移す。"""
        if not self.has_ura_toggle():
            return
        self.oni_side = "Edit" if self.oni_side == "Oni" else "Oni"
        self.selected = "Oni"
        self._sync()

    def _sync(self):
        for slot, card in self.cards.items():
            key = self.slot_key(slot)
            card.set_state(key, key in self.levels, self.levels.get(key),
                           slot == self.selected)
        self.btn_swap.setVisible(self.has_ura_toggle())
        ok = self.current_course() is not None
        self.btn_play.setEnabled(ok)
        self.btn_record.setEnabled(ok)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Escape:
            if self.settings.isVisible():
                self.close_settings()
            else:
                self.back_requested.emit()
            return
        if k in (Qt.Key_Left, Qt.Key_Right):
            have = [s for s in SLOTS if self.slot_available(s)]
            if have:
                i = have.index(self.selected) if self.selected in have else 0
                i = max(0, min(len(have) - 1, i + (1 if k == Qt.Key_Right else -1)))
                self.select_slot(have[i])
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------
    # 設定
    def settings_open(self):
        return self.settings.isVisible() and not self._closing

    def toggle_settings(self):
        if self.settings_open():
            self.close_settings()
        else:
            self.open_settings()

    def open_settings(self):
        self.settings.set_values(self.cfg)
        self._closing = False
        r = self._settings_rect()
        self.settings.setGeometry(r)
        self.settings.show()
        self.settings.raise_()
        # 半透明の板の後ろに「設定」が透けないように。
        self.btn_settings.hide()
        self._anim.stop()
        self._anim.setStartValue(QPoint(self.width(), 0))
        self._anim.setEndValue(r.topLeft())
        self._anim.start()

    def close_settings(self):
        if not self.settings.isVisible():
            return
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(self.settings.pos())
        self._anim.setEndValue(QPoint(self.width(), 0))
        self._anim.start()

    def _on_anim_done(self):
        if self._closing:
            self.settings.hide()
            self._closing = False
            self.btn_settings.show()

    def _on_setting(self, key, value):
        self.cfg[key] = bool(value)
        try:
            self._save_cb()
        except Exception:  # noqa: BLE001
            pass
        self.update()

    def _on_number_setting(self, key, value):
        """数の設定(連打秒速)。保存だけして、効くのは次に譜面を読むとき。
        押し続けて連続で変わるあいだは、保存をまとめて1回にする。"""
        self.cfg[key] = int(value)
        self._number_save.start()

    def _flush_number_save(self):
        try:
            self._save_cb()
        except Exception:  # noqa: BLE001
            pass

    def show_jacket(self):
        return bool(self.cfg.get("player_show_jacket", True))

    def show_bg(self):
        return bool(self.cfg.get("player_show_bg", True))

    # ------------------------------------------------------------------
    # 配置
    def _s(self):
        return max(0.55, min(self.width() / BASE_W, self.height() / BASE_H))

    def panel_top(self):
        return self.height() - 278 * self._s()

    def jacket_rect(self):
        s = self._s()
        return QRectF(80 * s, 112 * s, 190 * s, 190 * s)

    def _settings_rect(self):
        s = self._s()
        w = int(min(self.width() * 0.4, 480 * s))
        return QRect(self.width() - w, 0, w, self.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        s = self._s()
        self.scale = s
        w, h = self.width(), self.height()
        top = self.panel_top()
        ph = h - top
        cw, ch = 236 * s, 232 * s
        gap = 18 * s
        x = 84 * s
        y = top + (ph - ch) / 2 + 4 * s
        for slot in SLOTS:
            c = self.cards[slot]
            c.scale = s
            c.setGeometry(QRect(int(x), int(y), int(cw), int(ch)))
            x += cw + gap
        oni = self.cards["Oni"].geometry()
        d = max(48, int(56 * s))   # 押せる大きさは小さい窓でも 48px 以上
        pad = 8 * s
        cx = oni.right() - pad - 6 * s
        cy = oni.top() + pad + 2 * s
        self.btn_swap.scale = s
        # 光のぶん外側に余白を足す(押せる範囲は丸の中 + 余白)。
        gd = int(d + 2 * GLOW * s)
        self.btn_swap.setGeometry(int(cx - gd / 2), int(cy - gd / 2), gd, gd)
        self.btn_swap.raise_()
        # プレイ(主)は大きく、録画(従)は低く。カードの見えている高さの
        # 真ん中にそろえる。光のぶんだけウィジェットを外へ広げる。
        bw = 400 * s
        h_play, h_rec, gap2 = 100 * s, 68 * s, 16 * s
        g = ActionButton.GLOW * s
        bx = w - 70 * s - bw
        card_mid = y + ch / 2
        by = card_mid - (h_play + gap2 + h_rec) / 2
        for b, yy, hh in ((self.btn_play, by, h_play),
                          (self.btn_record, by + h_play + gap2, h_rec)):
            b.scale = s
            b.setGeometry(int(bx - g), int(yy - g), int(bw + 2 * g), int(hh + 2 * g))
        self.lbl_auto.setGeometry(int(32 * s), int(top - 46 * s), int(108 * s), int(34 * s))
        self.lbl_auto.setFont(thin_font(17 * s, QFont.DemiBold))
        gl = GLOW * s
        for b, align in ((self.btn_back, "left"), (self.btn_settings, "right")):
            b.scale = s
            hint = b.sizeHint()
            bx2 = int(28 * s - gl) if align == "left" else int(w - 28 * s + gl - hint.width())
            b.setGeometry(bx2, int(24 * s - gl), hint.width(), hint.height())
        self.settings.apply_scale(s)
        self.setStyleSheet(self._qss(s))
        if self.settings.isVisible():
            self.settings.setGeometry(self._settings_rect())
        self.settings.raise_()
        self._bg_scaled = None

    # ------------------------------------------------------------------
    # 描画
    def _bg_pixmap(self):
        source = self._video_bg if self._video_bg is not None else self._bg
        if source is None:
            return None
        size = self.size()
        if self._bg_scaled is None or self._bg_scaled.size() != size:
            scaled = source.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (scaled.width() - size.width()) // 2
            y = (scaled.height() - size.height()) // 2
            self._bg_scaled = scaled.copy(x, y, size.width(), size.height())
        return self._bg_scaled

    def paintEvent(self, ev):
        s = self._s()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        bg = self._bg_pixmap() if self.show_bg() else None
        if bg is not None:
            p.drawPixmap(0, 0, bg)
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, NAVY.lighter(108))
            grad.setColorAt(1, NAVY.darker(115))
            p.fillRect(self.rect(), QBrush(grad))
        # 上は文字が読めるよう少し暗く
        top = QLinearGradient(0, 0, 0, 360 * s)
        top.setColorAt(0, QColor(0, 0, 0, 120 if bg is not None else 40))
        top.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w, 360 * s), QBrush(top))

        # 下の帯
        pt = self.panel_top()
        band = QLinearGradient(0, pt, 0, h)
        band.setColorAt(0, QColor(16, 30, 54, 225))
        band.setColorAt(1, QColor(10, 20, 38, 240))
        p.fillRect(QRectF(0, pt, w, h - pt), QBrush(band))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawLine(QPointF(0, pt), QPointF(w, pt))

        # ジャケット
        jr = self.jacket_rect()
        # ジャケット OFF も、絵が無いときと同じ No Image の枠(利用者の指定)。
        if self._jacket is not None and self.show_jacket():
            shadow = QColor(0, 0, 0, 90)
            p.setPen(Qt.NoPen)
            p.setBrush(shadow)
            p.drawRoundedRect(jr.translated(0, 6 * s), 6 * s, 6 * s)
            p.drawPixmap(jr, self._jacket, QRectF(self._jacket.rect()))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(jr)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.6 * s))
            p.setBrush(QColor(10, 18, 32, 150))
            p.drawRect(jr)
            p.setFont(thin_font(30 * s, QFont.DemiBold))
            p.setPen(QColor(235, 240, 248))
            p.drawText(jr, Qt.AlignCenter, "No\nImage")

        # 曲名・副題(ジャケットの有無で位置を変えない)
        tx = jr.right() + 36 * s
        avail = w - tx - 60 * s
        size = 64 * s
        f = title_font(size)
        while QFontMetricsF(f).horizontalAdvance(self.title) > avail and size > 30 * s:
            size -= 2 * s
            f = title_font(size)
        text = QFontMetricsF(f).elidedText(self.title, Qt.ElideRight, avail)
        draw_outlined(p, text, f, tx, jr.top() + 92 * s, 9 * s)
        if self.subtitle:
            sf = title_font(28 * s)
            sub = QFontMetricsF(sf).elidedText(self.subtitle, Qt.ElideRight, avail)
            draw_outlined(p, sub, sf, tx, jr.top() + 142 * s, 5 * s)
        p.end()

    @staticmethod
    def _qss(s):
        ui = ui_family()
        px = lambda v: max(8, int(round(v * s)))
        fam = ('font-family: "%s";' % ui) if ui else ""
        return f"""
        QLabel#autoBadge {{ background-color: rgba(108,116,128,225); color: #ffffff;
                           border: 1px solid rgba(255,255,255,95); border-radius: {px(4)}px; {fam}
                           font-size: {px(16)}px; font-weight: 600; letter-spacing: 1px; }}
        QFrame#diffSettings QLabel {{ color: #ffffff; background: transparent; {fam}
                                     font-size: {px(24)}px; font-weight: 600; }}
        QLabel#setHead {{ font-size: {px(30)}px; letter-spacing: 2px; }}
        QLabel#setValue {{ color: rgba(255,255,255,190); }}
        """
