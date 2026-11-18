"""通常プレイの途中メニュー(Esc)。v15-design/PLAYER_IMPLEMENTATION_PLAN.md
「通常プレイと途中メニュー」。

項目はちょうど4つで、説明の文は出さない(見出しの「一時停止」だけ)。
  1 演奏を続ける / 2 初めからやり直す / 3 詳細モードに移行する / 4 選曲画面にもどる

見た目は難易度選択(difficulty.py)と同じ平らな板: 暗い半透明の塗り、細い
薄シアンの縁、ほぼ四角の角、太めの UI 書体、選んでいる項目はシアンの縁。

操作: ↑↓ で選んで Enter、数字 1〜4 で直接、Esc は「続ける」。マウスで押しても
よい。動かしたとき・決めたときに「ピ」(sound_requested)。

止める・流す・画面を替えるのはここではしない。決まった項目を chosen で
知らせるだけ(PlayerHost がやる)。
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from neotja.player.difficulty import (BASE_H, BASE_W, BTN_RADIUS, SELECT_COLOR,
                                      TOP_EDGE, paint_glass, thin_font)
from neotja.player.volume import VOLUME_KEYS, VolumeRows

#: (合図, 表記)。並びと数字キーはこの順。
ITEMS = (
    ("resume", "演奏を続ける"),
    ("restart", "初めからやり直す"),
    ("detail", "詳細モードに移行する"),
    ("library", "選曲画面にもどる"),
)
#: 詳細モードのあいだの3番目の表記(次の段で使う)。
DETAIL_BACK_LABEL = "通常演奏に移行する"

#: 倍率 1 のときの寸法(px)。
PANEL_W = 600
ITEM_H = 74
ITEM_GAP = 14
PAD = 34
HEAD_H = 44
#: 項目の下の音量の段(1段の高さ・上の余白)。
VOL_ROW_H = 56
VOL_GAP = 26


class _MenuItem(QAbstractButton):
    """項目1つ。フォーカスは取らない(キーはメニューがまとめて受ける)。"""

    def __init__(self, number, text, menu):
        super().__init__(menu)
        self.number = number
        self.setText(text)
        self.menu = menu
        self.scale = 1.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)

    def enterEvent(self, ev):
        if self.isEnabled():
            self.menu.set_current(self.number - 1, sound=False)
        super().enterEvent(ev)

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.38)
        cur = self.isEnabled() and self.menu.current == self.number - 1
        r = paint_glass(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), BTN_RADIUS * s, s,
                        lit=cur, down=self.isDown(), focus=cur, base_alpha=175,
                        edge=QColor(220, 228, 238, 150))
        # 数字の箱(左)。
        box = 40 * s
        bx = r.left() + 22 * s
        by = r.center().y() - box / 2
        p.setPen(QPen(QColor(SELECT_COLOR) if cur else QColor(TOP_EDGE), 1.4 * s))
        p.setBrush(QColor(127, 227, 255, 40) if cur else Qt.NoBrush)
        p.drawRect(QRectF(bx, by, box, box))
        nf = thin_font(22 * s, QFont.Bold)
        p.setFont(nf)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(bx, by, box, box), Qt.AlignCenter, str(self.number))
        f = thin_font(27 * s, QFont.Bold if cur else QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3 * s)
        p.setFont(f)
        p.drawText(QRectF(bx + box + 26 * s, r.top(), r.width(), r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


class PauseMenu(QWidget):
    """再生画面の上に重ねる途中メニュー。親いっぱいに広がり、真ん中に板。"""

    #: 決まった項目の合図(ITEMS の1つ目)。
    chosen = Signal(str)
    #: 操作音("pi")。
    sound_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pauseMenu")
        self.setFocusPolicy(Qt.StrongFocus)
        self.scale = 1.0
        self.current = 0
        #: 止めた瞬間の画面(GL の窓の上では透けないので、その代わりに敷く)。
        self.backdrop = None
        self.backdrop_rect = None
        self.items = []
        for i, (key, text) in enumerate(ITEMS):
            it = _MenuItem(i + 1, text, self)
            it.key = key
            it.clicked.connect(lambda _=False, k=key: self._click(k))
            self.items.append(it)
        #: 音量(全体・曲・効果音)。マウスで動かす(キーは項目の選択に使う)。
        #: 動かしても止めたまま(続けるのは「演奏を続ける」だけ)。
        self.volume = VolumeRows(self, focusable=False, label_px=22)
        self._panel = QRectF()
        self.hide()

    # ------------------------------------------------------------------
    def keys(self):
        return [it.key for it in self.items]

    def item(self, key):
        for it in self.items:
            if it.key == key:
                return it
        return None

    def set_item_enabled(self, key, on):
        it = self.item(key)
        if it is None:
            return
        it.setEnabled(bool(on))
        if not on and self.current == self.items.index(it):
            self._step(1, sound=False)
        self.update()

    def set_detail_mode(self, detail):
        """3番目の表記を「詳細モードに移行する」⇔「通常演奏に移行する」。"""
        it = self.item("detail")
        if it is not None:
            it.setText(DETAIL_BACK_LABEL if detail else ITEMS[2][1])
            it.update()

    def open(self, backdrop=None, backdrop_rect=None):
        """出す。いつも「演奏を続ける」から。"""
        self.backdrop = backdrop
        self.backdrop_rect = backdrop_rect
        self.current = 0
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())
        self._layout()
        self.show()
        self.raise_()
        self.setFocus(Qt.OtherFocusReason)
        self.update()

    def close_menu(self):
        self.hide()
        self.backdrop = None
        self.backdrop_rect = None

    def is_open(self):
        return self.isVisible()

    # ------------------------------------------------------------------
    def set_current(self, index, sound=True):
        if not (0 <= index < len(self.items)) or not self.items[index].isEnabled():
            return
        if index != self.current:
            self.current = index
            if sound:
                self.sound_requested.emit("pi")
            for it in self.items:
                it.update()

    def _step(self, d, sound=True):
        n = len(self.items)
        i = self.current
        for _ in range(n):
            i = (i + d) % n
            if self.items[i].isEnabled():
                self.set_current(i, sound=sound)
                return

    def choose(self, key):
        it = self.item(key)
        if it is None or not it.isEnabled():
            return
        self.sound_requested.emit("pi")
        self.chosen.emit(key)

    def _click(self, key):
        self.choose(key)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Escape:
            self.choose("resume")
        elif k == Qt.Key_Up:
            self._step(-1)
        elif k == Qt.Key_Down:
            self._step(1)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.choose(self.items[self.current].key)
        elif Qt.Key_1 <= k <= Qt.Key_9:
            i = k - Qt.Key_1
            if i < len(self.items):
                self.choose(self.items[i].key)
        # それ以外のキーは飲み込む(下の再生画面へ流さない)。
        ev.accept()

    def keyReleaseEvent(self, ev):
        ev.accept()

    def wheelEvent(self, ev):
        ev.accept()

    def mousePressEvent(self, ev):
        ev.accept()       # 板の外を押しても何も起きない(閉じもしない)

    # ------------------------------------------------------------------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout()

    def _layout(self):
        s = max(0.5, min(self.width() / float(BASE_W), self.height() / float(BASE_H)))
        self.scale = s
        n = len(self.items)
        pw = PANEL_W * s
        vol_h = len(VOLUME_KEYS) * VOL_ROW_H * s
        ph = (PAD * s * 2 + HEAD_H * s + ITEM_GAP * s + n * ITEM_H * s + (n - 1) * ITEM_GAP * s
              + VOL_GAP * s + vol_h)
        x = (self.width() - pw) / 2.0
        y = (self.height() - ph) / 2.0
        self._panel = QRectF(x, y, pw, ph)
        iy = y + PAD * s + HEAD_H * s + ITEM_GAP * s
        for it in self.items:
            it.scale = s
            it.setGeometry(int(x + PAD * s), int(iy), int(pw - 2 * PAD * s), int(ITEM_H * s))
            iy += (ITEM_H + ITEM_GAP) * s
        self._vol_line = iy - ITEM_GAP * s + VOL_GAP * s / 2
        self.volume.apply_scale(s)
        self.volume.setGeometry(int(x + PAD * s), int(iy - ITEM_GAP * s + VOL_GAP * s),
                                int(pw - 2 * PAD * s), int(vol_h))

    def paintEvent(self, ev):
        s = self.scale
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.backdrop is not None:
            p.fillRect(self.rect(), QColor(0, 0, 0))
            r = self.backdrop_rect or QRectF(self.rect())
            p.drawImage(QRectF(r), self.backdrop)
        # 後ろ(止まった再生画面)を暗く沈める。
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        paint_glass(p, self._panel, BTN_RADIUS * s, s, base_alpha=205, edge=TOP_EDGE)
        f = thin_font(24 * s, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 6 * s)
        p.setFont(f)
        p.setPen(QColor(230, 244, 250))
        head = QRectF(self._panel.left() + PAD * s, self._panel.top() + PAD * s,
                      self._panel.width() - 2 * PAD * s, HEAD_H * s)
        p.drawText(head, Qt.AlignLeft | Qt.AlignVCenter, "一時停止")
        # 見出しの下の細い線。
        ly = head.bottom() + ITEM_GAP * s / 2
        p.setPen(QPen(QColor(150, 215, 235, 90), 1.0 * max(1.0, s)))
        p.drawLine(int(head.left()), int(ly), int(head.right()), int(ly))
        # 項目と音量のあいだの線。
        vy = getattr(self, "_vol_line", None)
        if vy is not None:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0 * max(1.0, s)))
            p.drawLine(int(head.left()), int(vy), int(head.right()), int(vy))
        p.end()
