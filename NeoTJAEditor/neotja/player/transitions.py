"""画面の切り替えのフェード。PlayerHost の画面の重ね(QStackedWidget)の代わりに使う。

- cross … いまの画面を1枚の絵に撮って上に重ね、新しい画面へ切り替えてから
  その絵を薄くしていく(クロスフェード)。
- black … いまの画面の上で黒を濃くし、真っ黒になったところで切り替え、
  黒を薄くしていく。新しい画面が GL の窓(native)だと、ふつうのウィジェットを
  上に重ねても OS の窓の下に沈んで見えないので、そのときは真っ黒から
  そのまま切り替える(黒からの出は無し)。

あとで結果画面などの切り替えにもそのまま使う。
"""

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget


class _FadeOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.pixmap = None
        self.opacity = 0.0
        self.hide()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setOpacity(max(0.0, min(1.0, self.opacity)))
        if self.pixmap is not None:
            p.drawPixmap(self.rect(), self.pixmap)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0))
        p.end()

    # 切り替えのあいだは押せない(重ねた絵の下の画面へ届かせない)。
    def mousePressEvent(self, ev):
        ev.accept()


class FadeStack(QStackedWidget):
    """フェードで切り替えられる QStackedWidget。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay = _FadeOverlay(self)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)
        self._phase = None
        self._target = None
        self._done_cb = None
        self._ms = 350

    def is_fading(self):
        return self._phase is not None

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._overlay.setGeometry(self.rect())

    # ------------------------------------------------------------------
    # 出す前に大きさを決める。QStackedWidget は**いま出ている画面だけ**に
    # 大きさを渡すので、初めて出す画面は前の(既定の)大きさのまま1コマ
    # 塗られ、resizeEvent で置く部品(結果画面のボタンなど)が一瞬ずれて
    # 見えていた。切り替える前・絵に撮る前に、ここで大きさと中の配置を
    # 済ませておく。
    def presize(self, widget):
        if widget is None:
            return
        r = self.contentsRect()
        if r.width() <= 0 or r.height() <= 0:
            return
        if widget.geometry() != r:
            widget.setGeometry(r)
        if widget.testAttribute(Qt.WA_PendingResizeEvent):
            # 隠れている部品の resizeEvent は「出す直前」まで待たされる。
            # 配置をここで確定させるため、今すぐ届ける。
            QApplication.sendEvent(widget, _resize_event(widget))
            widget.setAttribute(Qt.WA_PendingResizeEvent, False)
        QApplication.sendPostedEvents(widget, QEvent.LayoutRequest)
        lay = widget.layout()
        if lay is not None:
            lay.activate()

    def addWidget(self, widget):
        idx = super().addWidget(widget)
        self.presize(widget)
        return idx

    def setCurrentWidget(self, widget):
        self.presize(widget)
        super().setCurrentWidget(widget)

    def setCurrentIndex(self, index):
        self.presize(self.widget(index))
        super().setCurrentIndex(index)

    def fade_to(self, widget, mode="cross", ms=350, done=None):
        """widget へフェードで切り替える。done は終わったときに呼ぶ。"""
        self._finish_now()
        self._target = widget
        self._done_cb = done
        self._ms = max(1, int(ms))
        ov = self._overlay
        ov.setGeometry(self.rect())
        self.presize(widget)
        if mode == "cross":
            cur = self.currentWidget()
            ov.pixmap = cur.grab() if cur is not None else None
            self.setCurrentWidget(widget)
            self._start("out", 1.0, 0.0, self._ms)
        else:
            ov.pixmap = None
            self._start("in", 0.0, 1.0, self._ms // 2)
        ov.opacity = self._anim.startValue()
        ov.show()
        ov.raise_()

    def _start(self, phase, a, b, ms):
        self._phase = phase
        self._anim.stop()
        self._anim.setDuration(max(1, ms))
        self._anim.setStartValue(float(a))
        self._anim.setEndValue(float(b))
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.start()

    def _on_value(self, v):
        self._overlay.opacity = float(v)
        self._overlay.update()

    def _on_finished(self):
        if self._phase == "in":
            # 真っ黒。ここで切り替えて、黒を薄くする。
            self.setCurrentWidget(self._target)
            if getattr(self._target, "native_gl", False):
                self._end()         # GL の窓の上には重ねられない
                return
            self._overlay.raise_()
            self._start("out", 1.0, 0.0, self._ms - self._ms // 2)
            return
        self._end()

    def _end(self):
        self._phase = None
        self._overlay.hide()
        self._overlay.pixmap = None
        cb, self._done_cb = self._done_cb, None
        if cb is not None:
            cb()

    def _finish_now(self):
        """途中のフェードを打ち切る(次のフェードや画面の直接切り替えの前)。"""
        if self._phase is None:
            return
        self._anim.stop()
        if self._target is not None:
            self.setCurrentWidget(self._target)
        self._phase = None
        self._overlay.hide()
        self._overlay.pixmap = None
        self._done_cb = None

    def cancel(self):
        self._finish_now()


def _resize_event(widget):
    from PySide6.QtGui import QResizeEvent
    return QResizeEvent(widget.size(), widget.size())
