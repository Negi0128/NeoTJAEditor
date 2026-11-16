"""Player の再生画面。PlayerCore の PreviewDock が持っている再生ウィンドウ
(GamePreviewWindow)を、そのまま自分の中へ置く。

見せるのはゲーム画面の絵だけ(set_view_only — 下の速度・波形・情報と、
レーンの上のボタン類は隠す)。大きさはこの画面に収まる最大の倍率、余りは黒で
真ん中に置く。

通常プレイでは止める・巻き戻す・飛ばすをさせない(set_controls_locked —
共通部品の側にある明示の締め切り。Editor は締めない)。締めているあいだの
キーはここへ回ってくる(handle_key)。Esc で途中メニュー(pause_menu.PauseMenu)
を出す合図を出す。止める・流すは PlayerHost がやる。
"""

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from neotja.player.pause_menu import PauseMenu


class PlayPage(QWidget):
    #: 通常プレイ中に Esc が押された(メニューを開く合図)。
    esc_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playPage")
        self.setFocusPolicy(Qt.StrongFocus)
        self.gpw = None
        #: ゲーム画面が GL の窓(native)か。FadeStack はこれを見て、上に黒を
        #: 重ねられないときは真っ黒から切り替えるだけにする。
        self.native_gl = False
        self.menu = PauseMenu(self)
        #: 詳細モード(下に作譜ペイン・操作できる)か。
        self.detail = False

    def attach(self, gpw, native=False, key_cb=None):
        """再生ウィンドウをこの画面の子にする(1回だけ)。操作は締める。"""
        if self.gpw is not gpw:
            gpw.setParent(self, Qt.Widget)
            gpw.installEventFilter(self)
            self.gpw = gpw
        self.native_gl = bool(native)
        self.detail = False
        self.menu.set_detail_mode(False)
        gpw.set_view_only(True)
        lock = getattr(gpw, "set_controls_locked", None)
        if lock is not None:
            lock(True, key_cb or self.handle_key)
        self._fit()
        gpw.show()
        self.menu.raise_()

    def set_detail(self, on):
        """詳細モード ⇔ 通常プレイ。詳細では下に作譜ペイン(と時刻・シーク
        バー・速度の行)を出し、止める・動かすを許す。Esc はどちらでも
        メニュー(締めていないあいだも Esc だけはこちらへ回してもらう)。"""
        self.detail = bool(on)
        self.menu.set_detail_mode(self.detail)
        g = self.gpw
        if g is None:
            return
        try:
            g.set_view_only(True, with_bottom=self.detail)
        except TypeError:
            g.set_view_only(True)
        lock = getattr(g, "set_controls_locked", None)
        if lock is not None:
            lock(not self.detail, self.handle_key)
        self._fit()

    def unlock(self):
        """再生画面を離れるとき。締め切りを外す(次に置くときにまた締める)。"""
        g = self.gpw
        lock = getattr(g, "set_controls_locked", None) if g is not None else None
        if lock is not None:
            lock(False)

    def detach_hide(self):
        if self.gpw is not None:
            self.gpw.hide()

    # ------------------------------------------------------------------
    # 途中メニュー
    def show_menu(self, backdrop=None):
        """メニューを出す。backdrop は止めた瞬間の画面(GL の窓のときだけ。
        native な窓の上ではふつうのウィジェットが透けないので、絵を敷く)。"""
        rect = None
        if self.native_gl:
            # GL の窓より前に出すには、メニュー自身も native にする。
            self.menu.setAttribute(Qt.WA_NativeWindow, True)
            if self.gpw is not None:
                rect = QRectF(self.gpw.geometry())
                sh = getattr(self.gpw, "scaled_host", None)
                if sh is not None:
                    # 絵はゲーム画面ぶんだけ(詳細モードでは下に作譜ペインがある)。
                    rect = QRectF(QRect(sh.mapTo(self, QPoint(0, 0)), sh.size()))
        self.menu.open(backdrop, rect)

    def hide_menu(self):
        self.menu.close_menu()

    def menu_open(self):
        return self.menu.is_open()

    def handle_key(self, ev):
        """締めているレーン・この画面に届いたキー。Esc だけが意味を持つ。"""
        if self.menu.is_open():
            self.menu.keyPressEvent(ev)
            return
        if ev.key() == Qt.Key_Escape and not ev.isAutoRepeat():
            self.esc_pressed.emit()
        ev.accept()

    def keyPressEvent(self, ev):
        self.handle_key(ev)

    def keyReleaseEvent(self, ev):
        ev.accept()

    def wheelEvent(self, ev):
        ev.accept()          # 余白で回しても何も起きない

    def mousePressEvent(self, ev):
        self.setFocus(Qt.MouseFocusReason)
        ev.accept()

    # ------------------------------------------------------------------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()
        if self.menu.is_open():
            self.menu.setGeometry(self.rect())

    def eventFilter(self, obj, ev):
        # 再生ウィンドウが自分で大きさを取り直したら(モード・打音表記の
        # 切り替えなど)、真ん中へ置き直す。
        if obj is self.gpw and ev.type() == QEvent.Resize:
            self._center()
        return False

    def _center(self):
        g = self.gpw
        if g is not None:
            g.move(max(0, (self.width() - g.width()) // 2),
                   max(0, (self.height() - g.height()) // 2))

    def _fit(self):
        g = self.gpw
        if g is None or self.width() <= 0 or self.height() <= 0:
            return
        g.fit_view_only(self.width(), self.height())
        self._center()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        p.end()
