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

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal, QTimer
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
        self._pause_game_frame = None
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setSingleShot(True)
        self._backdrop_timer.timeout.connect(self._refresh_menu_backdrop)
        #: ゲーム画面を返す呼び出し(フェードの黒を画面自身に描いてもらう)。
        self._screen_fn = None

    # ------------------------------------------------------------------
    # フェードの黒(transitions.FadeStack が使う)。GL の窓の上にはふつうの
    # ウィジェットを重ねられないので、ゲーム画面自身に黒を描いてもらう。
    def _dim_screen(self):
        fn = self._screen_fn
        try:
            s = fn() if fn is not None else None
        except Exception:  # noqa: BLE001
            s = None
        return s if s is not None and hasattr(s, "set_fade_dim") else None

    def supports_fade_dim(self):
        return self.gpw is not None and self._dim_screen() is not None

    def set_fade_dim(self, v):
        s = self._dim_screen()
        if s is not None:
            s.set_fade_dim(v)

    def adopt(self, gpw):
        """再生ウィンドウを先に子にしておく(見せない)。attach は付け替えを省く。"""
        if self.gpw is gpw or gpw is None:
            return
        gpw.setParent(self, Qt.Widget)
        gpw.installEventFilter(self)
        gpw.hide()
        self.gpw = gpw

    def attach(self, gpw, native=False, key_cb=None, screen_fn=None):
        """再生ウィンドウをこの画面の子にする(1回だけ)。操作は締める。
        screen_fn はゲーム画面を返す呼び出し(フェードの黒に使う)。"""
        if screen_fn is not None:
            self._screen_fn = screen_fn
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
        self._pause_game_frame = backdrop
        if self.native_gl:
            self.menu.setAttribute(Qt.WA_NativeWindow, True)
            backdrop, rect = self._compose_pause_backdrop()
        self.menu.open(backdrop, rect)

    def _compose_pause_backdrop(self):
        """Qtの作譜ペインとGPU停止画像を同じ画面へ合成する。"""
        g = self.gpw
        if g is None:
            return None, None
        # grabだけではnative GLが抜けるため、保存済みGPUフレームで補う。
        image = g.grab().toImage()
        frame = self._pause_game_frame
        if frame is not None and not frame.isNull():
            sh = getattr(g, "scaled_host", None)
            target = (QRectF(QRect(sh.mapTo(g, QPoint(0, 0)), sh.size()))
                      if sh is not None else QRectF(g.rect()))
            painter = QPainter(image)
            painter.drawImage(target, frame)
            painter.end()
        return image, QRectF(g.geometry())

    def _refresh_menu_backdrop(self):
        if self.native_gl and self.menu.is_open():
            image, rect = self._compose_pause_backdrop()
            self.menu.backdrop = image
            self.menu.backdrop_rect = rect
            self.menu.update()

    def hide_menu(self):
        self._backdrop_timer.stop()
        self._pause_game_frame = None
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
            self._backdrop_timer.start(0)

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
            if self.menu.is_open():
                self._backdrop_timer.start(0)

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
