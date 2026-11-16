"""Player の再生画面。PlayerCore の PreviewDock が持っている再生ウィンドウ
(GamePreviewWindow)を、そのまま自分の中へ置く。

見せるのはゲーム画面の絵だけ(set_view_only — 下の速度・波形・情報と、
レーンの上のボタン類は隠す)。大きさはこの画面に収まる最大の倍率、余りは黒で
真ん中に置く。操作の制限と Esc のメニューは次の段で足す。
"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class PlayPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playPage")
        self.gpw = None
        #: ゲーム画面が GL の窓(native)か。FadeStack はこれを見て、上に黒を
        #: 重ねられないときは真っ黒から切り替えるだけにする。
        self.native_gl = False

    def attach(self, gpw, native=False):
        """再生ウィンドウをこの画面の子にする(1回だけ)。"""
        if self.gpw is not gpw:
            gpw.setParent(self, Qt.Widget)
            gpw.installEventFilter(self)
            self.gpw = gpw
        self.native_gl = bool(native)
        gpw.set_view_only(True)
        self._fit()
        gpw.show()

    def detach_hide(self):
        if self.gpw is not None:
            self.gpw.hide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

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
